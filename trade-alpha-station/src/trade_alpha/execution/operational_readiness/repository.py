"""Append-only in-memory reference repository for EP-09."""

from __future__ import annotations

from datetime import datetime
from threading import RLock

from .models import (
    ApprovalDecision,
    ApprovalRequest,
    OperationalReadinessSnapshot,
    ReadinessEvaluationSnapshot,
)


class InMemoryOperationalReadinessRepository:
    def __init__(self) -> None:
        self._snapshots: dict[str, OperationalReadinessSnapshot] = {}
        self._evaluations: dict[str, ReadinessEvaluationSnapshot] = {}
        self._requests: dict[str, ApprovalRequest] = {}
        self._decisions: dict[str, list[ApprovalDecision]] = {}
        self._idempotency: dict[str, tuple[str, object]] = {}
        self._lock = RLock()

    def save_snapshot(self, value: OperationalReadinessSnapshot) -> OperationalReadinessSnapshot:
        return self._save_immutable(self._snapshots, value.readiness_snapshot_id, value)

    def snapshot(self, value_id: str) -> OperationalReadinessSnapshot | None:
        return self._snapshots.get(value_id)

    def save_evaluation(self, value: ReadinessEvaluationSnapshot) -> ReadinessEvaluationSnapshot:
        return self._save_immutable(self._evaluations, value.readiness_evaluation_id, value)

    def evaluation(self, value_id: str) -> ReadinessEvaluationSnapshot | None:
        return self._evaluations.get(value_id)

    def is_current_evaluation_as_of(self, value_id: str, as_of: datetime) -> bool:
        value = self._evaluations.get(value_id)
        if value is None:
            return False
        candidates = tuple(
            item
            for item in self._evaluations.values()
            if item.resource_id == value.resource_id
            and item.capability is value.capability
            and item.as_of <= as_of
        )
        tip = max(
            candidates,
            key=lambda item: (item.as_of, item.readiness_evaluation_id),
            default=None,
        )
        return tip is not None and tip.readiness_evaluation_id == value_id

    def save_request(self, value: ApprovalRequest) -> ApprovalRequest:
        return self._save_immutable(self._requests, value.approval_request_id, value)

    def request(self, value_id: str) -> ApprovalRequest | None:
        return self._requests.get(value_id)

    def replace_request(self, value: ApprovalRequest) -> ApprovalRequest:
        if value.approval_request_id not in self._requests:
            raise KeyError("unknown approval request")
        self._requests[value.approval_request_id] = value
        return value

    def add_decision(self, value: ApprovalDecision) -> ApprovalDecision:
        values = self._decisions.setdefault(value.approval_request_id, [])
        if any(item.decision_id == value.decision_id for item in values):
            existing = next(item for item in values if item.decision_id == value.decision_id)
            if existing != value:
                raise ValueError("decision id already exists with different content")
            return existing
        values.append(value)
        return value

    def commit_decision(
        self,
        *,
        expected_status: object,
        decision: ApprovalDecision,
        updated: ApprovalRequest,
    ) -> ApprovalRequest:
        """Atomically compare request state and append one stage decision."""
        with self._lock:
            current = self._requests.get(updated.approval_request_id)
            if current is None or current.status is not expected_status:
                raise ValueError("approval request changed concurrently")
            values = self._decisions.setdefault(updated.approval_request_id, [])
            if any(item.stage is decision.stage for item in values):
                raise ValueError("approval stage was already decided concurrently")
            values.append(decision)
            self._requests[updated.approval_request_id] = updated
            return updated

    def decisions(self, request_id: str) -> tuple[ApprovalDecision, ...]:
        return tuple(self._decisions.get(request_id, ()))

    def idempotent(self, key: str, fingerprint: str) -> object | None:
        existing = self._idempotency.get(key)
        if existing is None:
            return None
        old_fingerprint, value = existing
        if old_fingerprint != fingerprint:
            raise ValueError("idempotency key was reused with different payload")
        return value

    def save_idempotent(self, key: str, fingerprint: str, value: object) -> None:
        existing = self._idempotency.get(key)
        if existing is not None and existing != (fingerprint, value):
            raise ValueError("idempotency key was reused with different payload")
        self._idempotency[key] = (fingerprint, value)

    @staticmethod
    def _save_immutable[T](values: dict[str, T], value_id: str, value: T) -> T:
        existing = values.get(value_id)
        if existing is not None and existing != value:
            raise ValueError("immutable record cannot be overwritten")
        values[value_id] = value
        return value
