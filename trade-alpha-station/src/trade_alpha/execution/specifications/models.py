"""Immutable, versioned product-specification contracts."""

from __future__ import annotations

# ruff: noqa: E501, I001

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_alpha.contracts.common import VersionRef, require_utc
from trade_alpha.execution.contracts.models import require_decimal


class FrozenSpecModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ValueKind(StrEnum):
    TEXT = "text"
    ENUM = "enum"
    BOOLEAN = "boolean"
    QUANTITY = "quantity"
    RANGE = "range"
    DATE = "date"
    REFERENCE = "reference"
    DOCUMENT = "document"


class FieldState(StrEnum):
    MISSING = "missing"
    UNKNOWN = "unknown"
    CANDIDATE = "candidate"
    CONFLICT = "conflict"
    CONFIRMED = "confirmed"
    OPEN_TO_SUPPLIER_PROPOSAL = "open_to_supplier_proposal"
    NOT_APPLICABLE = "not_applicable"
    REJECTED = "rejected"


class SpecState(StrEnum):
    DRAFT = "draft"
    REVIEW_REQUIRED = "review_required"
    CONFIRMED = "confirmed"
    FROZEN_FOR_SUPPLIER_RFQ = "frozen_for_supplier_rfq"
    BLOCKED = "blocked"
    SUPERSEDED = "superseded"


class GateStatus(StrEnum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"


class DecisionAction(StrEnum):
    SELECT_CLAIM = "select_claim"
    MERGE_EQUIVALENT = "merge_equivalent"
    OPEN_TO_SUPPLIER_PROPOSAL = "open_to_supplier_proposal"
    NOT_APPLICABLE = "not_applicable"
    REJECT = "reject"
    REQUEST_CLARIFICATION = "request_clarification"


class FieldDefinition(FrozenSpecModel):
    key: str = Field(min_length=1)
    value_kind: ValueKind
    canonical_unit: str | None = None
    accepted_units: tuple[str, ...] = ()
    cardinality: Literal["one", "many"] = "one"
    criticality: Literal["normal", "important", "safety_scope"] = "normal"
    required_at: tuple[str, ...] = ()
    requirement_mode: Literal["buyer_required", "supplier_proposable", "conditional"] = (
        "buyer_required"
    )
    allow_not_applicable: bool = False
    normalizer: VersionRef = Field(
        default_factory=lambda: VersionRef(name="spec-normalizer", version="1.0.0")
    )
    conflict_policy: Literal["strict", "tolerance"] = "strict"
    equivalence_tolerance: Decimal = Field(default=Decimal("0"), ge=0)

    @field_validator("equivalence_tolerance", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return require_decimal(value)

    @model_validator(mode="after")
    def quantity_has_unit(self) -> FieldDefinition:
        if self.value_kind in {ValueKind.QUANTITY, ValueKind.RANGE} and not self.canonical_unit:
            raise ValueError("quantity and range fields require a canonical_unit")
        if self.conflict_policy == "strict" and self.equivalence_tolerance != Decimal("0"):
            raise ValueError("strict conflict policy requires zero equivalence_tolerance")
        return self


class ProductFamilyProfile(FrozenSpecModel):
    profile_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    commercial_validation_ref: str | None = None
    allowed_scope: tuple[str, ...] = ()
    hard_exclusions: tuple[str, ...] = ()
    field_definitions: tuple[FieldDefinition, ...] = Field(min_length=1)
    normalizer_versions: tuple[VersionRef, ...] = ()
    identity_alternatives: tuple[tuple[str, ...], ...] = ()
    quantity_alternatives: tuple[tuple[str, ...], ...] = ()
    schema_version: str = "product-family-profile/1.0.0"

    @model_validator(mode="after")
    def unique_field_keys(self) -> ProductFamilyProfile:
        keys = [field.key for field in self.field_definitions]
        if len(keys) != len(set(keys)):
            raise ValueError("field definitions must have unique keys")
        for field in self.field_definitions:
            if field.accepted_units and field.canonical_unit not in field.accepted_units:
                raise ValueError("canonical_unit must be included in accepted_units")
        for alternatives in (self.identity_alternatives, self.quantity_alternatives):
            for alternative in alternatives:
                if not alternative:
                    raise ValueError("profile alternatives must not be empty")
                if not set(alternative).issubset(set(keys)):
                    raise ValueError("profile alternative references an unknown field")
        return self

    @property
    def ref(self) -> VersionRef:
        return VersionRef(name=self.profile_id, version=self.profile_version)

    def field(self, key: str) -> FieldDefinition | None:
        return next((item for item in self.field_definitions if item.key == key), None)


class ProductSpec(FrozenSpecModel):
    spec_id: str = Field(min_length=1)
    rfq_id: str | None = None
    rfq_item_id: str | None = None
    product_family_ref: VersionRef
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

class SpecFieldProjection(FrozenSpecModel):
    field_key: str = Field(min_length=1)
    state: FieldState
    raw_value: Any | None = None
    typed_value: Any | None = None
    canonical_value: Any | None = None
    display_value: str | None = None
    source_claim_ids: tuple[str, ...] = ()
    selected_claim_id: str | None = None
    decision_id: str | None = None
    artifact_ids: tuple[str, ...] = ()
    evidence_block_ids: tuple[str, ...] = ()
    normalizer_version: VersionRef


class SpecConflict(FrozenSpecModel):
    conflict_id: str = Field(min_length=1)
    spec_id: str = Field(min_length=1)
    field_key: str = Field(min_length=1)
    claim_ids: tuple[str, ...] = Field(min_length=2)
    equivalence_groups: tuple[tuple[str, ...], ...] = Field(min_length=2)
    severity: Literal["normal", "important", "safety_scope"]
    reason: str = Field(min_length=1)
    state: Literal["open", "resolved"] = "open"
    resolution_decision_id: str | None = None


class SpecFieldDecision(FrozenSpecModel):
    decision_id: str = Field(min_length=1)
    spec_id: str = Field(min_length=1)
    field_key: str = Field(min_length=1)
    action: DecisionAction
    actor_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    decided_at: datetime
    input_claim_ids: tuple[str, ...] = ()
    selected_claim_id: str | None = None
    replaces_decision_id: str | None = None
    ui_version: VersionRef = Field(
        default_factory=lambda: VersionRef(name="spec-ui", version="1.0.0")
    )

    @field_validator("decided_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def action_inputs_are_explicit(self) -> SpecFieldDecision:
        if self.action is DecisionAction.SELECT_CLAIM and not self.selected_claim_id:
            raise ValueError("select_claim requires selected_claim_id")
        if self.action is DecisionAction.MERGE_EQUIVALENT and len(self.input_claim_ids) < 2:
            raise ValueError("merge_equivalent requires at least two input_claim_ids")
        if self.action is DecisionAction.REJECT and not self.input_claim_ids:
            raise ValueError("reject requires input_claim_ids")
        no_selection = {
            DecisionAction.MERGE_EQUIVALENT,
            DecisionAction.OPEN_TO_SUPPLIER_PROPOSAL,
            DecisionAction.NOT_APPLICABLE,
            DecisionAction.REJECT,
            DecisionAction.REQUEST_CLARIFICATION,
        }
        if self.action in no_selection and self.selected_claim_id:
            raise ValueError(f"{self.action.value} must not carry selected_claim_id")
        return self


class ReadinessResult(FrozenSpecModel):
    gate: str = "supplier_rfq"
    status: GateStatus
    missing_fields: tuple[str, ...] = ()
    conflict_ids: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    open_proposal_fields: tuple[str, ...] = ()
    profile_version: str


class ProductSpecVersion(FrozenSpecModel):
    spec_version_id: str = Field(min_length=1)
    spec_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    parent_version_id: str | None = None
    profile_ref: VersionRef
    fields: tuple[SpecFieldProjection, ...]
    conflicts: tuple[SpecConflict, ...] = ()
    claim_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    open_proposal_fields: tuple[str, ...] = ()
    unresolved_conflict_ids: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    readiness: ReadinessResult
    state: SpecState
    normalizer_versions: tuple[VersionRef, ...] = ()
    fingerprint: str = Field(min_length=1)
    created_at: datetime
    created_by: str = Field(min_length=1)

    @field_validator("created_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class SpecDiff(FrozenSpecModel):
    left_version_id: str
    right_version_id: str
    added_fields: tuple[str, ...] = ()
    removed_fields: tuple[str, ...] = ()
    changed_fields: tuple[str, ...] = ()
    state_changed_fields: tuple[str, ...] = ()
