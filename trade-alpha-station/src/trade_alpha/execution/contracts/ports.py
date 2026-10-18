"""Dependency-inversion ports for the pure execution contract layer."""

from __future__ import annotations

from typing import Protocol

from .models import MeasuredValue


class UnitConversionPort(Protocol):
    """Convert a persisted measured value while preserving Decimal semantics."""

    def convert(self, value: MeasuredValue, *, to_unit: str) -> MeasuredValue: ...
