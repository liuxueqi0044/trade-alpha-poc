"""Decimal-only, evidence-preserving quote normalization."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast

from pydantic_extra_types.currency_code import ISO4217

from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.contracts.models import MeasuredValue, MonetaryAmount

from .models import (
    ComparabilityStatus,
    ComponentType,
    ConversionTrace,
    InclusionStatus,
    NormalizedQuoteLine,
    QuantityBasis,
    QuoteNormalizationPolicyVersion,
    SupplierQuoteSnapshot,
)
from .ports import FxSnapshotReader, UnitConversionPort


def policy_content_fingerprint(policy: QuoteNormalizationPolicyVersion) -> str:
    """Compute policy identity from every operative field, never caller assertion."""

    return stable_fingerprint(policy.model_dump(exclude={"config_fingerprint"}))


def _unit_dimension(unit: str) -> str:
    if unit in {"piece", "count", "unit"}:
        return "count"
    if unit in {"mm", "cm", "m", "km", "inch", "in", "ft"}:
        return "length"
    if unit in {"g", "kg", "tonne", "lb", "oz"}:
        return "mass"
    if unit in {"ml", "l", "m3"}:
        return "volume"
    return "unknown"


def normalize_line(
    snapshot: SupplierQuoteSnapshot,
    line_index: int,
    *,
    policy: QuoteNormalizationPolicyVersion,
    unit_converter: UnitConversionPort,
    fx_reader: FxSnapshotReader | None,
    fx_snapshot_id: str | None,
    target_quantity_tier: QuantityBasis,
    as_of: datetime,
) -> tuple[NormalizedQuoteLine, tuple[str, ...]]:
    """Normalize one line without giving a numeric meaning to missing terms."""

    line = snapshot.confirmed_lines[line_index]
    component = line.product_price
    states = tuple(
        sorted(
            (
                (item.component_type, item.inclusion_status)
                for item in snapshot.confirmed_terms.components
            ),
            key=lambda item: item[0].value,
        )
    )
    if component.amount is None or component.quantity_basis is None:
        return _empty_line(
            snapshot,
            line.rfq_line_id,
            line.quantity_basis,
            states,
            "AMBIGUOUS_PRICE_BASIS",
            target_quantity_tier,
        )
    source_basis = component.quantity_basis
    dimension = _unit_dimension(source_basis.unit)
    if dimension not in set(policy.allowed_unit_conversion_dimensions):
        return _empty_line(
            snapshot,
            line.rfq_line_id,
            source_basis,
            states,
            "UNIT_CONVERSION_NOT_ALLOWED",
            target_quantity_tier,
        )
    try:
        converted_basis_value = unit_converter.convert(
            MeasuredValue(amount=source_basis.amount, unit=source_basis.unit),
            to_unit=policy.target_measurement_unit,
        )
    except ValueError:
        return _empty_line(
            snapshot,
            line.rfq_line_id,
            source_basis,
            states,
            "AMBIGUOUS_PRICE_BASIS",
            target_quantity_tier,
        )
    # The amount applies to the complete converted basis: CNY 1,000 / 100
    # pieces is CNY 10 / piece, never CNY 1,000 / piece.
    unit_factor = Decimal("1") / converted_basis_value.amount
    unit_price = MonetaryAmount(
        amount=component.amount.amount * unit_factor, currency=component.amount.currency
    )
    currency_price: MonetaryAmount | None = unit_price
    fx_rate: Decimal | None = None
    fx_snapshot = None
    issues: list[str] = []
    quantity_status = ComparabilityStatus.COMPARABLE
    if (
        component.applies_to_quantity_minimum is not None
        and target_quantity_tier.amount < component.applies_to_quantity_minimum
    ) or (
        component.applies_to_quantity_maximum is not None
        and target_quantity_tier.amount > component.applies_to_quantity_maximum
    ):
        issues.append("QUANTITY_TIER_NOT_COVERED")
        quantity_status = ComparabilityStatus.NOT_COMPARABLE
    if line.moq is not None:
        try:
            target_as_moq_unit = unit_converter.convert(
                MeasuredValue(amount=target_quantity_tier.amount, unit=target_quantity_tier.unit),
                to_unit=line.moq.unit,
            )
        except ValueError:
            issues.append("QUANTITY_TIER_NOT_COVERED")
            quantity_status = ComparabilityStatus.NOT_COMPARABLE
        else:
            if target_as_moq_unit.amount < line.moq.amount:
                issues.append("MOQ_EXCEEDS_TARGET")
                quantity_status = ComparabilityStatus.NOT_COMPARABLE
    direction = "identity"
    if str(unit_price.currency) != policy.target_currency:
        fx_snapshot = fx_reader.get(fx_snapshot_id) if fx_reader and fx_snapshot_id else None
        if fx_snapshot is None:
            currency_price = None
            issues.append("FX_SNAPSHOT_MISSING")
        elif (
            str(fx_snapshot.from_currency) == str(unit_price.currency)
            and str(fx_snapshot.to_currency) == policy.target_currency
        ):
            currency_price = fx_snapshot.convert(
                unit_price, to_currency=cast(ISO4217, policy.target_currency)
            )
            fx_rate = fx_snapshot.rate
            direction = "source_to_target"
        elif (
            policy.allow_reciprocal_fx
            and str(fx_snapshot.to_currency) == str(unit_price.currency)
            and str(fx_snapshot.from_currency) == policy.target_currency
        ):
            reciprocal = Decimal("1") / fx_snapshot.rate
            currency_price = MonetaryAmount(
                amount=unit_price.amount * reciprocal,
                currency=cast(ISO4217, policy.target_currency),
            )
            fx_rate = reciprocal
            direction = "reciprocal"
        else:
            currency_price = None
            issues.append("FX_SNAPSHOT_MISSING")
        if fx_snapshot is not None and fx_snapshot.observed_at > as_of:
            currency_price = None
            fx_rate = None
            issues.append("FX_SNAPSHOT_AFTER_AS_OF")
    trace = ConversionTrace(
        source_unit=source_basis.unit,
        target_unit=policy.target_measurement_unit,
        unit_factor=unit_factor,
        source_currency=str(component.amount.currency),
        target_currency=policy.target_currency,
        fx_rate=fx_rate,
        direction=direction,
        rate_timestamp=fx_snapshot.observed_at if fx_snapshot else None,
        provider=fx_snapshot.source if fx_snapshot else None,
        rule_version=policy.rule_version,
        fx_snapshot_id=fx_snapshot_id if fx_snapshot else None,
    )
    calculation = currency_price.amount if currency_price is not None else None
    display = None
    if calculation is not None:
        display = f"{calculation.quantize(Decimal(1).scaleb(-policy.rounding_display_places))} {policy.target_currency}/{policy.target_measurement_unit}"  # noqa: E501
    normalized = NormalizedQuoteLine(
        supplier_quote_snapshot_id=snapshot.supplier_quote_snapshot_id,
        rfq_line_id=line.rfq_line_id,
        original_amount=component.amount,
        original_quantity_basis=source_basis,
        unit_converted_price=unit_price,
        currency_converted_price=currency_price,
        conversion_trace=trace,
        quantity_tier=target_quantity_tier,
        component_inclusion_states=states,
        spec_deviation_refs=line.deviations,
        dimension_comparability=(("quantity", quantity_status),),
        calculation_value=calculation,
        display_value=display,
    )
    return normalized, tuple(issues)


def _empty_line(
    snapshot: SupplierQuoteSnapshot,
    rfq_line_id: str,
    basis: QuantityBasis,
    states: tuple[tuple[ComponentType, InclusionStatus], ...],
    issue: str,
    target_quantity_tier: QuantityBasis,
) -> tuple[NormalizedQuoteLine, tuple[str, ...]]:
    dimension = (
        "quantity"
        if issue in {"AMBIGUOUS_PRICE_BASIS", "UNIT_CONVERSION_NOT_ALLOWED"}
        else "currency"
    )
    status = (
        ComparabilityStatus.NOT_COMPARABLE
        if dimension == "quantity"
        else ComparabilityStatus.UNKNOWN
    )
    return (
        NormalizedQuoteLine(
            supplier_quote_snapshot_id=snapshot.supplier_quote_snapshot_id,
            rfq_line_id=rfq_line_id,
            original_quantity_basis=basis,
            quantity_tier=target_quantity_tier,
            component_inclusion_states=states,
            dimension_comparability=((dimension, status),),
        ),
        (issue,),
    )


def normalization_fingerprint(
    snapshots: tuple[SupplierQuoteSnapshot, ...],
    policy: QuoteNormalizationPolicyVersion,
    as_of: object,
    fx_snapshot_id: str | None,
) -> str:
    return stable_fingerprint(
        {
            "snapshot_fingerprints": [item.content_fingerprint for item in snapshots],
            "policy": policy_content_fingerprint(policy),
            "as_of": str(as_of),
            "fx_snapshot_id": fx_snapshot_id,
        }
    )
