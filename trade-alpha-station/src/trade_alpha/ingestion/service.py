"""Fixture-friendly orchestration for policy-gated, resumable retrieval."""

from __future__ import annotations

import csv
import io
import json
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from time import sleep as system_sleep
from typing import Any, Literal

from .adapters import PageFetcher
from .fingerprints import headers_hash, redact_url, request_fingerprint, sanitize_text
from .models import BatchManifest, IngestionEvent, PageResponse, RetrievalEnvelope
from .registry import SourceRegistry
from .store import EvidenceStore

EventType = Literal["artifact.retrieved", "retrieval.failed", "schema.drift", "policy.blocked"]


def _mime(value: str | None) -> str | None:
    return value.split(";", 1)[0].strip().lower() if value else None


def _header(headers: dict[str, str], name: str) -> str | None:
    """HTTP header names are case-insensitive."""

    target = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == target), None)


def _retry_after_seconds(headers: dict[str, str]) -> float | None:
    value = _header(headers, "retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        # HTTP-date forms require a wall clock; retaining the batch is safer than
        # guessing a delay.  A scheduler can retry it on its normal cadence.
        return None


def inspect_payload(
    content: bytes, declared_mime: str | None, allowed: tuple[str, ...]
) -> tuple[str, str | None]:
    """Return ``(detected_mime, invalid_reason)`` without trusting HTTP status alone."""

    stripped = content.lstrip()
    detected = "application/octet-stream"
    invalid: str | None = None
    if stripped.startswith((b"<!doctype", b"<!DOCTYPE", b"<html", b"<HTML")):
        detected = "text/html"
    elif stripped.startswith(b"%PDF-"):
        detected = "application/pdf"
    elif stripped.startswith((b"{", b"[")):
        detected = "application/json"
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, (dict, list)):
                invalid = "json_minimum_structure"
        except (UnicodeDecodeError, json.JSONDecodeError):
            invalid = "truncated_or_invalid_json"
    elif stripped.startswith(b"<?xml"):
        detected = "application/xml"
    else:
        try:
            text = content.decode("utf-8")
            rows = list(csv.reader(io.StringIO(text)))
            if len(rows) >= 1 and len(rows[0]) >= 2:
                detected = "text/csv"
            elif text.strip().startswith("<"):
                detected = "text/html"
        except UnicodeDecodeError:
            pass
    declared = _mime(declared_mime)
    if declared not in allowed:
        invalid = invalid or "declared_mime_not_allowed"
    if detected not in allowed:
        invalid = invalid or "detected_mime_not_allowed"
    if declared and declared != detected:
        invalid = invalid or "declared_mime_mismatch"
    return detected, invalid


class RateLimiter:
    """Small deterministic sliding-window limiter with injectable time/sleep."""

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = system_sleep,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.clock = clock
        self.sleeper = sleeper
        self._times: deque[float] = deque()

    def wait(self) -> None:
        now = self.clock()
        while self._times and now - self._times[0] >= self.window_seconds:
            self._times.popleft()
        if len(self._times) >= self.max_requests:
            duration = max(0.0, self.window_seconds - (now - self._times[0]))
            self.sleeper(duration)
            now = self.clock()
            while self._times and now - self._times[0] >= self.window_seconds:
                self._times.popleft()
        self._times.append(now)


class IngestionService:
    def __init__(
        self,
        registry: SourceRegistry,
        store: EvidenceStore,
        *,
        code_sha: str = "unknown",
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = system_sleep,
    ) -> None:
        self.registry = registry
        self.store = store
        self.code_sha = code_sha
        self.clock = clock
        self.sleeper = sleeper
        self._limiters: dict[str, RateLimiter] = {}

    def _limiter(self, source_id: str) -> RateLimiter:
        if source_id not in self._limiters:
            policy = self.registry.get(source_id).rate_policy
            self._limiters[source_id] = RateLimiter(
                policy.max_requests_per_window,
                policy.window_seconds,
                clock=self.clock,
                sleeper=self.sleeper,
            )
        return self._limiters[source_id]

    def _event(self, event_type: EventType, batch: BatchManifest, **kwargs: Any) -> None:
        self.store.append_event(
            IngestionEvent(
                event_type=event_type, source_id=batch.source_id, batch_id=batch.batch_id, **kwargs
            )
        )

    def run_paginated(
        self,
        source_id: str,
        fetch_page: PageFetcher,
        *,
        connector_version: str,
        endpoint: str | None = None,
        universe_version: str = "1",
        time_partition: str | None = None,
        batch_id: str | None = None,
    ) -> BatchManifest:
        """Run or resume a page sequence without advancing the published cursor early.

        ``fetch_page`` is purposely a local callable.  Production dlt/Scrapy/
        Playwright integrations adapt to it, while tests can inject faults with
        no live network access.
        """

        record = self.registry.get(source_id)
        decision = self.registry.policy_gate(source_id)
        if batch_id:
            batch = self.store.load_batch(source_id, batch_id)
            if batch.status == "complete":
                return batch
            batch.status = "retrying"
        else:
            batch = BatchManifest(
                source_id=source_id, cursor_before=self.store.get_checkpoint(source_id)
            )
        if not decision.allowed:
            batch.status = "blocked_policy"
            batch.policy_reason = decision.reason
            batch.updated_at = datetime.now(UTC)
            self.store.save_batch(batch)
            self._event("policy.blocked", batch, detail=decision.reason)
            return batch

        batch.status = "running"
        batch.error = None
        batch.updated_at = datetime.now(UTC)
        self.store.save_batch(batch)
        cursor = batch.resume_cursor if batch.resume_cursor is not None else batch.cursor_before
        page_number = max(batch.completed_page_numbers, default=0) + 1
        while True:
            try:
                self._limiter(source_id).wait()
                response = fetch_page(cursor, page_number)
            except Exception as exc:
                reason = f"fetch_exception:{sanitize_text(str(exc))}"
                failed_response = PageResponse(url=endpoint or str(record.base_url), status_code=0)
                fingerprint = request_fingerprint(
                    source_id=source_id,
                    endpoint=endpoint or str(record.base_url),
                    params={"cursor": cursor, "page": page_number},
                    time_partition=time_partition,
                    connector_version=connector_version,
                    universe_version=universe_version,
                )
                failed_envelope = self._make_envelope(
                    batch,
                    failed_response,
                    fingerprint,
                    redact_url(failed_response.url),
                    cursor,
                    page_number,
                    connector_version,
                    datetime.now(UTC),
                ).model_copy(
                    update={"completeness_status": "incomplete", "error_code": "fetch_exception"}
                )
                self.store.append_retrieval(failed_envelope)
                batch.retrieval_ids.append(failed_envelope.retrieval_id)
                return self._fail(batch, reason)
            now = datetime.now(UTC)
            request_url = redact_url(response.url)
            fingerprint = request_fingerprint(
                source_id=source_id,
                endpoint=endpoint or response.url,
                params={"cursor": cursor, "page": page_number},
                body=response.request_body,
                time_partition=time_partition,
                connector_version=connector_version,
                universe_version=universe_version,
            )
            envelope = self._make_envelope(
                batch,
                response,
                fingerprint,
                request_url,
                cursor,
                page_number,
                connector_version,
                now,
            )
            if response.status_code == 304:
                existing = self.store.latest_url_artifact(source_id, request_url)
                if existing is None:
                    missing = envelope.model_copy(
                        update={
                            "completeness_status": "incomplete",
                            "error_code": "not_modified_without_prior_artifact",
                        }
                    )
                    self.store.append_retrieval(missing)
                    batch.retrieval_ids.append(missing.retrieval_id)
                    self.store.append_quarantine(missing, "not_modified_without_prior_artifact")
                    return self._fail(batch, "not_modified_without_prior_artifact")
                envelope = envelope.model_copy(
                    update={
                        "artifact_id": existing,
                        "content_hash": existing,
                        "completeness_status": "not_modified",
                    }
                )
                self.store.append_retrieval(envelope)
            elif response.status_code == 429 or response.status_code >= 500:
                retry_after = _retry_after_seconds(response.headers)
                if retry_after is not None:
                    self.sleeper(retry_after)
                failed_envelope = envelope.model_copy(
                    update={
                        "completeness_status": "incomplete",
                        "error_code": f"http_{response.status_code}",
                    }
                )
                self.store.append_retrieval(failed_envelope)
                batch.retrieval_ids.append(failed_envelope.retrieval_id)
                return self._fail(batch, f"retryable_http_{response.status_code}")
            elif response.status_code < 200 or response.status_code >= 300:
                failed_envelope = envelope.model_copy(
                    update={
                        "completeness_status": "incomplete",
                        "error_code": f"http_{response.status_code}",
                    }
                )
                self.store.append_retrieval(failed_envelope)
                batch.retrieval_ids.append(failed_envelope.retrieval_id)
                return self._fail(batch, f"terminal_http_{response.status_code}", terminal=True)
            else:
                detected, invalid = inspect_payload(
                    response.content,
                    _header(response.headers, "content-type"),
                    record.allowed_content_types,
                )
                artifact = self.store.put_artifact(
                    source_id=source_id,
                    content=response.content,
                    retrieved_at=now,
                    mime_type_detected=detected,
                )
                supersedes = self.store.record_url_version(
                    source_id, request_url, artifact.artifact_id
                )
                status = "invalid_payload" if invalid else "complete"
                envelope = envelope.model_copy(
                    update={
                        "artifact_id": artifact.artifact_id,
                        "content_hash": artifact.content_hash,
                        "content_length": len(response.content),
                        "mime_type_detected": detected,
                        "supersedes_artifact_id": supersedes,
                        "completeness_status": status,
                        "error_code": invalid,
                    }
                )
                self.store.append_retrieval(envelope)
                if invalid:
                    batch.retrieval_ids.append(envelope.retrieval_id)
                    self.store.append_quarantine(envelope, invalid)
                    return self._fail(batch, invalid)
                self._event(
                    "artifact.retrieved",
                    batch,
                    retrieval_id=envelope.retrieval_id,
                    artifact_id=artifact.artifact_id,
                )

            batch.retrieval_ids.append(envelope.retrieval_id)
            batch.completed_page_numbers.append(page_number)
            batch.resume_cursor = response.next_cursor
            if response.declared_total_pages:
                batch.expected_total_pages = response.declared_total_pages
            batch.updated_at = datetime.now(UTC)
            self.store.save_batch(batch)
            if response.next_cursor is None:
                if batch.expected_total_pages and page_number != batch.expected_total_pages:
                    return self._fail(batch, "declared_page_count_mismatch")
                batch.status = "complete"
                # Do not publish the last page's navigation cursor.  It would
                # make the next scheduled run start at the final page and skip
                # data.  Connectors expose a high-water mark explicitly.
                batch.cursor_after = response.commit_cursor
                batch.updated_at = datetime.now(UTC)
                self.store.save_batch(batch)
                self.store.commit_checkpoint(
                    source_id, batch.cursor_before, batch.cursor_after, batch.batch_id
                )
                return batch
            cursor = response.next_cursor
            page_number += 1

    def _make_envelope(
        self,
        batch: BatchManifest,
        response: PageResponse,
        fingerprint: str,
        url: str,
        cursor: str | None,
        page_number: int,
        connector_version: str,
        now: datetime,
    ) -> RetrievalEnvelope:
        return RetrievalEnvelope(
            source_id=batch.source_id,
            request_fingerprint=fingerprint,
            request_url_redacted=url,
            requested_at=now,
            retrieved_at=now,
            http_status=response.status_code,
            response_headers_hash=headers_hash(response.headers),
            content_length=len(response.content),
            declared_mime_type=_mime(_header(response.headers, "content-type")),
            batch_id=batch.batch_id,
            page_number=page_number,
            cursor_before=cursor,
            cursor_after=response.next_cursor,
            completeness_status="incomplete",
            code_sha=self.code_sha,
            connector_version=connector_version,
        )

    def _fail(self, batch: BatchManifest, reason: str, *, terminal: bool = False) -> BatchManifest:
        batch.status = "failed_terminal" if terminal else "incomplete"
        batch.error = sanitize_text(reason)
        batch.updated_at = datetime.now(UTC)
        self.store.save_batch(batch)
        self._event("retrieval.failed", batch, detail=batch.error)
        return batch
