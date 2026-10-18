"""Pure claim-value normalization and equivalence grouping."""

from __future__ import annotations

# ruff: noqa: E501, I001

import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from trade_alpha.execution.adapters.pint_units import PintUnitConverter
from trade_alpha.execution.contracts import MeasuredValue
from trade_alpha.execution.contracts.models import require_decimal
from trade_alpha.knowledge import Claim

from .models import FieldDefinition, ValueKind

_QUANTITY = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*([A-Za-z0-9]+)\s*$")


def normalize_claim(claim: Claim, definition: FieldDefinition) -> tuple[Any, Any, Any, str]:
    """Keep raw and typed values while deriving a deterministic canonical value."""

    source = claim.object_value
    raw = source.get("raw", source) if isinstance(source, dict) else source
    value = source.get("typed", raw) if isinstance(source, dict) else raw
    if definition.value_kind is ValueKind.QUANTITY:
        amount, unit = _quantity_parts(value)
        if definition.accepted_units and unit not in definition.accepted_units:
            raise ValueError(f"unit {unit} is not accepted for {definition.key}")
        converted = PintUnitConverter().convert(
            MeasuredValue(amount=amount, unit=unit), to_unit=definition.canonical_unit or unit
        )
        canonical = {"amount": str(converted.amount), "unit": converted.unit}
        return (
            raw,
            {"amount": str(amount), "unit": unit},
            canonical,
            f"{converted.amount} {converted.unit}",
        )
    if definition.value_kind is ValueKind.BOOLEAN:
        parsed = _boolean(value)
        return raw, parsed, parsed, str(parsed).lower()
    if isinstance(value, str):
        normalized = " ".join(value.strip().split()).casefold()
        return raw, value, normalized, value
    return raw, value, value, str(value)


def equivalence_groups(
    claims: Sequence[Claim], values: Sequence[Any], definition: FieldDefinition
) -> tuple[tuple[str, ...], ...]:
    groups: list[list[str]] = []
    representatives: list[Any] = []
    for claim, value in zip(claims, values, strict=True):
        index = next(
            (i for i, item in enumerate(representatives) if _equivalent(item, value, definition)),
            None,
        )
        if index is None:
            representatives.append(value)
            groups.append([claim.claim_id])
        else:
            groups[index].append(claim.claim_id)
    return tuple(tuple(group) for group in groups)


def _quantity_parts(value: Any) -> tuple[Decimal, str]:
    if isinstance(value, dict):
        return require_decimal(value["amount"] if "amount" in value else value["value"]), str(
            value["unit"]
        )
    if isinstance(value, str):
        match = _QUANTITY.match(value)
        if match:
            return require_decimal(match.group(1)), match.group(2)
    raise ValueError("quantity value must contain a decimal amount and unit")


def _boolean(value: Any) -> bool | str:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().casefold() in {"true", "yes", "confirmed"}:
        return True
    if isinstance(value, str) and value.strip().casefold() in {"false", "no"}:
        return False
    if isinstance(value, str) and value.strip().casefold() == "unknown":
        return "unknown"
    raise ValueError("boolean value is invalid")


def _equivalent(left: Any, right: Any, definition: FieldDefinition) -> bool:
    if definition.value_kind is ValueKind.QUANTITY:
        return (
            abs(Decimal(left["amount"]) - Decimal(right["amount"]))
            <= definition.equivalence_tolerance
        )
    return bool(left == right)
