"""Append-only in-memory repository used by the RFQ PoC service."""

from __future__ import annotations

from collections.abc import Iterable

from trade_alpha.extraction import ExtractionRun

from .models import IntakeQuarantineRecord, IntakeReceipt, Rfq


class RfqRepository:
    """A deterministic repository boundary with idempotency indexes.

    A production adapter may persist these objects, but the service only
    depends on this small API.  Existing objects are returned rather than
    replaced, preserving the no-revision-on-retry rule.
    """

    def __init__(self) -> None:
        self._receipts: dict[str, IntakeReceipt] = {}
        self._receipt_by_fingerprint: dict[str, str] = {}
        self._fingerprint_by_identity: dict[str, str] = {}
        self._rfqs: dict[str, Rfq] = {}
        self._runs: dict[tuple[str, str], ExtractionRun] = {}
        self._candidate_values: dict[str, tuple[object, ...]] = {}
        self._quarantine: dict[str, IntakeQuarantineRecord] = {}

    def receipt_for_fingerprint(self, value: str) -> IntakeReceipt | None:
        receipt_id = self._receipt_by_fingerprint.get(value)
        return self._receipts.get(receipt_id) if receipt_id else None

    def save_receipt(self, receipt: IntakeReceipt) -> IntakeReceipt:
        return self.save_receipt_with_identity(receipt)

    def receipt_for_identity(self, identity: str) -> IntakeReceipt | None:
        fingerprint = self._fingerprint_by_identity.get(identity)
        return self.receipt_for_fingerprint(fingerprint) if fingerprint else None

    def save_receipt_with_identity(
        self, receipt: IntakeReceipt, *, identity: str | None = None
    ) -> IntakeReceipt:
        if identity is not None:
            prior_fingerprint = self._fingerprint_by_identity.get(identity)
            if prior_fingerprint is not None and prior_fingerprint != receipt.intake_fingerprint:
                raise ValueError("identity already used for a different intake payload")
        prior = self.receipt_for_fingerprint(receipt.intake_fingerprint)
        if prior is not None:
            if identity is not None:
                self._fingerprint_by_identity[identity] = prior.intake_fingerprint
            return prior
        self._receipts[receipt.receipt_id] = receipt
        self._receipt_by_fingerprint[receipt.intake_fingerprint] = receipt.receipt_id
        if identity is not None:
            self._fingerprint_by_identity[identity] = receipt.intake_fingerprint
        return receipt

    def get_receipt(self, receipt_id: str) -> IntakeReceipt | None:
        return self._receipts.get(receipt_id)

    def save_rfq(self, rfq: Rfq) -> Rfq:
        prior = self._rfqs.get(rfq.rfq_id)
        if prior is not None:
            return prior
        self._rfqs[rfq.rfq_id] = rfq
        return rfq

    def get_rfq(self, rfq_id: str) -> Rfq | None:
        return self._rfqs.get(rfq_id)

    def all_receipts(self) -> tuple[IntakeReceipt, ...]:
        return tuple(self._receipts.values())

    def all_rfqs(self) -> tuple[Rfq, ...]:
        return tuple(self._rfqs.values())

    def extraction_for(self, artifact_id: str, extraction_fingerprint: str) -> ExtractionRun | None:
        return self._runs.get((artifact_id, extraction_fingerprint))

    def save_extraction(
        self, artifact_id: str, extraction_fingerprint: str, run: ExtractionRun
    ) -> ExtractionRun:
        key = (artifact_id, extraction_fingerprint)
        prior = self._runs.get(key)
        if prior is not None:
            return prior
        self._runs[key] = run
        return run

    def runs_for(self, artifact_id: str) -> tuple[ExtractionRun, ...]:
        return tuple(
            run for (key_artifact, _), run in self._runs.items() if key_artifact == artifact_id
        )

    def save_candidate_projection(
        self, extraction_fingerprint: str, values: Iterable[object]
    ) -> None:
        self._candidate_values.setdefault(extraction_fingerprint, tuple(values))

    def candidate_projection_for(self, extraction_fingerprint: str) -> tuple[object, ...] | None:
        return self._candidate_values.get(extraction_fingerprint)

    def save_quarantine(self, record: IntakeQuarantineRecord) -> IntakeQuarantineRecord:
        return self._quarantine.setdefault(record.quarantine_id, record)

    def quarantine_for(self, artifact_id: str) -> tuple[IntakeQuarantineRecord, ...]:
        return tuple(
            record for record in self._quarantine.values() if record.artifact_id == artifact_id
        )

    @property
    def quarantine(self) -> tuple[IntakeQuarantineRecord, ...]:
        return tuple(self._quarantine.values())
