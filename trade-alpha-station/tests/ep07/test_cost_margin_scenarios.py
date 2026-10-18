from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_alpha.contracts.common import EvidenceLocator, VersionRef
from trade_alpha.execution.adapters.pint_units import PintUnitConverter
from trade_alpha.execution.contracts import Incoterm, MonetaryAmount, ProvenanceRef
from trade_alpha.execution.contracts.models import FxSnapshot
from trade_alpha.execution.costing import (
    CostAllocationBasis,
    CostAssumptionDecision,
    CostComponentKind,
    CostInput,
    CostInputState,
    CostScenarioPolicyVersion,
    CostScenarioService,
    InMemoryCostingSourceReader,
    InMemoryCostScenarioRepository,
    policy_content_fingerprint,
)
from trade_alpha.execution.supplier_quotes import (
    ComponentType,
    ConfirmedQuoteLine,
    InclusionStatus,
    NormalizedQuoteLine,
    PriceComponent,
    QuantityBasis,
    QuoteNormalizationRun,
    QuoteTerm,
    SupplierQuoteSnapshot,
)

NOW = datetime(2026, 8, 27, tzinfo=UTC)
TARGET = QuantityBasis(amount=Decimal("100"), unit="piece")


def provenance(artifact_id: str = "quote-artifact") -> ProvenanceRef:
    return ProvenanceRef(
        source="fixture",
        evidence=EvidenceLocator(artifact_id=artifact_id, block_ids=("block-1",)),
        observed_at=NOW,
        source_version=VersionRef(name="fixture", version="1.0.0"),
    )


def policy(
    required: tuple[CostComponentKind, ...] = (
        CostComponentKind.SUPPLIER_PRODUCT,
        CostComponentKind.PACKAGING,
        CostComponentKind.DOMESTIC_FREIGHT,
    ),
    not_applicable: tuple[CostComponentKind, ...] = (),
) -> CostScenarioPolicyVersion:
    draft = CostScenarioPolicyVersion(
        cost_scenario_policy_version_id="cost-policy-v1",
        product_family_id="FE-DRY-01",
        target_currency="CNY",
        target_margin_rate=Decimal("0.20"),
        sale_based_fee_rate=Decimal("0.03"),
        wastage_rate=Decimal("0.10"),
        risk_reserve_rate=Decimal("0.05"),
        required_cost_components=required,
        not_applicable_components=not_applicable,
        published_at=NOW,
        rule_version="1.0.0",
        config_fingerprint="sha256:placeholder",
    )
    return draft.model_copy(update={"config_fingerprint": policy_content_fingerprint(draft)})


def quote(*, packaging: PriceComponent | None = None) -> SupplierQuoteSnapshot:
    evidence = provenance()
    product = PriceComponent(
        component_type=ComponentType.PRODUCT,
        inclusion_status=InclusionStatus.INCLUDED,
        amount=MonetaryAmount(amount=Decimal("10"), currency="CNY"),
        quantity_basis=QuantityBasis(amount=Decimal("1"), unit="piece"),
        provenance_ref=evidence,
    )
    line = ConfirmedQuoteLine(
        rfq_line_id="line-1",
        supplier_line_ref="supplier-line-1",
        quantity_basis=QuantityBasis(amount=Decimal("1"), unit="piece"),
        product_price=product,
        provenance_refs=(evidence,),
    )
    return SupplierQuoteSnapshot(
        supplier_quote_snapshot_id="quote-snapshot-1",
        supplier_quote_id="quote-1",
        version=1,
        supplier_id="supplier-a",
        supplier_profile_version_id="supplier-a-v1",
        supplier_rfq_package_id="package-a",
        product_spec_version_id="spec-v1",
        confirmed_lines=(line,),
        confirmed_terms=QuoteTerm(
            currency="CNY",
            incoterm=Incoterm(code="EXW", rules_version="2020", named_place="Factory"),
            quote_valid_until=NOW + timedelta(days=1),
            components=(packaging,) if packaging else (),
        ),
        evidence_manifest=("quote-artifact",),
        effective_decision_ids=("quote-decision",),
        quote_validity_status="valid",
        received_at=NOW,
        confirmed_at=NOW,
        content_fingerprint="sha256:quote-content",
    )


def run(
    snapshot: SupplierQuoteSnapshot,
    *,
    calculation: Decimal | None = Decimal("10"),
) -> QuoteNormalizationRun:
    line = NormalizedQuoteLine(
        supplier_quote_snapshot_id=snapshot.supplier_quote_snapshot_id,
        rfq_line_id="line-1",
        original_amount=MonetaryAmount(amount=Decimal("10"), currency="CNY"),
        original_quantity_basis=QuantityBasis(amount=Decimal("1"), unit="piece"),
        currency_converted_price=(
            MonetaryAmount(amount=calculation, currency="CNY") if calculation is not None else None
        ),
        quantity_tier=TARGET,
        component_inclusion_states=(),
        calculation_value=calculation,
    )
    return QuoteNormalizationRun(
        normalization_run_id="normalization-1",
        quote_snapshot_ids=(snapshot.supplier_quote_snapshot_id,),
        normalization_policy_version_id="normalization-policy-v1",
        as_of=NOW,
        normalized_lines=(line,),
        unit_adapter_version="fixture/1.0.0",
        input_fingerprint="sha256:normalization-input",
        result_fingerprint="sha256:normalization-result",
    )


def services(snapshot: SupplierQuoteSnapshot, normalized: QuoteNormalizationRun):
    sources = InMemoryCostingSourceReader()
    sources.save_quote_snapshot(snapshot)
    sources.save_normalization_run(normalized)
    sources.save_product_family(snapshot.product_spec_version_id, "FE-DRY-01")
    repository = InMemoryCostScenarioRepository()
    return repository, sources, CostScenarioService(repository, sources, PintUnitConverter())


def input_cost(
    *,
    cost_input_id: str = "domestic-freight-1",
    state: CostInputState = CostInputState.KNOWN,
    currency: str = "CNY",
    amount: Decimal = Decimal("100"),
    quantity: QuantityBasis | None = TARGET,
    allocation: CostAllocationBasis = CostAllocationBasis.PER_SHIPMENT,
    fx_snapshot_id: str | None = None,
) -> CostInput:
    return CostInput(
        cost_input_id=cost_input_id,
        component_kind=CostComponentKind.DOMESTIC_FREIGHT,
        state=state,
        amount=(
            MonetaryAmount(amount=amount, currency=currency)
            if state is CostInputState.KNOWN
            else None
        ),
        quantity_basis=quantity if state is CostInputState.KNOWN else None,
        allocation_basis=allocation,
        observed_at=NOW,
        provenance_ref=provenance("freight-artifact"),
        fx_snapshot_id=fx_snapshot_id,
    )


def decide(service: CostScenarioService, value: CostInput, *, at: datetime = NOW) -> None:
    service.record_decision(
        CostAssumptionDecision(
            decision_id=f"decision-{value.cost_input_id}",
            cost_input_id=value.cost_input_id,
            actor_id="reviewer",
            rationale="reviewed fixture cost evidence",
            decided_at=at,
            accepted_value=value,
            provenance_ref=value.provenance_ref,
        )
    )


def separately_quoted_packaging(*, amount: Decimal | None = Decimal("200")) -> PriceComponent:
    return PriceComponent(
        component_type=ComponentType.PACKAGING,
        inclusion_status=InclusionStatus.QUOTED_SEPARATELY,
        amount=MonetaryAmount(amount=amount, currency="CNY") if amount is not None else None,
        quantity_basis=QuantityBasis(amount=Decimal("100"), unit="piece")
        if amount is not None
        else None,
        provenance_ref=provenance(),
    )


def test_ep07_ac_002_004_005_009_traceable_cost_and_margin_formula() -> None:
    snapshot = quote(packaging=separately_quoted_packaging())
    normalized = run(snapshot)
    repository, _sources, service = services(snapshot, normalized)
    freight = input_cost()
    decide(service, freight)
    result = service.build(
        quote_snapshot=snapshot,
        normalization_run=normalized,
        rfq_line_id="line-1",
        target_quantity=TARGET,
        policy=policy(),
        cost_inputs=(freight,),
        as_of=NOW,
        proposed_sale_price=MonetaryAmount(amount=Decimal("20"), currency="CNY"),
    )
    # Product 10 + packaging 2 + shipment freight 1, then 10% wastage + 5% reserve.
    assert result.landed_cost_per_unit == MonetaryAmount(amount=Decimal("14.95"), currency="CNY")
    assert result.minimum_sale_price == MonetaryAmount(
        amount=Decimal("14.95") / Decimal("0.77"), currency="CNY"
    )
    assert result.contribution_margin == MonetaryAmount(amount=Decimal("4.45"), currency="CNY")
    assert result.contribution_margin_rate == Decimal("0.2225")
    assert {item.component_kind for item in result.resolved_components} >= {
        CostComponentKind.SUPPLIER_PRODUCT,
        CostComponentKind.PACKAGING,
        CostComponentKind.DOMESTIC_FREIGHT,
    }


def test_ep07_ac_001_rejects_float_and_missing_fx() -> None:
    with pytest.raises(ValueError, match="binary float"):
        CostScenarioPolicyVersion(
            cost_scenario_policy_version_id="bad",
            product_family_id="family",
            target_currency="CNY",
            target_margin_rate=0.2,
            required_cost_components=(CostComponentKind.SUPPLIER_PRODUCT,),
            published_at=NOW,
            rule_version="1",
            config_fingerprint="x",
        )
    snapshot = quote(packaging=separately_quoted_packaging())
    normalized = run(snapshot)
    _repository, _sources, service = services(snapshot, normalized)
    usd_freight = input_cost(currency="USD", fx_snapshot_id=None)
    decide(service, usd_freight)
    with pytest.raises(ValueError, match="exact FX snapshot"):
        service.build(
            quote_snapshot=snapshot,
            normalization_run=normalized,
            rfq_line_id="line-1",
            target_quantity=TARGET,
            policy=policy(),
            cost_inputs=(usd_freight,),
            as_of=NOW,
        )
    wrong_shipment_basis = input_cost(
        cost_input_id="domestic-freight-2",
        quantity=QuantityBasis(amount=Decimal("99"), unit="piece")
    )
    decide(service, wrong_shipment_basis)
    with pytest.raises(ValueError, match="must equal the target quantity"):
        service.build(
            quote_snapshot=snapshot,
            normalization_run=normalized,
            rfq_line_id="line-1",
            target_quantity=TARGET,
            policy=policy(),
            cost_inputs=(wrong_shipment_basis,),
            as_of=NOW,
        )


def test_ep07_ac_003_004_unknown_costs_never_become_zero() -> None:
    snapshot = quote(packaging=separately_quoted_packaging(amount=None))
    normalized = run(snapshot)
    _repository, _sources, service = services(snapshot, normalized)
    unknown = input_cost(state=CostInputState.UNKNOWN)
    decide(service, unknown)
    result = service.build(
        quote_snapshot=snapshot,
        normalization_run=normalized,
        rfq_line_id="line-1",
        target_quantity=TARGET,
        policy=policy(),
        cost_inputs=(unknown,),
        as_of=NOW,
    )
    assert result.landed_cost_per_unit is None
    assert result.minimum_sale_price is None
    assert result.contribution_margin is None
    assert set(result.unresolved_required_components) == {
        CostComponentKind.PACKAGING,
        CostComponentKind.DOMESTIC_FREIGHT,
    }


def test_ep07_ac_006_007_saved_pit_and_decision_guards() -> None:
    snapshot = quote(packaging=separately_quoted_packaging())
    normalized = run(snapshot)
    repository, sources, service = services(snapshot, normalized)
    freight = input_cost()
    with pytest.raises(ValueError, match="exactly one effective human decision"):
        service.build(
            quote_snapshot=snapshot,
            normalization_run=normalized,
            rfq_line_id="line-1",
            target_quantity=TARGET,
            policy=policy(),
            cost_inputs=(freight,),
            as_of=NOW,
        )
    decide(service, freight, at=NOW + timedelta(seconds=1))
    with pytest.raises(ValueError, match="exactly one effective human decision"):
        service.build(
            quote_snapshot=snapshot,
            normalization_run=normalized,
            rfq_line_id="line-1",
            target_quantity=TARGET,
            policy=policy(),
            cost_inputs=(freight,),
            as_of=NOW,
        )
    assert repository.decisions_for(freight.cost_input_id, as_of=NOW) == ()
    future_policy = policy().model_copy(update={"published_at": NOW + timedelta(days=1)})
    future_policy = future_policy.model_copy(
        update={"config_fingerprint": policy_content_fingerprint(future_policy)}
    )
    with pytest.raises(ValueError, match="policy cannot be after"):
        service.build(
            quote_snapshot=snapshot,
            normalization_run=normalized,
            rfq_line_id="line-1",
            target_quantity=TARGET,
            policy=future_policy,
            as_of=NOW,
        )
    forged = snapshot.model_copy(update={"supplier_id": "forged"})
    with pytest.raises(ValueError, match="repository-saved"):
        service.build(
            quote_snapshot=forged,
            normalization_run=normalized,
            rfq_line_id="line-1",
            target_quantity=TARGET,
            policy=policy(),
            as_of=NOW,
        )
    sources.save_fx_snapshot(
        "future-fx",
        FxSnapshot(
            from_currency="USD",
            to_currency="CNY",
            rate=Decimal("7.2"),
            observed_at=NOW + timedelta(days=1),
            source="fixture",
            evidence=EvidenceLocator(artifact_id="fx-artifact"),
        ),
    )
    future_fx = input_cost(currency="USD", fx_snapshot_id="future-fx")
    service.record_decision(
        CostAssumptionDecision(
            decision_id="usd-decision",
            cost_input_id=future_fx.cost_input_id,
            actor_id="reviewer",
            rationale="reviewed",
            decided_at=NOW,
            accepted_value=future_fx,
            provenance_ref=future_fx.provenance_ref,
        )
    )
    with pytest.raises(ValueError, match="FX snapshot cannot be after"):
        service.build(
            quote_snapshot=snapshot,
            normalization_run=normalized,
            rfq_line_id="line-1",
            target_quantity=TARGET,
            policy=policy(),
            cost_inputs=(future_fx,),
            as_of=NOW,
        )


def test_ep07_ac_008_idempotency_supersedes_and_no_supplier_selection() -> None:
    snapshot = quote(packaging=separately_quoted_packaging())
    normalized = run(snapshot)
    repository, _sources, service = services(snapshot, normalized)
    freight = input_cost()
    decide(service, freight)
    first = service.build(
        quote_snapshot=snapshot,
        normalization_run=normalized,
        rfq_line_id="line-1",
        target_quantity=TARGET,
        policy=policy(),
        cost_inputs=(freight,),
        as_of=NOW,
    )
    assert (
        service.build(
            quote_snapshot=snapshot,
            normalization_run=normalized,
            rfq_line_id="line-1",
            target_quantity=TARGET,
            policy=policy(),
            cost_inputs=(freight,),
            as_of=NOW,
        )
        == first
    )
    with pytest.raises(ValueError, match="must supersede"):
        service.build(
            quote_snapshot=snapshot,
            normalization_run=normalized,
            rfq_line_id="line-1",
            target_quantity=TARGET,
            policy=policy(),
            cost_inputs=(freight,),
            as_of=NOW,
            proposed_sale_price=MonetaryAmount(amount=Decimal("20"), currency="CNY"),
        )
    second = service.build(
        quote_snapshot=snapshot,
        normalization_run=normalized,
        rfq_line_id="line-1",
        target_quantity=TARGET,
        policy=policy(),
        cost_inputs=(freight,),
        as_of=NOW,
        proposed_sale_price=MonetaryAmount(amount=Decimal("20"), currency="CNY"),
        supersedes=first.cost_scenario_snapshot_id,
    )
    assert second.version == 2 and second.supersedes == first.cost_scenario_snapshot_id
    assert not repository.is_lineage_tip_as_of(first.cost_scenario_snapshot_id, NOW)
    assert repository.is_lineage_tip_as_of(second.cost_scenario_snapshot_id, NOW)
    dumped = second.model_dump(mode="json")
    forbidden = {"winner", "selected_supplier_id", "approved", "send", "po", "payment"}
    assert not forbidden.intersection(dumped)


def test_required_not_applicable_cost_needs_explicit_policy_authorization() -> None:
    snapshot = quote(packaging=separately_quoted_packaging())
    normalized = run(snapshot)
    _repository, _sources, service = services(snapshot, normalized)
    freight = CostInput(
        cost_input_id="freight-na",
        component_kind=CostComponentKind.DOMESTIC_FREIGHT,
        state=CostInputState.NOT_APPLICABLE,
        observed_at=NOW,
        provenance_ref=provenance("freight-na-evidence"),
        not_applicable_reason="customer pickup at supplier site",
    )
    decide(service, freight)
    with pytest.raises(ValueError, match="does not allow"):
        service.build(
            quote_snapshot=snapshot,
            normalization_run=normalized,
            rfq_line_id="line-1",
            target_quantity=TARGET,
            policy=policy(),
            cost_inputs=(freight,),
            as_of=NOW,
        )
    allowed = service.build(
        quote_snapshot=snapshot,
        normalization_run=normalized,
        rfq_line_id="line-1",
        target_quantity=TARGET,
        policy=policy(not_applicable=(CostComponentKind.DOMESTIC_FREIGHT,)),
        cost_inputs=(freight,),
        as_of=NOW,
    )
    assert allowed.landed_cost_per_unit is not None
