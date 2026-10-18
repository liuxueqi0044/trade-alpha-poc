"""Versioned extraction orchestration with explicit trust and fallback boundaries."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from .backends import ParserBackend, default_backends
from .claims import parse_llm_claim, rule_extract
from .models import (
    CandidateClaim,
    DocumentBlock,
    DocumentFormat,
    ExtractionRun,
    ParsedBlock,
    QuarantineRecord,
    ReviewDecision,
    ReviewProjection,
    RunStatus,
    sha256_bytes,
    sha256_text,
    stable_id,
)
from .routing import parser_is_compatible, route_artifact

LLMExtractor = Callable[[tuple[DocumentBlock, ...]], object]


class ExtractionStore:
    """Append-only in-memory PoC store; persistent adapters can mirror this API."""

    def __init__(self) -> None:
        self._runs: dict[str, ExtractionRun] = {}
        self._raw_hashes: dict[str, str] = {}
        self._quarantine: list[QuarantineRecord] = []
        self._reviews: list[ReviewDecision] = []
        self._review_projection: dict[str, ReviewProjection] = {}

    def add_run(self, run: ExtractionRun) -> ExtractionRun:
        prior_hash = self._raw_hashes.get(run.artifact_id)
        if prior_hash is not None and prior_hash != run.raw_sha256:
            raise ValueError("artifact raw hash changed; M01 evidence is immutable")
        if run.extraction_run_id in self._runs:
            raise ValueError(f"duplicate extraction_run_id: {run.extraction_run_id}")
        self._raw_hashes[run.artifact_id] = run.raw_sha256
        self._runs[run.extraction_run_id] = run
        return run

    def add_quarantine(self, record: QuarantineRecord) -> None:
        self._quarantine.append(record)

    def runs_for(self, artifact_id: str) -> tuple[ExtractionRun, ...]:
        return tuple(run for run in self._runs.values() if run.artifact_id == artifact_id)

    @property
    def quarantine(self) -> tuple[QuarantineRecord, ...]:
        return tuple(self._quarantine)

    def add_review(self, decision: ReviewDecision) -> ReviewProjection:
        self._reviews.append(decision)
        projection = ReviewProjection(
            claim_id=decision.claim_id,
            status=decision.decision,
            decision_id=decision.decision_id,
            decided_at=decision.decided_at,
        )
        self._review_projection[decision.claim_id] = projection
        return projection

    @property
    def reviews(self) -> tuple[ReviewDecision, ...]:
        return tuple(self._reviews)

    def current_review(self, claim_id: str) -> ReviewProjection | None:
        return self._review_projection.get(claim_id)


def _materialize_blocks(
    *,
    parsed: Sequence[ParsedBlock],
    artifact_id: str,
    extraction_run_id: str,
    document_format: DocumentFormat,
    parser: str,
    parser_version: str,
    model_id: str | None,
    ocr_low_confidence_threshold: float,
) -> tuple[DocumentBlock, ...]:
    identifiers = {
        item.local_id: stable_id(
            "block",
            artifact_id,
            extraction_run_id,
            item.local_id,
            sha256_text(item.text),
        )
        for item in parsed
    }
    result: list[DocumentBlock] = []
    for reading_order, item in enumerate(parsed):
        is_ocr = item.block_type == "ocr_token"
        result.append(
            DocumentBlock(
                block_id=identifiers[item.local_id],
                artifact_id=artifact_id,
                extraction_run_id=extraction_run_id,
                document_format=document_format,
                block_type=item.block_type,
                page_number=item.page_number,
                parent_block_id=(
                    identifiers.get(item.parent_local_id) if item.parent_local_id else None
                ),
                reading_order=reading_order,
                bbox=item.bbox,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                text=item.text,
                language=item.language,
                confidence=item.confidence,
                parser=parser,
                parser_version=parser_version,
                model_id=model_id,
                content_hash=sha256_text(item.text),
                locator_quality=item.locator_quality,
                low_confidence=is_ocr and item.confidence < ocr_low_confidence_threshold,
                table_row=item.table_row,
                table_col=item.table_col,
            )
        )
    return tuple(result)


class ExtractionService:
    def __init__(
        self,
        *,
        store: ExtractionStore | None = None,
        backends: Mapping[str, ParserBackend] | None = None,
        ocr_low_confidence_threshold: float = 0.75,
    ) -> None:
        if not 0 <= ocr_low_confidence_threshold <= 1:
            raise ValueError("ocr_low_confidence_threshold must be between zero and one")
        self.store = store or ExtractionStore()
        self.backends = dict(backends or default_backends())
        self.ocr_low_confidence_threshold = ocr_low_confidence_threshold

    def _quarantine_run(
        self,
        *,
        artifact_id: str,
        raw_sha256: str,
        document_format: DocumentFormat,
        reason: str,
        retryable: bool,
    ) -> ExtractionRun:
        self.store.add_quarantine(
            QuarantineRecord(
                artifact_id=artifact_id,
                raw_sha256=raw_sha256,
                reason=reason,
                retryable=retryable,
            )
        )
        return self.store.add_run(
            ExtractionRun(
                artifact_id=artifact_id,
                raw_sha256=raw_sha256,
                document_format=document_format,
                parser="none",
                parser_version="none",
                status="quarantined",
                diagnostics=(reason,),
            )
        )

    def _parse(
        self,
        *,
        data: bytes,
        document_format: DocumentFormat,
        candidates: Sequence[str],
        diagnostics: list[str],
    ) -> tuple[ParserBackend | None, tuple[ParsedBlock, ...]]:
        for name in candidates:
            backend = self.backends.get(name)
            if backend is None or not backend.available():
                diagnostics.append(f"backend_unavailable:{name}")
                continue
            try:
                parsed = backend.parse(data, document_format)
            except Exception as exc:
                diagnostics.append(f"backend_failed:{name}:{type(exc).__name__}")
                continue
            if not parsed:
                diagnostics.append(f"backend_empty:{name}")
                continue
            return backend, parsed
        return None, ()

    def extract(
        self,
        artifact_id: str,
        data: bytes,
        *,
        declared_mime: str | None = None,
        filename: str | None = None,
        parser_profile: str | None = None,
        llm_extractor: LLMExtractor | None = None,
        model_id: str | None = None,
        prompt_version: str = "none",
        schema_version: str = "claim/1.0.0",
    ) -> ExtractionRun:
        del filename
        raw_sha256 = sha256_bytes(data)
        decision = route_artifact(data, declared_mime)
        if decision.document_format is DocumentFormat.UNKNOWN:
            return self._quarantine_run(
                artifact_id=artifact_id,
                raw_sha256=raw_sha256,
                document_format=decision.document_format,
                reason="unsupported_or_conflicting_format",
                retryable=False,
            )
        if parser_profile and not parser_is_compatible(decision, parser_profile):
            return self._quarantine_run(
                artifact_id=artifact_id,
                raw_sha256=raw_sha256,
                document_format=decision.document_format,
                reason=f"incompatible_parser_profile:{parser_profile}",
                retryable=False,
            )

        diagnostics: list[str] = []
        if decision.mime_conflict:
            diagnostics.append("declared_mime_conflict_signature_won")
        candidates = (parser_profile,) if parser_profile else decision.allowed_parsers
        backend, parsed = self._parse(
            data=data,
            document_format=decision.document_format,
            candidates=candidates,
            diagnostics=diagnostics,
        )
        if backend is None:
            return self._quarantine_run(
                artifact_id=artifact_id,
                raw_sha256=raw_sha256,
                document_format=decision.document_format,
                reason="no_parser_produced_evidence",
                retryable=True,
            )

        extraction_run_id = str(uuid4())
        blocks = _materialize_blocks(
            parsed=parsed,
            artifact_id=artifact_id,
            extraction_run_id=extraction_run_id,
            document_format=decision.document_format,
            parser=backend.name,
            parser_version=backend.version,
            model_id=model_id,
            ocr_low_confidence_threshold=self.ocr_low_confidence_threshold,
        )
        claims: list[CandidateClaim] = list(rule_extract(blocks))
        if llm_extractor is None:
            diagnostics.append("llm_unavailable_deterministic_only")
        else:
            try:
                payload = llm_extractor(blocks)
                items = payload if isinstance(payload, list) else [payload]
                for item in items:
                    gated = parse_llm_claim(
                        item,
                        artifact_id=artifact_id,
                        blocks=blocks,
                        extractor_version=model_id or "llm/unknown",
                        prompt_version=prompt_version,
                        schema_version=schema_version,
                    )
                    if gated.accepted and gated.claim is not None:
                        claims.append(gated.claim)
                    else:
                        diagnostics.append(gated.reason)
            except Exception as exc:
                diagnostics.append(f"llm_unavailable:{type(exc).__name__}")

        low_confidence = any(block.low_confidence for block in blocks)
        if low_confidence:
            diagnostics.append("ocr_low_confidence")
        status: RunStatus
        if low_confidence:
            status = "needs_review"
        elif any(value.startswith("llm_unavailable") for value in diagnostics):
            status = "partially_enriched"
        else:
            status = "completed"
        return self.store.add_run(
            ExtractionRun(
                extraction_run_id=extraction_run_id,
                artifact_id=artifact_id,
                raw_sha256=raw_sha256,
                document_format=decision.document_format,
                parser=backend.name,
                parser_version=backend.version,
                model_id=model_id,
                prompt_version=prompt_version,
                schema_version=schema_version,
                status=status,
                blocks=blocks,
                claims=tuple(claims),
                diagnostics=tuple(diagnostics),
                dependency_versions=((backend.name, backend.version),),
            )
        )

    def review(
        self,
        claim: CandidateClaim,
        *,
        reviewer_id: str,
        decision: str,
        reason: str,
        decided_at: datetime | None = None,
    ) -> tuple[ReviewProjection, ReviewDecision]:
        if decision not in {"accepted", "rejected"}:
            raise ValueError("decision must be accepted or rejected")
        if not reviewer_id.strip() or not reason.strip():
            raise ValueError("reviewer_id and reason are required")
        values: dict[str, Any] = {
            "claim_id": claim.claim_id,
            "decision": decision,
            "reviewer_id": reviewer_id,
            "reason": reason,
            "prior_status": claim.status,
        }
        if decided_at is not None:
            values["decided_at"] = decided_at
        audit = ReviewDecision.model_validate(values)
        projection = self.store.add_review(audit)
        return projection, audit

