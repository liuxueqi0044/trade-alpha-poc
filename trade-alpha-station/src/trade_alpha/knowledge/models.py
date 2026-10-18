"""Immutable records used by the M03 knowledge layer.

Records are append-only: an update is represented by closing a system-time
interval and inserting another record.  This is intentionally small enough for
the PoC while preserving the invariants required by a database implementation.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_alpha.contracts.common import Confidence, require_utc


class EntityKind(StrEnum):
    ORGANIZATION = "organization"
    PERSON = "person"
    FACILITY = "facility"
    PRODUCT_CLASS = "product_class"
    PRODUCT = "product"
    PLACE = "place"
    DOCUMENT = "document"
    EVENT = "event"
    TRADE_FLOW = "trade_flow"
    EXPERIMENT = "experiment"


class ReviewStatus(StrEnum):
    ACCEPTED = "accepted"
    PENDING = "pending"
    REJECTED = "rejected"


class LinkState(StrEnum):
    AUTO_LINKED = "auto_linked"
    REVIEW = "review"
    SEPARATE = "separate"
    REVOKED = "revoked"


class VersionedModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    system_from: datetime
    system_to: datetime | None = None

    @field_validator("system_from", "system_to")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def ordered_system_interval(self) -> VersionedModel:
        if self.system_to is not None and self.system_to <= self.system_from:
            raise ValueError("system_to must be after system_from")
        return self


class Claim(VersionedModel):
    """A source assertion; conflicting claims are deliberately retained."""

    claim_id: str
    subject_ref: str
    predicate: str
    object_value: Any | None = None
    object_ref: str | None = None
    source_id: str
    artifact_id: str | None = None
    evidence_block_ids: tuple[str, ...] = ()
    published_at: datetime | None = None
    observed_at: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    confidence: Confidence
    status: ReviewStatus = ReviewStatus.PENDING
    decision_basis: Literal["human", "rule", "model"]
    schema_version: str = "claim/1.0.0"

    @field_validator("published_at", "observed_at", "valid_from", "valid_to")
    @classmethod
    def utc_claim_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def claim_integrity(self) -> Claim:
        if self.object_value is None and self.object_ref is None:
            raise ValueError("claim requires object_value or object_ref")
        if (
            self.valid_to is not None
            and self.valid_from is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("valid_to must be after valid_from")
        # Non-human assertions must identify immutable evidence. Human-owned
        # notes are allowed to exist without an upstream artifact.
        if self.decision_basis != "human" and not self.artifact_id:
            raise ValueError("non-human claim requires artifact_id")
        if self.artifact_id is not None and not self.evidence_block_ids:
            raise ValueError("artifact-backed claim requires evidence_block_ids")
        return self


class Entity(VersionedModel):
    entity_id: str
    entity_type: EntityKind
    canonical_name: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_record_ids: tuple[str, ...] = ()
    resolution_version: str = "entity-resolution/1.0.0"

    @model_validator(mode="after")
    def product_class_is_versioned(self) -> Entity:
        if self.entity_type is EntityKind.PRODUCT_CLASS and (
            not self.attributes.get("hs_version") or not self.attributes.get("hs_code")
        ):
            raise ValueError("ProductClass requires hs_version and hs_code")
        return self

    @classmethod
    def stable_id(cls, entity_type: EntityKind, canonical_key: str) -> str:
        """Return a deterministic opaque identifier (including HS version in key)."""
        return str(uuid5(NAMESPACE_URL, f"trade-alpha/{entity_type}/{canonical_key}"))


class SourceRecord(VersionedModel):
    """Immutable source identity retained independently from resolution output."""

    source_record_id: str
    source_id: str
    original_name: str
    payload_ref: str
    entity_type: EntityKind
    language: str | None = None


class EntityAlias(VersionedModel):
    alias_id: str
    entity_id: str
    alias: str
    normalized_alias: str
    language: str | None = None
    transliteration: str | None = None
    source_record_id: str
    source_claim_id: str | None = None


class EntityLink(VersionedModel):
    """A reversible decision from a source record to a stable entity."""

    link_id: str
    source_record_id: str
    entity_id: str
    probability: Confidence
    state: LinkState
    matcher_version: str
    decision_reason: str
    evidence_claim_ids: tuple[str, ...] = ()
    replaces_link_id: str | None = None

    @classmethod
    def stable_id(
        cls,
        source_record_id: str,
        entity_id: str,
        matcher_version: str,
        resolution_version: str = "entity-resolution/1.0.0",
    ) -> str:
        """Deterministic decision ID, stable across an identical rerun."""
        return deterministic_id(
            "entity-link", source_record_id, entity_id, matcher_version, resolution_version
        )


class Relationship(VersionedModel):
    relationship_id: str
    subject_entity_id: str
    relationship_type: str
    object_entity_id: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source_claim_ids: tuple[str, ...]
    confidence: Confidence
    review_status: ReviewStatus

    @field_validator("valid_from", "valid_to")
    @classmethod
    def utc_valid_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def relationship_integrity(self) -> Relationship:
        if (
            self.valid_to is not None
            and self.valid_from is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("valid_to must be after valid_from")
        if self.review_status is ReviewStatus.ACCEPTED and not self.source_claim_ids:
            raise ValueError("accepted relationship requires source_claim_ids")
        return self


class Event(VersionedModel):
    event_id: str
    event_type: str
    participant_entity_ids: tuple[str, ...]
    place_entity_id: str | None = None
    product_entity_ids: tuple[str, ...] = ()
    occurred_from: datetime
    occurred_to: datetime | None = None
    severity: int | None = Field(default=None, ge=0, le=5)
    source_claim_ids: tuple[str, ...]

    @field_validator("occurred_from", "occurred_to")
    @classmethod
    def utc_event_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)


class ReconciliationImpact(BaseModel):
    """Deterministic invalidation work emitted after a link revocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    revoked_link_id: str
    source_record_id: str
    affected_entity_id: str
    affected_relationship_ids: tuple[str, ...]
    downstream_invalidation_keys: tuple[str, ...]
    reconciliation_version: str = "reconciliation/1.0.0"


def deterministic_id(kind: str, *parts: str) -> str:
    """Stable UUID5 IDs for non-entity records, independent of rerun order."""
    return str(uuid5(NAMESPACE_URL, "/".join(("trade-alpha", kind, *parts))))
