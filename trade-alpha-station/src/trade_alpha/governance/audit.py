"""Append-only, hash-chained audit journal."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from .models import AuditEvent, AuditVerification, Identity, stable_hash


class AuditMutationDenied(PermissionError):
    pass


class AppendOnlyAuditLog:
    """Reference journal; production persists the same contract in an immutable table."""

    GENESIS_HASH = "sha256:genesis"

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def append(
        self,
        *,
        identity: Identity,
        action: str,
        resource: str,
        before: Any,
        after: Any,
        occurred_at: datetime,
        reason: str,
        correlation_id: str,
    ) -> AuditEvent:
        previous_hash = self._events[-1].event_hash if self._events else self.GENESIS_HASH
        before_hash = stable_hash(before)
        after_hash = stable_hash(after)
        unsigned = {
            "sequence": len(self._events),
            "actor": identity.actor_id,
            "client_identity": identity.client_identity,
            "action": action,
            "resource": resource,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "occurred_at": occurred_at.isoformat(),
            "reason": reason,
            "correlation_id": correlation_id,
            "previous_hash": previous_hash,
        }
        event = AuditEvent(
            sequence=len(self._events),
            actor=identity.actor_id,
            client_identity=identity.client_identity,
            action=action,
            resource=resource,
            before_hash=before_hash,
            after_hash=after_hash,
            occurred_at=occurred_at,
            reason=reason,
            correlation_id=correlation_id,
            previous_hash=previous_hash,
            event_hash=stable_hash(unsigned),
        )
        self._events.append(event)
        return event

    def verify(self, events: Sequence[AuditEvent] | None = None) -> AuditVerification:
        candidates = tuple(events) if events is not None else self.events
        previous_hash = self.GENESIS_HASH
        for sequence, event in enumerate(candidates):
            unsigned = {
                "sequence": event.sequence,
                "actor": event.actor,
                "client_identity": event.client_identity,
                "action": event.action,
                "resource": event.resource,
                "before_hash": event.before_hash,
                "after_hash": event.after_hash,
                "occurred_at": event.occurred_at.isoformat(),
                "reason": event.reason,
                "correlation_id": event.correlation_id,
                "previous_hash": event.previous_hash,
            }
            if (
                event.sequence != sequence
                or event.previous_hash != previous_hash
                or event.event_hash != stable_hash(unsigned)
            ):
                return AuditVerification(valid=False, first_invalid_sequence=sequence)
            previous_hash = event.event_hash
        return AuditVerification(valid=True)

    def replace(self, _sequence: int, _event: AuditEvent) -> None:
        raise AuditMutationDenied("audit events are append-only")

    def delete(self, _sequence: int) -> None:
        raise AuditMutationDenied("audit events cannot be deleted")
