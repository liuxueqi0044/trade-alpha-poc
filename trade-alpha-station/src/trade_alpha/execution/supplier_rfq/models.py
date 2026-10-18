"""Immutable contracts for supplier-specific RFQ dry runs (EP-05).

The models in this module deliberately contain no transport or vendor SDK types.  A
campaign is an internal container; a :class:`SupplierRfqPackageSnapshot` is the
only object that may be rendered, and even that object can only be delivered to
the null adapter in P0.
"""

from __future__ import annotations

# ruff: noqa: E501
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_alpha.contracts.common import VersionRef, require_utc, utc_now
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.contracts.models import Incoterm, require_decimal


class FrozenRfqModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CampaignPurpose(StrEnum):
    DRY_RUN = "dry_run"


class PackageStatus(StrEnum):
    DRAFT = "draft"
    BLOCKED_SPEC = "blocked_spec"
    BLOCKED_SUPPLIER = "blocked_supplier"
    BLOCKED_DISCLOSURE = "blocked_disclosure"
    BLOCKED_APPROVAL = "blocked_approval"
    READY_FOR_INTERNAL_REVIEW = "ready_for_internal_review"
    APPROVED_FOR_DRY_RUN = "approved_for_dry_run"
    RENDERED_NOT_SENT = "rendered_not_sent"
    SUPERSEDED = "superseded"


class OutboundDisposition(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    BLOCKED_BY_NULL_ADAPTER = "blocked_by_null_adapter"


class ReadinessStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class ReadinessIssueKind(StrEnum):
    HARD_BLOCK = "hard_block"
    REQUIRED_QUESTION = "required_question"
    WARNING = "warning"


class DryRunDecision(StrEnum):
    APPROVE = "approve_for_dry_run"
    APPROVE_WITH_CONDITIONS = "approve_with_conditions"
    REQUEST_CHANGES = "request_changes"
    BLOCK = "block"


class AnswerScope(StrEnum):
    LINE = "line"
    PACKAGE = "package"


class QuantityTier(FrozenRfqModel):
    quantity: Decimal = Field(gt=0)
    unit: str = Field(min_length=1)

    @field_validator("quantity", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return require_decimal(value)


class RequestedIncoterm(FrozenRfqModel):
    incoterm: Incoterm

    @property
    def code(self) -> str:
        return self.incoterm.code.value


class RequirementValue(FrozenRfqModel):
    field_key: str = Field(min_length=1)
    value: Any | None = None
    status: str | None = None


class AttachmentManifestEntry(FrozenRfqModel):
    artifact_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    media_type: str = "application/octet-stream"
    metadata_sanitized: bool = False
    metadata_sanitization_note: str | None = None

    @field_validator("sha256")
    @classmethod
    def valid_hash(cls, value: str) -> str:
        digest = value.removeprefix("sha256:")
        if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            raise ValueError("attachment hash must be a complete SHA-256 hexadecimal digest")
        return value



class SupplierRfqLine(FrozenRfqModel):
    line_id: str = Field(min_length=1)
    product_spec_version_id: str = Field(min_length=1)
    quantity_tier: QuantityTier
    requested_unit: str = Field(min_length=1)
    requirements: tuple[RequirementValue, ...] = ()
    supplier_part_number_request: bool = True
    drawing_disclosure_refs: tuple[str, ...] = ()
    requested_sample_quantity: QuantityTier | None = None
    requested_production_lead_time: bool = True
    requested_packaging_response: bool = True
    requested_test_documents: bool = True
    requested_exceptions_response: bool = True

    @model_validator(mode="after")
    def unit_matches_tier(self) -> SupplierRfqLine:
        if self.requested_unit != self.quantity_tier.unit:
            raise ValueError("requested_unit must match quantity tier unit")
        return self


class OpenSpecificationQuestion(FrozenRfqModel):
    question_id: str = Field(min_length=1)
    source_spec_field: str = Field(min_length=1)
    prompt_key: str = Field(min_length=1)
    expected_value_type: str = Field(min_length=1)
    allowed_units: tuple[str, ...] = ()
    allowed_values: tuple[str, ...] = ()
    required_for_quote: bool = True
    answer_scope: AnswerScope = AnswerScope.LINE
    reason_code: str = Field(min_length=1)
    provenance_ref: str = Field(min_length=1)


class RequestedCommercialTerm(FrozenRfqModel):
    key: str = Field(min_length=1)
    prompt_key: str = Field(min_length=1)
    required: bool = True
    optional: bool = False
    not_applicable_allowed: bool = False
    expected_value_type: str = Field(min_length=1)
    allowed_values: tuple[str, ...] = ()
    unit: str | None = None
    currency_required: bool = False

    @model_validator(mode="after")
    def term_flags(self) -> RequestedCommercialTerm:
        if self.required and self.optional:
            raise ValueError("commercial term cannot be both required and optional")
        return self


class DisclosurePolicyVersion(FrozenRfqModel):
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    buyer_alias: str = Field(min_length=1)
    artifact_whitelist: tuple[str, ...] = ()
    disclose_buyer_identity: bool = False
    disclose_buyer_contacts: bool = False
    disclose_competing_suppliers: bool = False
    disclose_internal_targets: bool = False
    disclose_internal_evidence: bool = False
    strip_drawing_metadata: bool = True
    disclose_safety_exclusions: bool = True
    fingerprint: str | None = None

    @model_validator(mode="after")
    def no_sensitive_defaults(self) -> DisclosurePolicyVersion:
        if self.disclose_buyer_identity or self.disclose_buyer_contacts:
            raise ValueError("P0 disclosure policy cannot expose buyer identity or contacts")
        if self.disclose_competing_suppliers or self.disclose_internal_targets:
            raise ValueError("P0 disclosure policy cannot expose competing suppliers or targets")
        if self.disclose_internal_evidence:
            raise ValueError("P0 disclosure policy cannot expose internal evidence")
        return self

    def content_fingerprint(self) -> str:
        return stable_fingerprint(self.model_dump(mode="json", exclude={"fingerprint"}))


class PackageReadinessResult(FrozenRfqModel):
    status: ReadinessStatus
    hard_blocks: tuple[str, ...] = ()
    required_questions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    checked_at: datetime = Field(default_factory=utc_now)
    policy_version: str = "supplier-rfq-readiness/1.0.0"

    @field_validator("checked_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @property
    def machine_ready(self) -> bool:
        return self.status is ReadinessStatus.READY and not self.hard_blocks

    @property
    def issues(self) -> tuple[tuple[ReadinessIssueKind, str], ...]:
        return tuple((ReadinessIssueKind.HARD_BLOCK, item) for item in self.hard_blocks) + tuple(
            (ReadinessIssueKind.REQUIRED_QUESTION, item) for item in self.required_questions
        ) + tuple((ReadinessIssueKind.WARNING, item) for item in self.warnings)


class InternalDryRunDecision(FrozenRfqModel):
    decision_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    decision: DryRunDecision
    actor_id: str = Field(min_length=1)
    actor_role: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    condition_codes: tuple[str, ...] = ()
    decided_at: datetime
    expires_at: datetime | None = None

    @field_validator("decided_at", "expires_at")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def approved_only_when_ready(self) -> InternalDryRunDecision:
        if self.decision is DryRunDecision.APPROVE_WITH_CONDITIONS and not self.condition_codes:
            raise ValueError("approve_with_conditions requires condition_codes")
        if self.expires_at is not None and self.expires_at <= self.decided_at:
            raise ValueError("approval expires_at must follow decided_at")
        return self


class SupplierRfqCampaign(FrozenRfqModel):
    campaign_id: str = Field(min_length=1)
    campaign_version: int = Field(ge=1)
    source_rfq_id: str = Field(min_length=1)
    rfq_line_item_id: str = Field(min_length=1)
    product_spec_version_id: str = Field(min_length=1)
    purpose: CampaignPurpose = CampaignPurpose.DRY_RUN
    requested_quantity_tiers: tuple[QuantityTier, ...] = Field(min_length=1)
    response_deadline: datetime
    target_delivery_place: str = Field(min_length=1)
    requested_incoterm_options: tuple[RequestedIncoterm, ...] = Field(min_length=1)
    requested_quote_currencies: tuple[str, ...] = Field(min_length=1)
    supplier_ids: tuple[str, ...] = ()
    supplier_package_refs: tuple[str, ...] = ()
    campaign_fingerprint: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("response_deadline", "created_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def deadline_after_creation(self) -> SupplierRfqCampaign:
        if self.response_deadline < self.created_at:
            raise ValueError("response_deadline must not precede created_at")
        return self


class StructuredRequest(FrozenRfqModel):
    campaign_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    buyer_alias: str = Field(min_length=1)
    product_spec_version_id: str = Field(min_length=1)
    lines: tuple[SupplierRfqLine, ...] = ()
    open_questions: tuple[OpenSpecificationQuestion, ...] = ()
    commercial_terms: tuple[RequestedCommercialTerm, ...] = Field(min_length=1)
    response_deadline: datetime
    target_delivery_place: str = Field(min_length=1)
    requested_incoterms: tuple[RequestedIncoterm, ...] = Field(min_length=1)
    requested_currencies: tuple[str, ...] = Field(min_length=1)

    @field_validator("response_deadline")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class SupplierRfqPackageSnapshot(FrozenRfqModel):
    supplier_rfq_package_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    campaign_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    supplier_profile_version_id: str = Field(min_length=1)
    qualification_snapshot_id: str = Field(min_length=1)
    product_spec_version_id: str = Field(min_length=1)
    template_version: VersionRef
    disclosure_policy_version: VersionRef
    structured_request: StructuredRequest
    open_questions: tuple[OpenSpecificationQuestion, ...] = ()
    attachment_manifest: tuple[AttachmentManifestEntry, ...] = ()
    package_readiness_result: PackageReadinessResult
    internal_approval_decision_id: str | None = None
    rendered_artifact_refs: tuple[str, ...] = ()
    outbound_disposition: OutboundDisposition = OutboundDisposition.NOT_ATTEMPTED
    status: PackageStatus = PackageStatus.DRAFT
    created_at: datetime = Field(default_factory=utc_now)
    content_fingerprint: str = Field(min_length=1)
    input_fingerprint: str | None = None

    @field_validator("created_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def status_boundary(self) -> SupplierRfqPackageSnapshot:
        if self.status is PackageStatus.RENDERED_NOT_SENT and not self.rendered_artifact_refs:
            raise ValueError("rendered package must reference an artifact")
        return self

    @property
    def package_id(self) -> str:
        return self.supplier_rfq_package_id

    @property
    def fingerprint(self) -> str:
        return self.content_fingerprint


class OutboundIntent(FrozenRfqModel):
    package_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    payload_fingerprint: str = Field(min_length=1)
    recipient_contact: None = None
    channel: Literal["null"] = "null"


class DeliveryResult(FrozenRfqModel):
    status: OutboundDisposition = OutboundDisposition.BLOCKED_BY_NULL_ADAPTER
    provider_message_id: None = None
    recipient_contact_used: None = None
    side_effect_count: Literal[0] = 0
    intent_fingerprint: str = Field(min_length=1)
    recorded_at: datetime = Field(default_factory=utc_now)

    @field_validator("recorded_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class SupplierQualificationSnapshotRef(FrozenRfqModel):
    """Small compatibility reference used by fixtures before EP-04 lands."""

    qualification_snapshot_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    supplier_profile_version_id: str = Field(min_length=1)
    product_spec_version_id: str = Field(min_length=1)
    dry_run_eligible: bool
    disposition: str | None = None
    valid_until: datetime | None = None
    content_fingerprint: str = "fixture"
    expires_at: datetime | None = None

    @field_validator("valid_until", "expires_at")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)


def deterministic_id(kind: str, *parts: object) -> str:
    """Generate an id stable across deterministic re-runs."""

    value = "/".join([kind, *(str(part) for part in parts)])
    return str(uuid5(NAMESPACE_URL, "trade-alpha/ep05/" + value))


def content_fingerprint(value: BaseModel | dict[str, Any]) -> str:
    return stable_fingerprint(value)
