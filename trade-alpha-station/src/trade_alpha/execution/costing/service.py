"""Fail-closed, Decimal-only calculation service for EP-07."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import NAMESPACE_URL, uuid5

from pydantic_extra_types.currency_code import ISO4217

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.contracts.models import MeasuredValue, MonetaryAmount
from trade_alpha.execution.supplier_quotes.models import (
    ComponentType,
    InclusionStatus,
    NormalizedQuoteLine,
    PriceComponent,
    QuantityBasis,
    QuoteNormalizationRun,
    SupplierQuoteSnapshot,
)
from trade_alpha.execution.supplier_quotes.ports import UnitConversionPort

from .models import (
    CostAllocationBasis,
    CostAssumptionDecision,
    CostComponentKind,
    CostInput,
    CostInputState,
    CostScenarioPolicyVersion,
    CostScenarioSnapshot,
    ResolvedCostComponent,
)
from .ports import CostingSourceReader
from .repository import InMemoryCostScenarioRepository

_UPSTREAM_COMPONENTS = {
    ComponentType.PACKAGING: CostComponentKind.PACKAGING,
    ComponentType.DOMESTIC_FREIGHT: CostComponentKind.DOMESTIC_FREIGHT,
    ComponentType.EXPORT_CLEARANCE: CostComponentKind.EXPORT_CLEARANCE,
    ComponentType.INTERNATIONAL_FREIGHT: CostComponentKind.INTERNATIONAL_FREIGHT,
    ComponentType.INSURANCE: CostComponentKind.INSURANCE,
    ComponentType.TAX: CostComponentKind.IMPORT_DUTY,
    ComponentType.INSPECTION_OR_TEST: CostComponentKind.INSPECTION_OR_QC,
    ComponentType.TOOLING_OR_NRE: CostComponentKind.TOOLING_OR_NRE,
    ComponentType.SAMPLE: CostComponentKind.SAMPLE,
}
_POLICY_FORMULA_COMPONENTS = {
    CostComponentKind.WASTAGE,
    CostComponentKind.RISK_RESERVE,
    CostComponentKind.PAYMENT_FEE,
}


def policy_content_fingerprint(policy: CostScenarioPolicyVersion) -> str:
    """Recompute policy identity, excluding only the caller-supplied assertion."""

    return stable_fingerprint(policy.model_dump(exclude={"config_fingerprint"}))


def _effective_decisions(
    decisions: tuple[CostAssumptionDecision, ...],
) -> tuple[CostAssumptionDecision, ...]:
    by_id = {item.decision_id: item for item in decisions}
    superseded: set[str] = set()
    for item in decisions:
        if item.supersedes_decision_id is None:
            continue
        prior = by_id.get(item.supersedes_decision_id)
        if prior is None or prior.cost_input_id != item.cost_input_id:
            raise ValueError("cost decision supersedes must reference the same input")
        superseded.add(prior.decision_id)
    result = tuple(item for item in decisions if item.decision_id not in superseded)
    if len(result) != 1:
        raise ValueError("each supplementary cost requires exactly one effective human decision")
    return result


class CostScenarioService:
    """Build independent, auditable scenarios without selecting a supplier."""

    def __init__(
        self,
        repository: InMemoryCostScenarioRepository,
        sources: CostingSourceReader,
        unit_converter: UnitConversionPort,
    ) -> None:
        self.repository = repository
        self.sources = sources
        self.unit_converter = unit_converter

    def record_decision(self, decision: CostAssumptionDecision) -> CostAssumptionDecision:
        return self.repository.save_decision(decision)

    def build(
        self,
        *,
        quote_snapshot: SupplierQuoteSnapshot,
        normalization_run: QuoteNormalizationRun,
        rfq_line_id: str,
        target_quantity: QuantityBasis,
        policy: CostScenarioPolicyVersion,
        cost_inputs: tuple[CostInput, ...] = (),
        as_of: datetime | None = None,
        proposed_sale_price: MonetaryAmount | None = None,
        supersedes: str | None = None,
    ) -> CostScenarioSnapshot:
        moment = require_utc(as_of or datetime.now(UTC))
        self._verify_upstream(quote_snapshot, normalization_run, policy, moment)
        if (
            proposed_sale_price is not None
            and str(proposed_sale_price.currency) != policy.target_currency
        ):
            raise ValueError("proposed sale price must use the cost policy target currency")
        if proposed_sale_price is not None and proposed_sale_price.amount <= 0:
            raise ValueError("proposed sale price must be positive")
        line = self._find_normalized_line(
            normalization_run, quote_snapshot, rfq_line_id, target_quantity
        )
        if line.currency_converted_price is not None and (
            str(line.currency_converted_price.currency) != policy.target_currency
        ):
            raise ValueError("normalized product cost currency differs from cost policy target")
        if len({item.cost_input_id for item in cost_inputs}) != len(cost_inputs):
            raise ValueError("cost input IDs must be unique")
        if any(item.component_kind is CostComponentKind.SUPPLIER_PRODUCT for item in cost_inputs):
            raise ValueError(
                "supplier product cost can only originate from the EP06 normalization run"
            )
        if any(item.component_kind in _POLICY_FORMULA_COMPONENTS for item in cost_inputs):
            raise ValueError("policy formula components cannot be supplied as ad-hoc costs")

        components: dict[CostComponentKind, ResolvedCostComponent] = {}
        product_provenance = quote_snapshot.confirmed_lines[
            [item.rfq_line_id for item in quote_snapshot.confirmed_lines].index(rfq_line_id)
        ].product_price.provenance_ref
        if line.calculation_value is None or line.currency_converted_price is None:
            components[CostComponentKind.SUPPLIER_PRODUCT] = self._unresolved(
                CostComponentKind.SUPPLIER_PRODUCT,
                CostInputState.UNKNOWN,
                "normalized product price missing",
            )
        else:
            components[CostComponentKind.SUPPLIER_PRODUCT] = ResolvedCostComponent(
                component_kind=CostComponentKind.SUPPLIER_PRODUCT,
                state=CostInputState.KNOWN,
                per_unit_amount=line.currency_converted_price,
                source_quote_component="product",
                provenance_refs=(product_provenance,),
                calculation_trace="EP06 normalized product unit price",
            )

        for upstream in quote_snapshot.confirmed_terms.components:
            kind = _UPSTREAM_COMPONENTS.get(upstream.component_type)
            if kind is None:
                continue
            components[kind] = self._resolve_upstream_component(
                kind, upstream, target_quantity, policy, normalization_run.fx_snapshot_id, moment
            )

        upstream_provenance = (
            product_provenance,
            *(item.provenance_ref for item in quote_snapshot.confirmed_terms.components),
        )
        if any(item.observed_at > moment for item in upstream_provenance):
            raise ValueError("upstream quote cost evidence cannot be after as_of")

        for cost_input in cost_inputs:
            if cost_input.observed_at > moment or cost_input.provenance_ref.observed_at > moment:
                raise ValueError("cost input evidence cannot be after as_of")
            decision = _effective_decisions(
                self.repository.decisions_for(cost_input.cost_input_id, as_of=moment)
            )[0]
            if (
                decision.accepted_value != cost_input
                or decision.provenance_ref != cost_input.provenance_ref
            ):
                raise ValueError(
                    "supplementary cost requires an exact matching human decision and evidence"
                )
            if decision.decided_at < cost_input.observed_at:
                raise ValueError("cost decision cannot predate its evidence")
            if cost_input.state is CostInputState.INCLUDED_UPSTREAM:
                raise ValueError("supplemental cost cannot self-assert included_upstream")
            if (
                cost_input.state is CostInputState.NOT_APPLICABLE
                and cost_input.component_kind not in policy.not_applicable_components
            ):
                raise ValueError("cost policy does not allow this component to be not_applicable")
            existing = components.get(cost_input.component_kind)
            if existing is not None and existing.state in {
                CostInputState.KNOWN,
                CostInputState.INCLUDED_UPSTREAM,
            }:
                raise ValueError("supplementary cost would duplicate an upstream cost boundary")
            components[cost_input.component_kind] = self._resolve_input(
                cost_input, target_quantity, policy, moment
            )

        # The only formula-generated costs are finite Decimal policy rates applied
        # to the resolved direct recurrent base.  No user expression is evaluated.
        direct_total = sum(
            (
                item.per_unit_amount.amount
                for item in components.values()
                if item.recurring and item.per_unit_amount is not None
            ),
            Decimal("0"),
        )
        for kind, rate in (
            (CostComponentKind.WASTAGE, policy.wastage_rate),
            (CostComponentKind.RISK_RESERVE, policy.risk_reserve_rate),
        ):
            components[kind] = ResolvedCostComponent(
                component_kind=kind,
                state=CostInputState.KNOWN,
                per_unit_amount=MonetaryAmount(
                    amount=direct_total * rate,
                    currency=cast(ISO4217, policy.target_currency),
                ),
                recurring=True,
                calculation_trace=(
                    f"policy {kind.value} rate {rate} applied to direct recurrent cost"
                ),
            )
        components[CostComponentKind.PAYMENT_FEE] = ResolvedCostComponent(
            component_kind=CostComponentKind.PAYMENT_FEE,
            state=CostInputState.INCLUDED_UPSTREAM,
            recurring=True,
            calculation_trace=f"policy sale-based payment fee rate {policy.sale_based_fee_rate}",
        )

        unresolved = tuple(
            sorted(
                (
                    kind
                    for kind in policy.required_cost_components
                    if kind not in components
                    or components[kind].state
                    in {
                        CostInputState.UNKNOWN,
                        CostInputState.EXCLUDED_UPSTREAM,
                        CostInputState.QUOTED_SEPARATELY,
                    }
                    or (
                        components[kind].state is CostInputState.NOT_APPLICABLE
                        and kind not in policy.not_applicable_components
                    )
                ),
                key=lambda item: item.value,
            )
        )
        landed: MonetaryAmount | None = None
        landed_display: str | None = None
        minimum: MonetaryAmount | None = None
        minimum_display: str | None = None
        contribution: MonetaryAmount | None = None
        contribution_display: str | None = None
        contribution_rate: Decimal | None = None
        if not unresolved:
            landed_value = sum(
                (
                    item.per_unit_amount.amount
                    for item in components.values()
                    if item.recurring and item.per_unit_amount is not None
                ),
                Decimal("0"),
            )
            landed = MonetaryAmount(
                amount=landed_value, currency=cast(ISO4217, policy.target_currency)
            )
            landed_display = self._display(landed, target_quantity, policy)
            denominator = Decimal("1") - policy.target_margin_rate - policy.sale_based_fee_rate
            if denominator <= 0:
                raise ValueError("target margin and sale-based fee rate leave no price denominator")
            minimum = MonetaryAmount(
                amount=landed_value / denominator,
                currency=cast(ISO4217, policy.target_currency),
            )
            minimum_display = self._display(minimum, target_quantity, policy)
            if proposed_sale_price is not None:
                fee = proposed_sale_price.amount * policy.sale_based_fee_rate
                contribution = MonetaryAmount(
                    amount=proposed_sale_price.amount - landed_value - fee,
                    currency=cast(ISO4217, policy.target_currency),
                )
                contribution_rate = contribution.amount / proposed_sale_price.amount
                contribution_display = self._display(contribution, target_quantity, policy)

        ordered_components = tuple(
            sorted(components.values(), key=lambda item: item.component_kind.value)
        )
        scenario_key = self._scenario_key(quote_snapshot, rfq_line_id, target_quantity)
        effective_ids = tuple(
            sorted(
                _effective_decisions(
                    self.repository.decisions_for(item.cost_input_id, as_of=moment)
                )[0].decision_id
                for item in cost_inputs
            )
        )
        payload = {
            "quote": quote_snapshot.content_fingerprint,
            "normalization": normalization_run.result_fingerprint,
            "line": line,
            "policy": policy.config_fingerprint,
            "inputs": cost_inputs,
            "decisions": effective_ids,
            "as_of": moment,
            "proposed_sale_price": proposed_sale_price,
            "components": ordered_components,
            "unresolved": unresolved,
        }
        fingerprint = stable_fingerprint(payload)
        old = self.repository.snapshots_for_key(scenario_key)
        prior = next((item for item in old if item.content_fingerprint == fingerprint), None)
        if prior is not None:
            return prior
        if old:
            if supersedes is None:
                raise ValueError("a changed cost scenario must supersede the current lineage tip")
            if old[-1].cost_scenario_snapshot_id != supersedes:
                raise ValueError("supersedes must reference the current cost scenario lineage tip")
        elif supersedes is not None:
            raise ValueError("supersedes cannot be supplied for an initial cost scenario")
        snapshot = CostScenarioSnapshot(
            cost_scenario_snapshot_id=str(
                uuid5(NAMESPACE_URL, f"trade-alpha/ep07/scenario/{fingerprint}")
            ),
            version=len(old) + 1,
            supplier_quote_snapshot_id=quote_snapshot.supplier_quote_snapshot_id,
            supplier_quote_content_fingerprint=quote_snapshot.content_fingerprint,
            normalization_run_id=normalization_run.normalization_run_id,
            normalization_result_fingerprint=normalization_run.result_fingerprint,
            supplier_id=quote_snapshot.supplier_id,
            supplier_rfq_package_id=quote_snapshot.supplier_rfq_package_id,
            product_spec_version_id=quote_snapshot.product_spec_version_id,
            product_family_id=policy.product_family_id,
            rfq_line_id=rfq_line_id,
            target_quantity=target_quantity,
            cost_scenario_policy_version_id=policy.cost_scenario_policy_version_id,
            policy_fingerprint=policy.config_fingerprint,
            resolved_components=ordered_components,
            unresolved_required_components=unresolved,
            landed_cost_per_unit=landed,
            landed_cost_display=landed_display,
            minimum_sale_price=minimum,
            minimum_sale_price_display=minimum_display,
            proposed_sale_price=proposed_sale_price,
            contribution_margin=contribution,
            contribution_margin_display=contribution_display,
            contribution_margin_rate=contribution_rate,
            effective_decision_ids=effective_ids,
            as_of=moment,
            content_fingerprint=fingerprint,
            supersedes=supersedes,
        )
        return self.repository.save_snapshot(snapshot, scenario_key=scenario_key)

    def _verify_upstream(
        self,
        quote: SupplierQuoteSnapshot,
        run: QuoteNormalizationRun,
        policy: CostScenarioPolicyVersion,
        as_of: datetime,
    ) -> None:
        if type(policy) is not CostScenarioPolicyVersion:
            raise TypeError("cost policy has an invalid type")
        CostScenarioPolicyVersion.model_validate(policy.model_dump())
        saved_quote = self.sources.quote_snapshot(quote.supplier_quote_snapshot_id)
        if (
            saved_quote != quote
            or saved_quote is None
            or saved_quote.content_fingerprint != quote.content_fingerprint
        ):
            raise ValueError("costing requires a repository-saved immutable quote snapshot")
        saved_run = self.sources.normalization_run(run.normalization_run_id)
        if (
            saved_run != run
            or saved_run is None
            or saved_run.result_fingerprint != run.result_fingerprint
        ):
            raise ValueError("costing requires a repository-saved immutable normalization run")
        if policy.config_fingerprint != policy_content_fingerprint(policy):
            raise ValueError("cost policy config_fingerprint does not match its canonical content")
        if (
            self.sources.product_family_id(quote.product_spec_version_id)
            != policy.product_family_id
        ):
            raise ValueError("cost policy product family does not match the saved specification")
        if quote.received_at > as_of or quote.confirmed_at > as_of:
            raise ValueError("quote snapshot cannot be after cost scenario as_of")
        if quote.validity_as_of(as_of) != "valid":
            raise ValueError("quote must be valid at cost scenario as_of")
        if run.as_of != as_of:
            raise ValueError("normalization as_of must equal cost scenario as_of")
        if policy.published_at > as_of:
            raise ValueError("cost policy cannot be after as_of")
        if quote.supplier_quote_snapshot_id not in run.quote_snapshot_ids:
            raise ValueError("normalization run does not cover the exact quote snapshot")

    @staticmethod
    def _find_normalized_line(
        run: QuoteNormalizationRun,
        quote: SupplierQuoteSnapshot,
        rfq_line_id: str,
        target_quantity: QuantityBasis,
    ) -> NormalizedQuoteLine:
        found = tuple(
            item
            for item in run.normalized_lines
            if item.supplier_quote_snapshot_id == quote.supplier_quote_snapshot_id
            and item.rfq_line_id == rfq_line_id
            and item.quantity_tier == target_quantity
        )
        if len(found) != 1:
            raise ValueError(
                "normalization run must contain exactly one matching quote line and quantity tier"
            )
        return found[0]

    def _resolve_upstream_component(
        self,
        kind: CostComponentKind,
        component: PriceComponent,
        target_quantity: QuantityBasis,
        policy: CostScenarioPolicyVersion,
        fx_snapshot_id: str | None,
        as_of: datetime,
    ) -> ResolvedCostComponent:
        if component.inclusion_status is InclusionStatus.INCLUDED:
            return ResolvedCostComponent(
                component_kind=kind,
                state=CostInputState.INCLUDED_UPSTREAM,
                source_quote_component=component.component_type.value,
                provenance_refs=(component.provenance_ref,),
                calculation_trace="included in confirmed upstream quote price",
            )
        if component.inclusion_status is InclusionStatus.NOT_APPLICABLE:
            return ResolvedCostComponent(
                component_kind=kind,
                state=CostInputState.NOT_APPLICABLE,
                source_quote_component=component.component_type.value,
                provenance_refs=(component.provenance_ref,),
                calculation_trace="not applicable according to confirmed upstream quote",
            )
        if (
            component.inclusion_status is InclusionStatus.QUOTED_SEPARATELY
            and component.amount is not None
        ):
            if component.quantity_basis is None:
                return self._unresolved(
                    kind,
                    CostInputState.QUOTED_SEPARATELY,
                    "separate quote has no quantity basis",
                )
            amount = self._per_unit_amount(
                component.amount,
                component.quantity_basis,
                CostAllocationBasis.PER_UNIT,
                target_quantity,
                policy,
                fx_snapshot_id,
                as_of,
            )
            return ResolvedCostComponent(
                component_kind=kind,
                state=CostInputState.KNOWN,
                per_unit_amount=amount,
                source_quote_component=component.component_type.value,
                provenance_refs=(component.provenance_ref,),
                calculation_trace="separately quoted upstream component normalized per target unit",
            )
        state = (
            CostInputState.QUOTED_SEPARATELY
            if component.inclusion_status is InclusionStatus.QUOTED_SEPARATELY
            else CostInputState.EXCLUDED_UPSTREAM
            if component.inclusion_status
            in {InclusionStatus.EXCLUDED, InclusionStatus.BUYER_SUPPLIED}
            else CostInputState.UNKNOWN
        )
        return self._unresolved(kind, state, "upstream quote did not resolve this cost boundary")

    def _resolve_input(
        self,
        value: CostInput,
        target_quantity: QuantityBasis,
        policy: CostScenarioPolicyVersion,
        as_of: datetime,
    ) -> ResolvedCostComponent:
        if value.state is not CostInputState.KNOWN:
            return self._unresolved(
                value.component_kind,
                value.state,
                "human-reviewed supplementary input is unresolved",
            )
        assert value.amount is not None and value.quantity_basis is not None
        amount = self._per_unit_amount(
            value.amount,
            value.quantity_basis,
            value.allocation_basis,
            target_quantity,
            policy,
            value.fx_snapshot_id,
            as_of,
        )
        return ResolvedCostComponent(
            component_kind=value.component_kind,
            state=CostInputState.KNOWN,
            per_unit_amount=amount,
            source_cost_input_id=value.cost_input_id,
            provenance_refs=(value.provenance_ref,),
            recurring=value.recurring,
            calculation_trace=f"human-decided {value.allocation_basis.value} supplementary cost",
        )

    def _per_unit_amount(
        self,
        amount: MonetaryAmount,
        basis: QuantityBasis,
        allocation: CostAllocationBasis,
        target_quantity: QuantityBasis,
        policy: CostScenarioPolicyVersion,
        fx_snapshot_id: str | None,
        as_of: datetime,
    ) -> MonetaryAmount:
        converted_basis = self.unit_converter.convert(
            MeasuredValue(amount=basis.amount, unit=basis.unit), to_unit=target_quantity.unit
        )
        if (
            allocation is CostAllocationBasis.PER_SHIPMENT
            and converted_basis.amount != target_quantity.amount
        ):
            raise ValueError("per-shipment cost quantity basis must equal the target quantity")
        if str(amount.currency) == policy.target_currency:
            converted = amount
        else:
            if not fx_snapshot_id:
                raise ValueError("cross-currency cost requires an exact FX snapshot")
            fx = self.sources.fx_snapshot(fx_snapshot_id)
            if fx is None:
                raise ValueError("cross-currency cost requires an exact FX snapshot")
            if fx.observed_at > as_of:
                raise ValueError("FX snapshot cannot be after as_of")
            if (
                str(fx.from_currency) != str(amount.currency)
                or str(fx.to_currency) != policy.target_currency
            ):
                raise ValueError("FX snapshot direction does not match supplementary cost")
            converted = fx.convert(amount, to_currency=cast(ISO4217, policy.target_currency))
        return MonetaryAmount(
            amount=converted.amount / converted_basis.amount,
            currency=cast(ISO4217, policy.target_currency),
        )

    @staticmethod
    def _unresolved(
        kind: CostComponentKind, state: CostInputState, trace: str
    ) -> ResolvedCostComponent:
        return ResolvedCostComponent(component_kind=kind, state=state, calculation_trace=trace)

    @staticmethod
    def _display(
        amount: MonetaryAmount,
        target_quantity: QuantityBasis,
        policy: CostScenarioPolicyVersion,
    ) -> str:
        rounded = amount.amount.quantize(Decimal(1).scaleb(-policy.rounding_display_places))
        return f"{rounded} {amount.currency}/{target_quantity.unit}"

    @staticmethod
    def _scenario_key(
        quote: SupplierQuoteSnapshot,
        rfq_line_id: str,
        target_quantity: QuantityBasis,
    ) -> str:
        return ":".join(
            (
                quote.supplier_quote_snapshot_id,
                rfq_line_id,
                str(target_quantity.amount),
                target_quantity.unit,
            )
        )
