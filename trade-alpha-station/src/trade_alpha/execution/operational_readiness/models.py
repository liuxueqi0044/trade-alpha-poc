"""Immutable EP-09 operational-readiness and maker-checker contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.contracts import ProvenanceRef
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint


class FrozenOperationalModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OperationalCapability(StrEnum):
    INTERNAL_QUOTE_DRY_RUN = "internal_quote_dry_run"
    CUSTOMER_QUOTE = "customer_quote"
    ORDER = "order"
    PAYMENT = "payment"
    EXPORT = "export"


class ReadinessItemState(StrEnum):
    VERIFIED = "verified"
    PENDING = "pending"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


class ReadinessEvaluationStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class ApprovalStage(StrEnum):
    REVIEW = "review"
    APPROVAL = "approval"


class ApprovalOutcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REQUEST_CHANGES = "request_changes"


class ApprovalRequestStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class ExecutionDependencyKind(StrEnum):
    COST = "cost"
    RISK = "risk"


class ReadinessItem(FrozenOperationalModel):
    item_id: str = Field(min_length=1)
    state: ReadinessItemState
    reason_codes: tuple[str, ...] = ()
    evidence_refs: tuple[ProvenanceRef, ...] = ()
    verified_at: datetime | None = None

    @field_validator("verified_at")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    def model_post_init(self, __context: object) -> None:
        if self.state is ReadinessItemState.VERIFIED:
            if not self.evidence_refs or self.verified_at is None:
                raise ValueError("verified readiness item requires evidence and verified_at")
        elif self.verified_at is not None:
            raise ValueError("only verified readiness items may have verified_at")
        if self.state is ReadinessItemState.NOT_APPLICABLE and not self.reason_codes:
            raise ValueError("not_applicable readiness item requires a policy reason")


class OperationalReadinessPolicyVersion(FrozenOperationalModel):
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    required_items: dict[OperationalCapability, tuple[str, ...]] = Field(min_length=1)
    production_actions_enabled: bool = False
    valid_from: datetime
    valid_until: datetime | None = None
    config_fingerprint: str = Field(min_length=1)

    @field_validator("valid_from", "valid_until")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    def model_post_init(self, __context: object) -> None:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("policy valid_until must follow valid_from")
        if self.config_fingerprint != readiness_policy_fingerprint(self):
            raise ValueError("readiness policy config_fingerprint mismatch")


class OperationalReadinessSnapshot(FrozenOperationalModel):
    readiness_snapshot_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    observed_at: datetime
    items: tuple[ReadinessItem, ...]
    content_fingerprint: str = Field(min_length=1)

    @field_validator("observed_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class ReadinessEvaluationSnapshot(FrozenOperationalModel):
    readiness_evaluation_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    resource_fingerprint: str = Field(min_length=1)
    readiness_snapshot_id: str = Field(min_length=1)
    readiness_snapshot_fingerprint: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_config_fingerprint: str = Field(min_length=1)
    capability: OperationalCapability
    as_of: datetime
    status: ReadinessEvaluationStatus
    hard_blocks: tuple[str, ...] = ()
    content_fingerprint: str = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class ExecutionDependencySnapshot(FrozenOperationalModel):
    """Narrow EP-07/EP-08 reader result, deliberately independent of their models."""

    dependency_id: str = Field(min_length=1)
    kind: ExecutionDependencyKind
    content_fingerprint: str = Field(min_length=1)
    as_of: datetime
    valid_until: datetime | None = None
    complete: bool = True
    hard_block: bool = False
    is_lineage_tip: bool = True

    @field_validator("as_of", "valid_until")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)


class ApprovalRequest(FrozenOperationalModel):
    approval_request_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    resource_fingerprint: str = Field(min_length=1)
    readiness_evaluation_id: str = Field(min_length=1)
    readiness_evaluation_fingerprint: str = Field(min_length=1)
    cost_scenario_id: str = Field(min_length=1)
    cost_scenario_fingerprint: str = Field(min_length=1)
    risk_assessment_id: str = Field(min_length=1)
    risk_assessment_fingerprint: str = Field(min_length=1)
    maker_id: str = Field(min_length=1)
    created_at: datetime
    expires_at: datetime
    idempotency_key: str = Field(min_length=1)
    status: ApprovalRequestStatus = ApprovalRequestStatus.PENDING_REVIEW
    content_fingerprint: str = Field(min_length=1)

    @field_validator("created_at", "expires_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    def model_post_init(self, __context: object) -> None:
        if self.expires_at <= self.created_at:
            raise ValueError("approval request expiry must follow creation")


class ApprovalDecision(FrozenOperationalModel):
    decision_id: str = Field(min_length=1)
    approval_request_id: str = Field(min_length=1)
    request_fingerprint: str = Field(min_length=1)
    stage: ApprovalStage
    outcome: ApprovalOutcome
    actor_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    decided_at: datetime
    idempotency_key: str = Field(min_length=1)

    @field_validator("decided_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


def readiness_policy_fingerprint(policy: OperationalReadinessPolicyVersion) -> str:
    return stable_fingerprint(
        {
            "policy_id": policy.policy_id,
            "version": policy.version,
            "required_items": policy.required_items,
            "production_actions_enabled": policy.production_actions_enabled,
            "valid_from": policy.valid_from,
            "valid_until": policy.valid_until,
        }
    )


def readiness_snapshot_fingerprint(snapshot: OperationalReadinessSnapshot) -> str:
    return stable_fingerprint(
        {
            "resource_id": snapshot.resource_id,
            "observed_at": snapshot.observed_at,
            "items": snapshot.items,
        }
    )


def evaluation_fingerprint(values: dict[str, object]) -> str:
    return stable_fingerprint(values)


def readiness_evaluation_fingerprint(snapshot: ReadinessEvaluationSnapshot) -> str:
    return evaluation_fingerprint(
        {
            "resource_id": snapshot.resource_id,
            "resource_fingerprint": snapshot.resource_fingerprint,
            "snapshot": snapshot.readiness_snapshot_id,
            "snapshot_fingerprint": snapshot.readiness_snapshot_fingerprint,
            "policy": (
                snapshot.policy_id,
                snapshot.policy_version,
                snapshot.policy_config_fingerprint,
            ),
            "capability": snapshot.capability,
            "as_of": snapshot.as_of,
            "hard_blocks": snapshot.hard_blocks,
        }
    )


def approval_request_fingerprint(request: ApprovalRequest) -> str:
    """Fingerprint the immutable approval basis, deliberately excluding mutable status."""
    return stable_fingerprint(
        {
            "evaluation": request.readiness_evaluation_fingerprint,
            "cost_id": request.cost_scenario_id,
            "cost": request.cost_scenario_fingerprint,
            "risk_id": request.risk_assessment_id,
            "risk": request.risk_assessment_fingerprint,
            "maker": request.maker_id,
            "expires": request.expires_at,
        }
    )
