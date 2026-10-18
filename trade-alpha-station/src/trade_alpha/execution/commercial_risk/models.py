"""Immutable, deny-by-default commercial risk contracts for EP-08."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_alpha.contracts.common import VersionRef, require_utc
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.costing.models import CostScenarioSnapshot
from trade_alpha.execution.specifications.models import ProductSpecVersion
from trade_alpha.execution.supplier_quotes.models import (
    QuoteNormalizationRun,
    SupplierComparisonMatrix,
    SupplierQuoteSnapshot,
)
from trade_alpha.execution.suppliers.models import SupplierQualificationSnapshot


class FrozenRiskModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RiskDimension(StrEnum):
    SAFETY = "safety"
    SUPPLIER = "supplier"
    QUOTE = "quote"
    COST = "cost"
    TRANSACTION = "transaction"


class RiskSeverity(StrEnum):
    HARD_BLOCK = "hard_block"
    APPROVAL_REQUIRED = "approval_required"
    WARNING = "warning"


class RiskDisposition(StrEnum):
    BLOCKED = "blocked"
    REVIEW_REQUIRED = "review_required"
    CONDITIONALLY_CLEAR = "conditionally_clear"
    CLEAR_FOR_INTERNAL_QUOTE = "clear_for_internal_quote"
    EXPIRED = "expired"


class FindingState(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class RiskDecisionAction(StrEnum):
    RESOLVE = "resolve"
    REQUEST_EVIDENCE = "request_evidence"
    BLOCK = "block"


class RiskPolicyVersion(FrozenRiskModel):
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    product_family_id: str = Field(min_length=1)
    purpose: str = Field(default="internal_quote", min_length=1)
    effective_from: datetime
    effective_until: datetime | None = None
    rule_version: str = Field(min_length=1)
    schema_version: str = "commercial-risk-policy/1.0.0"
    hard_block_codes: tuple[str, ...] = ()
    approval_required_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    allowed_human_roles: tuple[str, ...] = Field(min_length=1)
    minimum_contribution_margin_rate: Decimal | None = Field(default=None, ge=0, le=1)
    required_transaction_evidence: tuple[str, ...] = ()
    config_fingerprint: str = Field(min_length=1)

    @field_validator("effective_from", "effective_until")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @field_validator("minimum_contribution_margin_rate", mode="before")
    @classmethod
    def decimal_rate(cls, value: object | None) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, (bool, float)):
            raise TypeError("margin rate must not be bool or binary float")
        try:
            return Decimal(str(value))
        except Exception as error:
            raise TypeError("margin rate must be Decimal-compatible") from error

    @model_validator(mode="after")
    def effective_range(self) -> RiskPolicyVersion:
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("risk policy effective_until must not precede effective_from")
        configured = (
            set(self.hard_block_codes) | set(self.approval_required_codes) | set(self.warning_codes)
        )
        if len(configured) != len(
            self.hard_block_codes + self.approval_required_codes + self.warning_codes
        ):
            raise ValueError("risk policy finding codes must be unique across severity groups")
        return self


class RiskFinding(FrozenRiskModel):
    finding_id: str = Field(min_length=1)
    dimension: RiskDimension
    code: str = Field(min_length=1)
    severity: RiskSeverity
    state: FindingState = FindingState.OPEN
    subject_id: str = Field(min_length=1)
    input_refs: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    message_key: str = Field(min_length=1)
    suggested_action: str = Field(min_length=1)
    resolved_by_decision_id: str | None = None

    @model_validator(mode="after")
    def resolution_shape(self) -> RiskFinding:
        if self.state is FindingState.RESOLVED and not self.resolved_by_decision_id:
            raise ValueError("resolved risk finding requires a decision reference")
        if self.state is FindingState.OPEN and self.resolved_by_decision_id:
            raise ValueError("open risk finding cannot reference a resolution decision")
        return self


class RiskDecision(FrozenRiskModel):
    decision_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    finding_ids: tuple[str, ...] = Field(min_length=1)
    decision: RiskDecisionAction
    actor_id: str = Field(min_length=1)
    actor_role: str = Field(min_length=1)
    client_identity: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    condition_codes: tuple[str, ...] = ()
    decided_at: datetime
    supersedes_decision_id: str | None = None

    @field_validator("decided_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def decision_shape(self) -> RiskDecision:
        if len(self.finding_ids) != len(set(self.finding_ids)):
            raise ValueError("risk decision finding_ids must be unique")
        return self


class RiskAssessmentInput(FrozenRiskModel):
    """The exact upstream snapshot references consumed by the risk evaluator."""

    product_spec: ProductSpecVersion
    qualification: SupplierQualificationSnapshot
    quote_snapshots: tuple[SupplierQuoteSnapshot, ...] = Field(min_length=1)
    normalization_run: QuoteNormalizationRun
    comparison_matrix: SupplierComparisonMatrix
    cost_scenarios: tuple[CostScenarioSnapshot, ...] = ()
    license_allowed: bool = True
    license_reason: str = ""
    transaction_evidence: tuple[str, ...] = ()
    quote_quarantine_statuses: tuple[str, ...] = ()


class TradeRiskAssessmentSnapshot(FrozenRiskModel):
    assessment_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    product_family_id: str = Field(min_length=1)
    product_spec_version_id: str = Field(min_length=1)
    qualification_snapshot_id: str = Field(min_length=1)
    quote_snapshot_ids: tuple[str, ...] = Field(min_length=1)
    normalization_run_id: str = Field(min_length=1)
    comparison_matrix_id: str = Field(min_length=1)
    cost_scenario_ids: tuple[str, ...] = ()
    policy_version: VersionRef
    policy_config_fingerprint: str = Field(min_length=1)
    as_of: datetime
    valid_until: datetime | None = None
    findings: tuple[RiskFinding, ...] = ()
    disposition: RiskDisposition
    effective_decision_ids: tuple[str, ...] = ()
    supersedes_assessment_id: str | None = None
    input_fingerprint: str = Field(min_length=1)
    result_fingerprint: str = Field(min_length=1)
    created_at: datetime

    @field_validator("as_of", "valid_until", "created_at")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def valid_range(self) -> TradeRiskAssessmentSnapshot:
        if self.valid_until is not None and self.valid_until < self.as_of:
            raise ValueError("risk assessment valid_until must not precede as_of")
        hard = [item for item in self.findings if item.severity is RiskSeverity.HARD_BLOCK]
        if hard and self.disposition is not RiskDisposition.BLOCKED:
            raise ValueError("hard-block findings require blocked disposition")
        return self


def policy_content_fingerprint(policy: RiskPolicyVersion) -> str:
    return stable_fingerprint(policy.model_dump(exclude={"config_fingerprint"}))


def assessment_content_fingerprint(snapshot: TradeRiskAssessmentSnapshot) -> str:
    return stable_fingerprint(snapshot.model_dump(exclude={"result_fingerprint"}))


def deterministic_finding_id(subject_id: str, code: str, input_refs: tuple[str, ...]) -> str:
    return str(uuid5(NAMESPACE_URL, f"trade-alpha/ep08/finding/{subject_id}/{code}/{input_refs}"))
