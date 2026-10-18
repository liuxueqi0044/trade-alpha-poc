"""Pint-backed unit conversion adapter for execution contracts."""

from __future__ import annotations

from decimal import Decimal

from pint import UnitRegistry
from pint.errors import DimensionalityError, UndefinedUnitError

from trade_alpha.execution.contracts.models import MeasuredValue, require_unit_code


class PintUnitConverter:
    """Convert supported persisted unit codes without converting Decimal values to floats."""

    def __init__(self) -> None:
        self._registry: UnitRegistry[Decimal] = UnitRegistry(non_int_type=Decimal)
        # Pint's stock inch definition carries a binary-float conversion factor.
        # Replacing it with this exact decimal relationship keeps 25.4 mm exact.
        self._registry.define("inch = 25.4 * millimeter = in")
        self._registry.define("m3 = meter ** 3")
        self._registry.define("piece = [item]")

    def convert(self, value: MeasuredValue, *, to_unit: str) -> MeasuredValue:
        """Convert ``value`` to a compatible unit, retaining a Decimal magnitude."""

        target = require_unit_code(to_unit)
        try:
            converted = self._registry.Quantity(value.amount, value.unit).to(target)
        except UndefinedUnitError as error:
            raise ValueError(f"unknown unit code: {to_unit}") from error
        except DimensionalityError as error:
            raise ValueError("cannot convert across unit dimensions") from error
        magnitude = converted.magnitude
        if not isinstance(magnitude, Decimal):
            raise TypeError("Pint conversion did not retain a Decimal magnitude")
        return MeasuredValue(amount=magnitude, unit=target)
