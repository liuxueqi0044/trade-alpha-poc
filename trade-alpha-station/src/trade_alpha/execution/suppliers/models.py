"""Immutable, evidence-aware supplier qualification contracts for EP-04."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_alpha.contracts.common import Confidence, VersionRef, require_utc
from trade_alpha.execution.contracts import ProvenanceRef
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint


class FrozenSupplierModel(BaseModel):
    """Base model for append-only supplier-domain facts."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SupplierLifecycleStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE_FOR_DRY_RUN = "active_for_dry_run"
    DORMANT = "dormant"
    BLOCKED = "blocked"
    MERGED = "merged"


class SiteType(StrEnum):
    FACTORY = "factory"
    OFFICE = "office"
    WAREHOUSE = "warehouse"
    UNKNOWN = "unknown"


class IdentifierFormatStatus(StrEnum):
    UNCHECKED = "unchecked"
    VALID_FORMAT = "valid_format"
    INVALID_FORMAT = "invalid_format"
    UNSUPPORTED = "unsupported"


class RegistryStatus(StrEnum):
    NOT_CHECKED = "not_checked"
    CANDIDATE_MATCH = "candidate_match"
    VERIFIED_BY_HUMAN = "verified_by_human"
    MISMATCH = "mismatch"


class CapabilityType(StrEnum):
    MANUFACTURING = "manufacturing"
    MATERIAL = "material"
    PROCESS = "process"
    TESTING = "testing"
    SAMPLING = "sampling"
    PACKAGING = "packaging"
    CAPACITY = "capacity"
    SUPPLY = "supply"


class CapabilitySourceType(StrEnum):
    SUPPLIER_ASSERTED = "supplier_asserted"
    DOCUMENTED = "documented"
    OBSERVED = "observed"
    THIRD_PARTY = "third_party"


class ClaimReviewStatus(StrEnum):
    PENDING = "pending"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class ExtractionStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    EXTRACTED = "extracted"
    FAILED = "failed"
    HUMAN_ENTERED = "human_entered"


class DocumentReviewStatus(StrEnum):
    PENDING = "pending"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class ExpiryStatus(StrEnum):
    CURRENT = "current"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class AuthenticityStatus(StrEnum):
    NOT_CHECKED = "not_checked"
    CANDIDATE_VERIFIED = "candidate_verified"
    VERIFIED_BY_HUMAN = "verified_by_human"
    REJECTED = "rejected"


class ScreeningRunStatus(StrEnum):
    COMPLETED = "completed"
    PROVIDER_ERROR = "provider_error"
    DATASET_STALE = "dataset_stale"
    UNSUPPORTED = "unsupported"


class ScreeningDisposition(StrEnum):
    NO_CANDIDATE = "no_candidate"
    CANDIDATE_FOUND = "candidate_found"
    REVIEW_REQUIRED = "review_required"
    NOT_ASSESSED = "not_assessed"


class QualificationPurpose(StrEnum):
    DRY_RUN = "dry_run"
    FUTURE_OUTBOUND = "future_outbound"


class RequirementStatus(StrEnum):
    SATISFIED = "satisfied"
    MISSING = "missing"
    WARNING = "warning"
    BLOCKED = "blocked"
    NOT_ASSESSED = "not_assessed"


class MachineDisposition(StrEnum):
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


class QualificationDecisionAction(StrEnum):
    APPROVE_FOR_DRY_RUN = "approve_for_dry_run"
    APPROVE_WITH_CONDITIONS = "approve_with_conditions"
    REQUEST_EVIDENCE = "request_evidence"
    BLOCK = "block"


class QualificationDisposition(StrEnum):
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    REVIEW_REQUIRED = "review_required"
    DRY_RUN_ELIGIBLE = "dry_run_eligible"
    DRY_RUN_ELIGIBLE_WITH_CONDITIONS = "dry_run_eligible_with_conditions"
    BLOCKED = "blocked"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class IdentityDecisionAction(StrEnum):
    CREATE_NEW = "create_new"
    LINK_EXISTING = "link_existing"
    MERGE = "merge"


class SupplierProfile(FrozenSupplierModel):
    supplier_id: str = Field(min_length=1)
    current_version_id: str | None = None
    lifecycle_status: SupplierLifecycleStatus = SupplierLifecycleStatus.CANDIDATE
    merged_into_supplier_id: str | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def merge_state_is_explicit(self) -> SupplierProfile:
        if (
            self.lifecycle_status is SupplierLifecycleStatus.MERGED
            and not self.merged_into_supplier_id
        ):
            raise ValueError("merged SupplierProfile requires merged_into_supplier_id")
        if (
            self.lifecycle_status is not SupplierLifecycleStatus.MERGED
            and self.merged_into_supplier_id
        ):
            raise ValueError("merged_into_supplier_id requires merged lifecycle status")
        if self.merged_into_supplier_id == self.supplier_id:
            raise ValueError("supplier cannot be merged into itself")
        return self


class SupplierSiteVersion(FrozenSupplierModel):
    site_id: str = Field(min_length=1)
    site_version_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    site_type: SiteType
    address_raw: str = Field(min_length=1)
    address_normalized: str | None = None
    country_or_region: str = Field(min_length=1)
    geo_candidate: str | None = None
    geo_confirmed_by_human: bool = False
    claimed_functions: tuple[str, ...] = ()
    provenance_refs: tuple[ProvenanceRef, ...] = Field(min_length=1)
    valid_from: datetime
    valid_until: datetime | None = None
    content_fingerprint: str = Field(min_length=1)

    @field_validator("valid_from", "valid_until")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def valid_range(self) -> SupplierSiteVersion:
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("site valid_until must not precede valid_from")
        return self


class BusinessIdentifierClaim(FrozenSupplierModel):
    identifier_claim_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    identifier_type: str = Field(min_length=1)
    country_or_region: str = Field(min_length=1)
    raw_value: str = Field(min_length=1)
    compact_value: str = Field(min_length=1)
    format_status: IdentifierFormatStatus = IdentifierFormatStatus.UNCHECKED
    registry_status: RegistryStatus = RegistryStatus.NOT_CHECKED
    validator_version: VersionRef = Field(
        default_factory=lambda: VersionRef(name="identifier-validator", version="unspecified")
    )
    provenance_ref: ProvenanceRef


class SupplierProfileVersion(FrozenSupplierModel):
    supplier_profile_version_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    legal_name_raw: str = Field(min_length=1)
    legal_name_normalized: str = Field(min_length=1)
    trading_names: tuple[str, ...] = ()
    country_or_region: str = Field(min_length=1)
    sites: tuple[SupplierSiteVersion, ...] = ()
    identifiers: tuple[BusinessIdentifierClaim, ...] = ()
    contact_refs: tuple[str, ...] = ()
    provenance_refs: tuple[ProvenanceRef, ...] = Field(min_length=1)
    valid_from: datetime
    valid_until: datetime | None = None
    system_time: datetime
    content_fingerprint: str = Field(min_length=1)
    supersedes: str | None = None

    @field_validator("valid_from", "valid_until", "system_time")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def owned_children_and_time(self) -> SupplierProfileVersion:
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("profile valid_until must not precede valid_from")
        if any(item.supplier_id != self.supplier_id for item in self.sites):
            raise ValueError("all site versions must belong to the profile supplier")
        if any(item.supplier_id != self.supplier_id for item in self.identifiers):
            raise ValueError("all identifier claims must belong to the profile supplier")
        return self


class SupplierCapabilityClaim(FrozenSupplierModel):
    capability_claim_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    product_family_id: str = Field(min_length=1)
    capability_type: CapabilityType
    claim_value: Any
    spec_scope: tuple[str, ...] = ()
    source_type: CapabilitySourceType
    provenance_refs: tuple[ProvenanceRef, ...] = Field(min_length=1)
    confidence: Confidence
    valid_from: datetime
    valid_until: datetime | None = None
    recorded_at: datetime
    review_status: ClaimReviewStatus = ClaimReviewStatus.PENDING
    supersedes: str | None = None

    @field_validator("valid_from", "valid_until", "recorded_at")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def valid_range(self) -> SupplierCapabilityClaim:
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("capability valid_until must not precede valid_from")
        return self


class SupplierDocumentClaim(FrozenSupplierModel):
    document_claim_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    document_type: str = Field(min_length=1)
    issuer_name: str | None = None
    issuer_identifier: str | None = None
    document_number: str | None = None
    subject_supplier_id: str = Field(min_length=1)
    site_id: str | None = None
    scope_text: str | None = None
    product_family_scope: tuple[str, ...] = ()
    issued_at: datetime | None = None
    valid_until: datetime | None = None
    recorded_at: datetime
    artifact_id: str = Field(min_length=1)
    block_ids: tuple[str, ...] = ()
    extraction_status: ExtractionStatus = ExtractionStatus.NOT_APPLICABLE
    human_review_status: DocumentReviewStatus = DocumentReviewStatus.PENDING
    expiry_status: ExpiryStatus = ExpiryStatus.UNKNOWN
    authenticity_status: AuthenticityStatus = AuthenticityStatus.NOT_CHECKED

    @field_validator("issued_at", "valid_until", "recorded_at")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def subject_is_supplier_and_dates_ordered(self) -> SupplierDocumentClaim:
        if self.subject_supplier_id != self.supplier_id:
            raise ValueError("document subject_supplier_id must equal supplier_id")
        if (
            self.issued_at is not None
            and self.valid_until is not None
            and self.valid_until < self.issued_at
        ):
            raise ValueError("document valid_until must not precede issued_at")
        return self


class DuplicateCandidate(FrozenSupplierModel):
    candidate_id: str = Field(min_length=1)
    source_supplier_id: str = Field(min_length=1)
    target_supplier_id: str = Field(min_length=1)
    score: float = Field(ge=0, le=100)
    matching_fields: tuple[str, ...] = Field(min_length=1)
    proposed_at: datetime

    @field_validator("proposed_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def distinct_suppliers(self) -> DuplicateCandidate:
        if self.source_supplier_id == self.target_supplier_id:
            raise ValueError("duplicate candidate must reference two suppliers")
        return self


class SupplierIdentityDecision(FrozenSupplierModel):
    decision_id: str = Field(min_length=1)
    action: IdentityDecisionAction
    actor_id: str = Field(min_length=1)
    source_supplier_id: str = Field(min_length=1)
    target_supplier_id: str | None = None
    candidate_id: str | None = None
    rationale: str = Field(min_length=1)
    decided_at: datetime
    supersedes_decision_id: str | None = None

    @field_validator("decided_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def action_target_requirements(self) -> SupplierIdentityDecision:
        if self.action in {IdentityDecisionAction.LINK_EXISTING, IdentityDecisionAction.MERGE}:
            if not self.target_supplier_id:
                raise ValueError("link_existing and merge require target_supplier_id")
            if self.target_supplier_id == self.source_supplier_id:
                raise ValueError("identity decision target must differ from source")
        if self.action is IdentityDecisionAction.CREATE_NEW and self.target_supplier_id:
            raise ValueError("create_new must not have target_supplier_id")
        return self


class ScreeningCandidateMatch(FrozenSupplierModel):
    source_entity_id: str = Field(min_length=1)
    score: float = Field(ge=0, le=100)
    matched_fields: tuple[str, ...] = Field(min_length=1)
    source_name: str | None = None


class CounterpartyScreeningRun(FrozenSupplierModel):
    screening_run_id: str = Field(min_length=1)
    supplier_profile_version_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    dataset_scope: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    published_at: datetime
    algorithm: str = Field(min_length=1)
    algorithm_version: str = Field(min_length=1)
    request_fingerprint: str = Field(min_length=1)
    candidate_matches: tuple[ScreeningCandidateMatch, ...] = ()
    run_status: ScreeningRunStatus
    disposition: ScreeningDisposition
    completed_at: datetime

    @field_validator("published_at", "completed_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def screening_semantics_are_safe(self) -> CounterpartyScreeningRun:
        if self.run_status is not ScreeningRunStatus.COMPLETED:
            if self.disposition is ScreeningDisposition.NO_CANDIDATE:
                raise ValueError("failed or stale screening cannot be no_candidate")
            if self.disposition is not ScreeningDisposition.NOT_ASSESSED:
                raise ValueError("failed or stale screening must be not_assessed")
        if self.run_status is ScreeningRunStatus.COMPLETED:
            if self.candidate_matches and self.disposition is ScreeningDisposition.NO_CANDIDATE:
                raise ValueError("candidate matches cannot have no_candidate disposition")
            if (
                not self.candidate_matches
                and self.disposition is ScreeningDisposition.CANDIDATE_FOUND
            ):
                raise ValueError("candidate_found requires candidate matches")
        return self


class CapabilityPredicate(FrozenSupplierModel):
    requirement_id: str = Field(min_length=1)
    product_family_id: str = Field(min_length=1)
    capability_types: tuple[CapabilityType, ...] = Field(min_length=1)
    required_scope_tokens: tuple[str, ...] = ()


class QualificationPolicyVersion(FrozenSupplierModel):
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    product_family_id: str = Field(min_length=1)
    purpose: QualificationPurpose = QualificationPurpose.DRY_RUN
    required_evidence_types: tuple[str, ...] = ()
    required_capability_predicates: tuple[CapabilityPredicate, ...] = ()
    maximum_screening_age: timedelta = timedelta(days=30)
    hard_blocks: tuple[str, ...] = ()
    review_required_conditions: tuple[str, ...] = ()
    allowed_human_roles: tuple[str, ...] = Field(min_length=1)
    config_fingerprint: str = Field(min_length=1)
    effective_from: datetime
    effective_until: datetime | None = None

    @field_validator("effective_from", "effective_until")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @field_validator("maximum_screening_age")
    @classmethod
    def positive_age(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            raise ValueError("maximum_screening_age must be positive")
        return value

    @model_validator(mode="after")
    def effective_range(self) -> QualificationPolicyVersion:
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("policy effective_until must not precede effective_from")
        return self


class RequirementResult(FrozenSupplierModel):
    requirement_id: str = Field(min_length=1)
    status: RequirementStatus
    reason_code: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class SupplierQualificationAssessment(FrozenSupplierModel):
    assessment_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    supplier_profile_version_id: str = Field(min_length=1)
    product_family_id: str = Field(min_length=1)
    product_spec_version_id: str = Field(min_length=1)
    qualification_policy_version_id: str = Field(min_length=1)
    policy_config_fingerprint: str = Field(min_length=1)
    as_of: datetime
    requirement_results: tuple[RequirementResult, ...] = Field(min_length=1)
    missing_evidence: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    hard_blocks: tuple[str, ...] = ()
    machine_disposition: MachineDisposition
    valid_until: datetime
    input_fingerprint: str = Field(min_length=1)
    result_fingerprint: str = Field(min_length=1)
    created_at: datetime

    @field_validator("as_of", "valid_until", "created_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class SupplierQualificationDecision(FrozenSupplierModel):
    decision_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    actor_role: str = Field(min_length=1)
    decision: QualificationDecisionAction
    condition_codes: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)
    decided_at: datetime
    expires_at: datetime | None = None
    supersedes_decision_id: str | None = None

    @field_validator("decided_at", "expires_at")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def decision_conditions_and_time(self) -> SupplierQualificationDecision:
        if (
            self.decision is QualificationDecisionAction.APPROVE_WITH_CONDITIONS
            and not self.condition_codes
        ):
            raise ValueError("approve_with_conditions requires condition_codes")
        if (
            self.decision is not QualificationDecisionAction.APPROVE_WITH_CONDITIONS
            and self.condition_codes
        ):
            raise ValueError("condition_codes are only permitted for approve_with_conditions")
        if self.expires_at is not None and self.expires_at <= self.decided_at:
            raise ValueError("decision expires_at must follow decided_at")
        return self


class SupplierQualificationSnapshot(FrozenSupplierModel):
    qualification_snapshot_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    supplier_profile_version_id: str = Field(min_length=1)
    product_family_id: str = Field(min_length=1)
    product_spec_version_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    effective_decision_id: str | None = None
    disposition: QualificationDisposition
    conditions: tuple[str, ...] = ()
    screening_run_refs: tuple[str, ...] = ()
    evidence_manifest: tuple[str, ...] = ()
    as_of: datetime
    valid_until: datetime | None = None
    policy_version: str = Field(min_length=1)
    content_fingerprint: str = Field(min_length=1)

    @field_validator("as_of", "valid_until")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def eligibility_requires_human_decision(self) -> SupplierQualificationSnapshot:
        eligible = {
            QualificationDisposition.DRY_RUN_ELIGIBLE,
            QualificationDisposition.DRY_RUN_ELIGIBLE_WITH_CONDITIONS,
        }
        if self.disposition in eligible and not self.effective_decision_id:
            raise ValueError("eligible qualification snapshot requires an effective human decision")
        if (
            self.disposition is QualificationDisposition.DRY_RUN_ELIGIBLE_WITH_CONDITIONS
            and not self.conditions
        ):
            raise ValueError("conditional eligibility requires conditions")
        return self


def qualification_snapshot_content_fingerprint(
    snapshot: SupplierQualificationSnapshot,
) -> str:
    """Recompute the immutable EP-04 snapshot proof at a trust boundary."""

    payload = snapshot.model_dump(mode="python")
    payload["content_fingerprint"] = "pending"
    return stable_fingerprint(payload)
