"""Append-only in-memory persistence adapter for quote evidence and facts."""

from __future__ import annotations

from datetime import datetime

from trade_alpha.contracts.common import require_utc

from .models import (
    SupplierComparisonMatrix,
    SupplierQuoteDecision,
    SupplierQuoteFieldCandidate,
    SupplierQuoteIntakeReceipt,
    SupplierQuoteLineCandidate,
    SupplierQuoteSnapshot,
)


class InMemorySupplierQuoteRepository:
    def __init__(self) -> None:
        self._receipts: dict[str, SupplierQuoteIntakeReceipt] = {}
        self._receipt_by_fingerprint: dict[str, str] = {}
        self._idempotency: dict[str, str] = {}
        self._field_candidates: dict[str, SupplierQuoteFieldCandidate] = {}
        self._line_candidates: dict[str, SupplierQuoteLineCandidate] = {}
        self._decisions: list[SupplierQuoteDecision] = []
        self._snapshots: dict[str, SupplierQuoteSnapshot] = {}
        self._matrices: dict[str, SupplierComparisonMatrix] = {}

    def save_receipt(self, receipt: SupplierQuoteIntakeReceipt) -> SupplierQuoteIntakeReceipt:
        if receipt.idempotency_key:
            prior_id = self._idempotency.get(receipt.idempotency_key)
            if prior_id and prior_id != receipt.intake_id:
                prior = self._receipts[prior_id]
                if prior.input_fingerprint != receipt.input_fingerprint:
                    raise ValueError("idempotency key already used for different quote evidence")
                return prior
        prior_id = self._receipt_by_fingerprint.get(receipt.input_fingerprint)
        if prior_id:
            return self._receipts[prior_id]
        self._receipts[receipt.intake_id] = receipt
        self._receipt_by_fingerprint[receipt.input_fingerprint] = receipt.intake_id
        if receipt.idempotency_key:
            self._idempotency[receipt.idempotency_key] = receipt.intake_id
        return receipt

    def receipt(self, quote_id: str) -> SupplierQuoteIntakeReceipt | None:
        return next((item for item in self._receipts.values() if item.quote_id == quote_id), None)

    def save_field_candidate(
        self, value: SupplierQuoteFieldCandidate
    ) -> SupplierQuoteFieldCandidate:
        prior = self._field_candidates.get(value.candidate_id)
        if prior is not None and prior != value:
            raise ValueError("SupplierQuoteFieldCandidate is immutable")
        self._field_candidates.setdefault(value.candidate_id, value)
        return self._field_candidates[value.candidate_id]

    def save_line_candidate(self, value: SupplierQuoteLineCandidate) -> SupplierQuoteLineCandidate:
        prior = self._line_candidates.get(value.candidate_line_id)
        if prior is not None and prior != value:
            raise ValueError("SupplierQuoteLineCandidate is immutable")
        self._line_candidates.setdefault(value.candidate_line_id, value)
        return self._line_candidates[value.candidate_line_id]

    def field_candidates_for(self, quote_id: str) -> tuple[SupplierQuoteFieldCandidate, ...]:
        return tuple(
            sorted(
                (x for x in self._field_candidates.values() if x.quote_id == quote_id),
                key=lambda x: x.candidate_id,
            )
        )

    def line_candidates_for(self, quote_id: str) -> tuple[SupplierQuoteLineCandidate, ...]:
        return tuple(
            sorted(
                (x for x in self._line_candidates.values() if x.quote_id == quote_id),
                key=lambda x: x.candidate_line_id,
            )
        )

    def save_decision(self, decision: SupplierQuoteDecision) -> SupplierQuoteDecision:
        prior = next(
            (item for item in self._decisions if item.decision_id == decision.decision_id), None
        )
        if prior is not None:
            if prior != decision:
                raise ValueError("SupplierQuoteDecision is immutable")
            return prior
        self._decisions.append(decision)
        return decision

    def decisions_for(
        self, quote_id: str, as_of: datetime | None = None
    ) -> tuple[SupplierQuoteDecision, ...]:
        moment = require_utc(as_of) if as_of else None
        return tuple(
            sorted(
                (
                    x
                    for x in self._decisions
                    if x.quote_id == quote_id and (moment is None or x.decided_at <= moment)
                ),
                key=lambda x: (x.decided_at, x.decision_id),
            )
        )

    def save_snapshot(self, snapshot: SupplierQuoteSnapshot) -> SupplierQuoteSnapshot:
        prior = self._snapshots.get(snapshot.supplier_quote_snapshot_id)
        if prior is not None:
            if prior != snapshot:
                raise ValueError("SupplierQuoteSnapshot is immutable")
            return prior
        duplicate = next(
            (
                x
                for x in self._snapshots.values()
                if x.content_fingerprint == snapshot.content_fingerprint
            ),
            None,
        )
        if duplicate is not None:
            return duplicate
        self._snapshots[snapshot.supplier_quote_snapshot_id] = snapshot
        return snapshot

    def snapshot(self, snapshot_id: str) -> SupplierQuoteSnapshot | None:
        return self._snapshots.get(snapshot_id)

    def snapshots_for_quote(self, quote_id: str) -> tuple[SupplierQuoteSnapshot, ...]:
        return tuple(
            sorted(
                (x for x in self._snapshots.values() if x.supplier_quote_id == quote_id),
                key=lambda x: (x.version, x.confirmed_at, x.supplier_quote_snapshot_id),
            )
        )

    def save_matrix(self, matrix: SupplierComparisonMatrix) -> SupplierComparisonMatrix:
        prior = self._matrices.get(matrix.comparison_matrix_id)
        if prior is not None:
            if prior != matrix:
                raise ValueError("SupplierComparisonMatrix is immutable")
            return prior
        self._matrices[matrix.comparison_matrix_id] = matrix
        return matrix

    def matrix(self, matrix_id: str) -> SupplierComparisonMatrix | None:
        return self._matrices.get(matrix_id)
