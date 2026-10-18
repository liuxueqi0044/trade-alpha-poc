"""Retention, legal-hold, and multi-copy deletion workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from .audit import AppendOnlyAuditLog
from .models import Identity, Role, Tombstone, stable_hash

StorageLayer = Literal["active", "index", "cache", "backup"]
ALL_LAYERS: tuple[StorageLayer, ...] = ("active", "index", "cache", "backup")


class RetentionBlocked(PermissionError):
    pass


class RetentionStore:
    """Four explicit copies make deletion coverage testable rather than implied."""

    def __init__(self) -> None:
        self._layers: dict[StorageLayer, dict[str, Any]] = {
            layer: {} for layer in ALL_LAYERS
        }
        self._holds: set[str] = set()

    def seed(self, subject_id: str, layer: StorageLayer, value: Any) -> None:
        self._layers[layer][subject_id] = value

    def set_legal_hold(self, subject_id: str) -> None:
        self._holds.add(subject_id)

    def release_legal_hold(self, subject_id: str) -> None:
        self._holds.discard(subject_id)

    def has_legal_hold(self, subject_id: str) -> bool:
        return subject_id in self._holds

    def present_layers(self, subject_id: str) -> tuple[StorageLayer, ...]:
        return tuple(layer for layer in ALL_LAYERS if subject_id in self._layers[layer])

    def purge_layer(self, subject_id: str, layer: StorageLayer) -> None:
        self._layers[layer].pop(subject_id, None)


class RetentionManager:
    def __init__(self, store: RetentionStore, audit: AppendOnlyAuditLog) -> None:
        self._store = store
        self._audit = audit
        self._tombstones: list[Tombstone] = []

    @property
    def tombstones(self) -> tuple[Tombstone, ...]:
        return tuple(self._tombstones)

    def delete_subject(
        self,
        *,
        subject_id: str,
        identity: Identity,
        at: datetime,
        reason: str,
        correlation_id: str,
        derivation_rebuild_required: bool,
    ) -> Tombstone:
        subject_hash = stable_hash(subject_id)
        existing = next(
            (item for item in self._tombstones if item.subject_hash == subject_hash), None
        )
        if existing:
            return existing
        resource = f"subject:{subject_hash}"
        before = {"present_layers": self._store.present_layers(subject_id)}
        if Role.ADMIN not in identity.roles:
            self._audit.append(
                identity=identity,
                action="retention.deletion_blocked",
                resource=resource,
                before=before,
                after={"authorization": "denied"},
                occurred_at=at,
                reason=reason,
                correlation_id=correlation_id,
            )
            raise RetentionBlocked("deletion requires admin role")
        if self._store.has_legal_hold(subject_id):
            self._audit.append(
                identity=identity,
                action="retention.deletion_blocked",
                resource=resource,
                before=before,
                after={"legal_hold": True},
                occurred_at=at,
                reason=reason,
                correlation_id=correlation_id,
            )
            raise RetentionBlocked("deletion blocked by legal hold")
        for layer in ALL_LAYERS:
            self._store.purge_layer(subject_id, layer)
        tombstone = Tombstone(
            subject_hash=subject_hash,
            deleted_at=at,
            layers_processed=ALL_LAYERS,
            backup_disposition="purged_from_scheduled_backup_workflow",
            derivation_rebuild_required=derivation_rebuild_required,
            correlation_id=correlation_id,
        )
        self._tombstones.append(tombstone)
        self._audit.append(
            identity=identity,
            action="retention.subject_deleted",
            resource=resource,
            before=before,
            after=tombstone,
            occurred_at=at,
            reason=reason,
            correlation_id=correlation_id,
        )
        return tombstone
