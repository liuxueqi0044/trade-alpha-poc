"""Frozen, evidence-aware contracts for EP-07 cost and margin scenarios."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_alpha.contracts.common import VersionRef, require_utc
from trade_alpha.execution.contracts.models import MonetaryAmount, ProvenanceRef, require_decimal
from trade_alpha.execution.supplier_quotes.models import QuantityBasis


class FrozenCostModel(BaseModel):
    """Strict immutable base for all persisted EP-07 records."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class CostComponentKind(StrEnum):
    SUPPLIER_PRODUCT = "supplier_product"
    PACKAGING = "packaging"
    DOMESTIC_FREIGHT = "domestic_freight"
    EXPORT_CLEARANCE = "export_clearance"
    INTERNATIONAL_FREIGHT = "international_freight"
    INSURANCE = "insurance"
    IMPORT_DUTY = "import_duty"
    PAYMENT_FEE = "payment_fee"
    INSPECTION_OR_QC = "inspection_or_qc"
    WASTAGE = "wastage"
    RISK_RESERVE = "risk_reserve"
    TOOLING_OR_NRE = "tooling_or_nre"
    SAMPLE = "sample"


class CostInputState(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    INCLUDED_UPSTREAM = "included_upstream"
    EXCLUDED_UPSTREAM = "excluded_upstream"
    QUOTED_SEPARATELY = "quoted_separately"


class CostAllocationBasis(StrEnum):
    PER_UNIT = "per_unit"
    PER_SHIPMENT = "per_shipment"


class CostInput(FrozenCostModel):
    """A supplementary cost fact; it becomes usable only after human decision."""

    cost_input_id: str = Field(min_length=1)
    component_kind: CostComponentKind
    state: CostInputState
    amount: MonetaryAmount | None = None
    quantity_basis: QuantityBasis | None = None
    allocation_basis: CostAllocationBasis = CostAllocationBasis.PER_UNIT
    observed_at: datetime
    provenance_ref: ProvenanceRef
    fx_snapshot_id: str | None = None
    recurring: bool = True
    not_applicable_reason: str | None = None

    @field_validator("observed_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def cost_shape(self) -> CostInput:
        if self.state is CostInputState.KNOWN:
            if self.amount is None or self.quantity_basis is None:
                raise ValueError("known cost input requires an amount and quantity basis")
        elif self.amount is not None or self.quantity_basis is not None:
            raise ValueError("non-known cost input must not carry an amount or quantity basis")
        if self.state is CostInputState.NOT_APPLICABLE:
            if self.not_applicable_reason is None or not self.not_applicable_reason.strip():
                raise ValueError("not_applicable cost input requires a reason")
        elif self.not_applicable_reason is not None:
            raise ValueError("only not_applicable cost input may carry a reason")
        return self


class CostAssumptionDecision(FrozenCostModel):
    """Append-only human acceptance of one supplementary cost input."""

    decision_id: str = Field(min_length=1)
    cost_input_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    decided_at: datetime
    accepted_value: CostInput
    provenance_ref: ProvenanceRef
    supersedes_decision_id: str | None = None

    @field_validator("decided_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def same_input(self) -> CostAssumptionDecision:
        if self.cost_input_id != self.accepted_value.cost_input_id:
            raise ValueError("cost decision must accept its exact cost input")
        return self


class CostScenarioPolicyVersion(FrozenCostModel):
    """Versioned finite policy; no caller supplied formulas are accepted."""

    cost_scenario_policy_version_id: str = Field(min_length=1)
    product_family_id: str = Field(min_length=1)
    target_currency: str = Field(min_length=3, max_length=3)
    target_margin_rate: Decimal = Field(ge=0, lt=1)
    sale_based_fee_rate: Decimal = Field(default=Decimal("0"), ge=0, lt=1)
    wastage_rate: Decimal = Field(default=Decimal("0"), ge=0, lt=1)
    risk_reserve_rate: Decimal = Field(default=Decimal("0"), ge=0, lt=1)
    required_cost_components: tuple[CostComponentKind, ...] = Field(min_length=1)
    not_applicable_components: tuple[CostComponentKind, ...] = ()
    rounding_display_places: int = Field(default=2, ge=0, le=12)
    published_at: datetime
    rule_version: str = Field(min_length=1)
    config_fingerprint: str = Field(min_length=1)
    schema_version: VersionRef = Field(
        default_factory=lambda: VersionRef(name="trade-alpha.execution.costing", version="1.0.0")
    )

    @field_validator(
        "target_margin_rate",
        "sale_based_fee_rate",
        "wastage_rate",
        "risk_reserve_rate",
        mode="before",
    )
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return require_decimal(value)

    @field_validator("published_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def unique_components(self) -> CostScenarioPolicyVersion:
        if len(self.required_cost_components) != len(set(self.required_cost_components)):
            raise ValueError("required cost component kinds must be unique")
        if CostComponentKind.SUPPLIER_PRODUCT not in self.required_cost_components:
            raise ValueError("supplier product must be a required cost component")
        if len(self.not_applicable_components) != len(set(self.not_applicable_components)):
            raise ValueError("not_applicable policy components must be unique")
        if not set(self.not_applicable_components).issubset(self.required_cost_components):
            raise ValueError("not_applicable components must be part of required cost scope")
        if CostComponentKind.SUPPLIER_PRODUCT in self.not_applicable_components:
            raise ValueError("supplier product cost cannot be not_applicable")
        return self


class ResolvedCostComponent(FrozenCostModel):
    component_kind: CostComponentKind
    state: CostInputState
    per_unit_amount: MonetaryAmount | None = None
    source_cost_input_id: str | None = None
    source_quote_component: str | None = None
    provenance_refs: tuple[ProvenanceRef, ...] = ()
    recurring: bool = True
    calculation_trace: str = Field(min_length=1)

    @model_validator(mode="after")
    def resolved_shape(self) -> ResolvedCostComponent:
        if self.state is CostInputState.KNOWN and self.per_unit_amount is None:
            raise ValueError("known resolved cost requires a per-unit amount")
        if self.state is not CostInputState.KNOWN and self.per_unit_amount is not None:
            raise ValueError("unresolved or included cost must not carry a per-unit amount")
        return self


class CostScenarioSnapshot(FrozenCostModel):
    """Immutable cost conclusion for one quote line and one target quantity."""

    cost_scenario_snapshot_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    supplier_quote_snapshot_id: str = Field(min_length=1)
    supplier_quote_content_fingerprint: str = Field(min_length=1)
    normalization_run_id: str = Field(min_length=1)
    normalization_result_fingerprint: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    supplier_rfq_package_id: str = Field(min_length=1)
    product_spec_version_id: str = Field(min_length=1)
    product_family_id: str = Field(min_length=1)
    rfq_line_id: str = Field(min_length=1)
    target_quantity: QuantityBasis
    cost_scenario_policy_version_id: str = Field(min_length=1)
    policy_fingerprint: str = Field(min_length=1)
    resolved_components: tuple[ResolvedCostComponent, ...]
    unresolved_required_components: tuple[CostComponentKind, ...] = ()
    landed_cost_per_unit: MonetaryAmount | None = None
    landed_cost_display: str | None = None
    minimum_sale_price: MonetaryAmount | None = None
    minimum_sale_price_display: str | None = None
    proposed_sale_price: MonetaryAmount | None = None
    contribution_margin: MonetaryAmount | None = None
    contribution_margin_display: str | None = None
    contribution_margin_rate: Decimal | None = None
    effective_decision_ids: tuple[str, ...] = ()
    as_of: datetime
    content_fingerprint: str = Field(min_length=1)
    supersedes: str | None = None

    @field_validator("as_of")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @field_validator("contribution_margin_rate", mode="before")
    @classmethod
    def decimal_if_present(cls, value: object | None) -> Decimal | None:
        return None if value is None else require_decimal(value)

    @model_validator(mode="after")
    def conclusion_shape(self) -> CostScenarioSnapshot:
        values = (
            self.landed_cost_per_unit,
            self.minimum_sale_price,
            self.contribution_margin,
            self.contribution_margin_rate,
            self.landed_cost_display,
            self.minimum_sale_price_display,
            self.contribution_margin_display,
        )
        if self.unresolved_required_components and any(value is not None for value in values):
            raise ValueError("unresolved required costs cannot produce a cost or margin conclusion")
        return self
