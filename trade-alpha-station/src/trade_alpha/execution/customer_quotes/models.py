"""Immutable EP-10 customer-facing quote drafts; no outbound contract exists here."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.contracts import Incoterm, MonetaryAmount, ProvenanceRef
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.operational_readiness.models import ExecutionDependencySnapshot


class FrozenCustomerQuoteModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CustomerQuoteStatus(StrEnum):
    INCOMPLETE = "incomplete"
    BUDGETARY_ONLY = "budgetary_only"
    BLOCKED = "blocked"
    APPROVAL_REQUIRED = "approval_required"
    READY_FOR_HUMAN_APPROVAL = "ready_for_human_approval"


class CustomerCommercialDecisionAction(StrEnum):
    CONFIRM = "confirm"
    MARK_UNKNOWN = "mark_unknown"


class CustomerDraftDisposition(StrEnum):
    RENDERED_NOT_SENT = "rendered_not_sent"


class CustomerQuotePolicyVersion(FrozenCustomerQuoteModel):
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    required_disclosures: tuple[str, ...] = ()
    required_term_paths: tuple[str, ...] = (
        "terms.incoterm",
        "terms.validity",
        "terms.payment_terms",
        "terms.delivery_terms",
        "terms.assumptions",
        "terms.exclusions",
    )
    allow_budgetary_only: bool = False
    allowed_human_roles: tuple[str, ...] = Field(
        default=("commercial_reviewer",), min_length=1
    )
    valid_from: datetime
    valid_until: datetime | None = None
    config_fingerprint: str = Field(min_length=1)

    @field_validator("valid_from", "valid_until")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    def model_post_init(self, __context: object) -> None:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("customer quote policy validity is invalid")
        if self.config_fingerprint != customer_quote_policy_fingerprint(self):
            raise ValueError("customer quote policy config_fingerprint mismatch")
        mandatory = {
            "terms.incoterm",
            "terms.validity",
            "terms.payment_terms",
            "terms.delivery_terms",
            "terms.assumptions",
            "terms.exclusions",
        }
        if not mandatory.issubset(self.required_term_paths):
            raise ValueError("customer quote policy cannot omit mandatory commercial terms")


class CustomerCommercialDecision(FrozenCustomerQuoteModel):
    decision_id: str = Field(min_length=1)
    package_key: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    action: CustomerCommercialDecisionAction
    value: Any | None = None
    actor_id: str = Field(min_length=1)
    actor_role: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    provenance_ref: ProvenanceRef
    decided_at: datetime
    supersedes_decision_id: str | None = None

    @field_validator("decided_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    def model_post_init(self, __context: object) -> None:
        if self.action is CustomerCommercialDecisionAction.CONFIRM and self.value is None:
            raise ValueError("confirmed commercial decision requires a value")
        if self.action is CustomerCommercialDecisionAction.MARK_UNKNOWN and self.value is not None:
            raise ValueError("unknown commercial decision cannot carry a value")


class CustomerCostScenarioSelection(FrozenCustomerQuoteModel):
    selection_id: str = Field(min_length=1)
    package_key: str = Field(min_length=1)
    cost_scenario_id: str = Field(min_length=1)
    cost_scenario_fingerprint: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    actor_role: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    provenance_ref: ProvenanceRef
    decided_at: datetime

    @field_validator("decided_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class CustomerQuoteLine(FrozenCustomerQuoteModel):
    line_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    unit: str = Field(min_length=1)
    unit_price: MonetaryAmount

    @field_validator("quantity", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        if isinstance(value, (float, bool)):
            raise ValueError("customer quote quantity must be Decimal")
        return Decimal(str(value))


class CustomerQuoteTerms(FrozenCustomerQuoteModel):
    incoterm: Incoterm | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    payment_terms: str | None = None
    delivery_terms: str | None = None
    assumptions: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()

    @field_validator("valid_from", "valid_until")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @field_validator("payment_terms", "delivery_terms")
    @classmethod
    def non_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("customer commercial terms cannot be blank")
        return value

    @field_validator("assumptions", "exclusions")
    @classmethod
    def non_blank_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("customer assumptions and exclusions cannot contain blanks")
        return value

    def model_post_init(self, __context: object) -> None:
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until < self.valid_from
        ):
            raise ValueError("customer quote validity is invalid")
        if (self.valid_from is None) != (self.valid_until is None):
            raise ValueError("customer quote validity requires both start and end")


class CustomerQuoteRenderView(FrozenCustomerQuoteModel):
    """The only customer-facing values a renderer is allowed to receive."""

    customer_alias: str = Field(min_length=1)
    lines: tuple[CustomerQuoteLine, ...] = Field(min_length=1)
    terms: CustomerQuoteTerms
    disclosures: tuple[str, ...]


class InputReference(FrozenCustomerQuoteModel):
    stage: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    content_fingerprint: str = Field(min_length=1)
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class CustomerQuotePackageSnapshot(FrozenCustomerQuoteModel):
    customer_quote_package_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    package_key: str = Field(min_length=1)
    customer_alias: str = Field(min_length=1)
    product_spec: InputReference
    rfq: InputReference
    cost_scenario: ExecutionDependencySnapshot
    risk_assessment: ExecutionDependencySnapshot
    readiness_approval_request_id: str | None = None
    readiness_approval_fingerprint: str | None = None
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    selected_cost_scenario_id: str | None = None
    lines: tuple[CustomerQuoteLine, ...] = ()
    terms: CustomerQuoteTerms
    disclosures: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    status: CustomerQuoteStatus
    as_of: datetime
    content_fingerprint: str = Field(min_length=1)
    supersedes: str | None = None

    @field_validator("as_of")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class CustomerCommunicationDraft(FrozenCustomerQuoteModel):
    draft_id: str = Field(min_length=1)
    customer_quote_package_id: str = Field(min_length=1)
    customer_quote_package_fingerprint: str = Field(min_length=1)
    template_fingerprint: str = Field(min_length=1)
    quote_artifact_ref: str = Field(min_length=1)
    communication_artifact_ref: str = Field(min_length=1)
    recipient_reference: str = Field(min_length=1)
    channel_intent: str = "local_draft"
    content_hash: str = Field(min_length=1)
    disposition: CustomerDraftDisposition = CustomerDraftDisposition.RENDERED_NOT_SENT
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class ExecutionChainNode(FrozenCustomerQuoteModel):
    stage: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    content_fingerprint: str = Field(min_length=1)
    occurred_at: datetime
    predecessor_refs: tuple[str, ...] = ()
    is_lineage_tip: bool

    @field_validator("occurred_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class ExecutionChainManifest(FrozenCustomerQuoteModel):
    manifest_id: str = Field(min_length=1)
    customer_quote_package_id: str = Field(min_length=1)
    customer_quote_package_fingerprint: str = Field(min_length=1)
    nodes: tuple[ExecutionChainNode, ...]
    audit_tip_hash: str = Field(min_length=1)
    as_of: datetime
    content_fingerprint: str = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class ChainVerificationResult(FrozenCustomerQuoteModel):
    valid: bool
    issues: tuple[str, ...] = ()
    verified_at: datetime

    @field_validator("verified_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


def budgetary_no_approval_binding(
    package: CustomerQuotePackageSnapshot,
) -> tuple[str, str]:
    """Return the explicit EP-09 sentinel for a disclosed budgetary-only path."""

    snapshot_id = f"no-approval:{package.customer_quote_package_id}"
    return snapshot_id, stable_fingerprint(
        {
            "stage": "ep09",
            "outcome": "not_required_for_budgetary_only",
            "package": package.content_fingerprint,
        }
    )


def customer_quote_policy_fingerprint(policy: CustomerQuotePolicyVersion) -> str:
    return stable_fingerprint(
        {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "required_disclosures": policy.required_disclosures,
            "required_term_paths": policy.required_term_paths,
            "allow_budgetary_only": policy.allow_budgetary_only,
            "allowed_human_roles": policy.allowed_human_roles,
            "valid_from": policy.valid_from,
            "valid_until": policy.valid_until,
        }
    )
