"""Local, explicit-only commercial-evidence adapter for supplier quotes.

It deliberately has no mailbox, portal, network listener, or credential path.
Uploaded bytes go into M01's content-addressed evidence store and remain there.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from trade_alpha.contracts.common import VersionRef
from trade_alpha.execution.adapters.local_intake_store import LocalArtifactCaptureAdapter
from trade_alpha.execution.supplier_quotes.models import (
    SupplierQuoteIntakeReceipt,
    canonical_commercial_mime,
    commercial_input_fingerprint,
    commercial_quarantine_status,
)
from trade_alpha.execution.supplier_quotes.ports import CommercialExtractionPort
from trade_alpha.extraction.routing import route_artifact
from trade_alpha.ingestion import EvidenceStore


def _stable_id(kind: str, *parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, "/".join(("trade-alpha", "ep06", kind, *parts))))


class CommercialEvidenceAdapter:
    """M01-backed implementation of the EP-06 commercial evidence intake port."""

    def __init__(
        self,
        store: EvidenceStore,
        *,
        source_id: str = "local_supplier_quote",
        max_bytes: int = 10 * 1024 * 1024,
        allowed_mime_types: tuple[str, ...] = (
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "message/rfc822",
            "text/plain",
            "image/png",
            "image/jpeg",
        ),
        extraction_service: CommercialExtractionPort | None = None,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.capture_adapter = LocalArtifactCaptureAdapter(store, source_id=source_id)
        self.max_bytes = max_bytes
        self.allowed_mime_types = frozenset(allowed_mime_types)
        self.extraction_service = extraction_service

    def capture(
        self,
        *,
        supplier_id: str,
        supplier_rfq_package_id: str,
        content: bytes,
        filename: str,
        declared_mime: str | None,
        idempotency_key: str | None,
        received_at: datetime,
        quote_received_at: datetime | None,
        submitted_by: str,
    ) -> SupplierQuoteIntakeReceipt:
        if not content:
            raise ValueError("commercial evidence content must not be empty")
        mime = canonical_commercial_mime(filename, declared_mime)
        quarantine_status = self._quarantine_status(content, filename, mime)
        artifact = self.capture_adapter.capture(
            content, mime_type_detected=mime, captured_at=received_at
        )
        route = route_artifact(content, mime)
        parser_status: Literal["routed", "manual_review", "extracted", "needs_review"] = (
            "routed" if route.primary_parser != "none" else "manual_review"
        )
        extraction_run_refs: tuple[str, ...] = ()
        parser_versions = [
            VersionRef(name="commercial-evidence", version="1.0.0"),
            VersionRef(name=f"m02-route:{route.primary_parser}", version="1.0.0"),
        ]
        if (
            self.extraction_service is not None
            and route.primary_parser != "none"
            and quarantine_status == "clear"
        ):
            try:
                run = self.extraction_service.extract(
                    artifact.artifact_id,
                    content,
                    declared_mime=mime,
                    filename=filename,
                )
            except Exception as error:
                parser_status = "needs_review"
                quarantine_status = f"quarantined:m02_extraction_failed:{type(error).__name__}"
            else:
                extraction_run_refs = (run.extraction_run_id,)
                parser_versions.append(
                    VersionRef(name=f"m02-parser:{run.parser}", version=run.parser_version)
                )
                if run.status == "quarantined":
                    parser_status = "needs_review"
                    reason = run.diagnostics[0] if run.diagnostics else "unknown"
                    quarantine_status = f"quarantined:m02:{reason}"
                elif run.blocks:
                    parser_status = (
                        "needs_review" if run.status == "needs_review" else "extracted"
                    )
                else:
                    parser_status = "needs_review"
        fingerprint = commercial_input_fingerprint(
            supplier_id=supplier_id,
            supplier_rfq_package_id=supplier_rfq_package_id,
            content=content,
            filename=filename,
            mime_type=mime,
            idempotency_key=idempotency_key,
            quote_received_at=quote_received_at,
            submitted_by=submitted_by,
        )
        quote_id = _stable_id("quote", fingerprint)
        return SupplierQuoteIntakeReceipt(
            intake_id=_stable_id("intake", fingerprint),
            quote_id=quote_id,
            supplier_id=supplier_id,
            supplier_rfq_package_id=supplier_rfq_package_id,
            idempotency_key=idempotency_key,
            artifact_refs=(artifact.artifact_id,),
            content_sha256=f"sha256:{hashlib.sha256(content).hexdigest()}",
            filename=filename,
            mime_type=mime,
            parser_status=parser_status,
            extraction_run_refs=extraction_run_refs,
            parser_versions=tuple(parser_versions),
            received_at=received_at,
            quote_received_at=quote_received_at,
            submitted_by=submitted_by,
            quarantine_status=quarantine_status,
            input_fingerprint=fingerprint,
        )

    def _quarantine_status(self, content: bytes, filename: str, mime: str) -> str:
        return commercial_quarantine_status(
            content,
            filename,
            mime,
            max_bytes=self.max_bytes,
            allowed_mime_types=tuple(self.allowed_mime_types),
        )


# A fixture-oriented spelling helps EP-06 remain independently testable.
FixtureCommercialEvidenceAdapter = CommercialEvidenceAdapter
