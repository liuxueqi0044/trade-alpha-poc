"""Immutable, evidence-aware value objects for commercial execution."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_extra_types.currency_code import ISO4217

from trade_alpha.contracts.common import EvidenceLocator, VersionRef, require_utc

_CONTRACT_VERSION = VersionRef(name="trade-alpha.execution.contracts", version="1.0.0")

# These are the persisted unit codes supported by the first execution-plane contract.
# The Pint adapter is intentionally responsible for interpreting their dimensions.
_UNIT_CODES = frozenset(
    {
        "mm",
        "cm",
        "m",
        "km",
        "inch",
        "in",
        "ft",
        "g",
        "kg",
        "tonne",
        "lb",
        "oz",
        "ml",
        "l",
        "m3",
        "piece",
        "count",
        "unit",
        "s",
        "min",
        "h",
        "day",
    }
)


def require_decimal(value: object) -> Decimal:
    """Parse a persisted decimal without silently accepting binary floats."""

    if isinstance(value, (bool, float)):
        raise ValueError("binary float values are not permitted; use Decimal or a decimal string")
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("value must be a Decimal-compatible value") from error
    if not decimal.is_finite():
        raise ValueError("decimal value must be finite")
    return decimal


def require_unit_code(value: str) -> str:
    """Validate a stable persisted unit code without importing Pint into contracts."""

    normalized = value.strip()
    if normalized not in _UNIT_CODES:
        raise ValueError(f"unknown unit code: {value}")
    return normalized


class FrozenContract(BaseModel):
    """Base configuration shared by every public execution value object."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_by_alias=True)

    schema_version: VersionRef = Field(default_factory=lambda: _CONTRACT_VERSION)


DecimalValue = Annotated[Decimal, Field()]


class MonetaryAmount(FrozenContract):
    """An amount in an explicit ISO 4217 currency, represented only as a Decimal."""

    amount: DecimalValue
    currency: ISO4217

    @field_validator("amount", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return require_decimal(value)

    def convert_to(
        self, currency: ISO4217, *, fx_snapshot: FxSnapshot | None = None
    ) -> MonetaryAmount:
        """Return this amount in ``currency``, requiring a snapshot across currencies."""

        if currency == self.currency:
            return self
        if fx_snapshot is None:
            raise ValueError("cross-currency conversion requires an FxSnapshot")
        return fx_snapshot.convert(self, to_currency=currency)

    def add(
        self, other: MonetaryAmount, *, fx_snapshot: FxSnapshot | None = None
    ) -> MonetaryAmount:
        """Add another amount, with explicit FX provenance when currencies differ."""

        converted = other.convert_to(self.currency, fx_snapshot=fx_snapshot)
        return MonetaryAmount(amount=self.amount + converted.amount, currency=self.currency)


class MeasuredValue(FrozenContract):
    """A Decimal magnitude persisted with a canonical, dimension-bearing unit code."""

    amount: DecimalValue = Field(ge=0)
    unit: str = Field(min_length=1)

    @field_validator("amount", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return require_decimal(value)

    @field_validator("unit")
    @classmethod
    def known_unit(cls, value: str) -> str:
        return require_unit_code(value)


class MeasuredRange(FrozenContract):
    """An inclusive range whose endpoints must use the same persisted unit."""

    minimum: MeasuredValue = Field(validation_alias=AliasChoices("minimum", "lower", "min"))
    maximum: MeasuredValue = Field(validation_alias=AliasChoices("maximum", "upper", "max"))

    @model_validator(mode="after")
    def ordered_same_unit(self) -> MeasuredRange:
        if self.minimum.unit != self.maximum.unit:
            raise ValueError("range endpoints must use the same unit")
        if self.minimum.amount > self.maximum.amount:
            raise ValueError("range minimum must not exceed maximum")
        return self

    @property
    def lower(self) -> MeasuredValue:
        """Compatibility spelling for the inclusive minimum."""

        return self.minimum

    @property
    def upper(self) -> MeasuredValue:
        """Compatibility spelling for the inclusive maximum."""

        return self.maximum


class Tolerance(FrozenContract):
    """A permitted absolute and/or relative deviation from a measured target."""

    absolute: MeasuredValue | None = Field(
        default=None, validation_alias=AliasChoices("absolute", "amount")
    )
    relative: DecimalValue | None = Field(default=None, ge=0, le=1)

    @field_validator("relative", mode="before")
    @classmethod
    def decimal_only(cls, value: object | None) -> Decimal | None:
        return None if value is None else require_decimal(value)

    @model_validator(mode="after")
    def specified(self) -> Tolerance:
        if self.absolute is None and self.relative is None:
            raise ValueError("tolerance requires an absolute or relative value")
        return self


class FxSnapshot(FrozenContract):
    """A directional, point-in-time foreign-exchange rate with source evidence."""

    from_currency: ISO4217
    to_currency: ISO4217
    rate: DecimalValue = Field(gt=0)
    observed_at: datetime
    source: str = Field(min_length=1)
    source_version: VersionRef = Field(
        default_factory=lambda: VersionRef(name="fx-source", version="unspecified")
    )
    evidence: EvidenceLocator

    @field_validator("rate", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return require_decimal(value)

    @field_validator("observed_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def directional(self) -> FxSnapshot:
        if self.from_currency == self.to_currency:
            raise ValueError("FxSnapshot currencies must differ")
        return self

    def convert(self, amount: MonetaryAmount, *, to_currency: ISO4217) -> MonetaryAmount:
        """Convert only in the captured direction so the audit trail remains explicit."""

        target = str(to_currency)
        if amount.currency != self.from_currency or target != self.to_currency:
            raise ValueError("FxSnapshot direction does not match requested conversion")
        return MonetaryAmount(amount=amount.amount * self.rate, currency=target)


class IncotermCode(StrEnum):
    EXW = "EXW"
    FCA = "FCA"
    CPT = "CPT"
    CIP = "CIP"
    DAP = "DAP"
    DPU = "DPU"
    DDP = "DDP"
    FAS = "FAS"
    FOB = "FOB"
    CFR = "CFR"
    CIF = "CIF"


class Incoterm(FrozenContract):
    """An Incoterm is meaningful only with its rules edition and named place."""

    code: IncotermCode
    rules_version: str = Field(
        min_length=1, validation_alias=AliasChoices("rules_version", "version")
    )
    named_place: str = Field(min_length=1, validation_alias=AliasChoices("named_place", "place"))

    @field_validator("rules_version", "named_place")
    @classmethod
    def non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Incoterm rules version and named place must not be blank")
        return normalized


class ProvenanceRef(FrozenContract):
    """An immutable reference to the evidence and version that support a value."""

    source: str = Field(min_length=1)
    evidence: EvidenceLocator = Field(validation_alias=AliasChoices("evidence", "locator"))
    observed_at: datetime
    source_version: VersionRef

    @field_validator("source")
    @classmethod
    def source_non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source must not be blank")
        return normalized

    @field_validator("observed_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class DecisionOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    DEFERRED = "deferred"


class HumanDecision(FrozenContract):
    """A reviewable, timestamped human judgment grounded in evidence references."""

    decision_id: str = Field(min_length=1)
    outcome: DecisionOutcome = Field(validation_alias=AliasChoices("outcome", "decision"))
    actor: str = Field(min_length=1)
    decided_at: datetime
    rationale: str = Field(min_length=1)
    # A human judgment may originate from an interview or review meeting and
    # therefore need not have an upstream Artifact. Actor, reason, and UTC
    # timestamp remain mandatory audit evidence.
    evidence: tuple[ProvenanceRef, ...] = ()

    @field_validator("actor", "rationale")
    @classmethod
    def non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("human decision fields must not be blank")
        return normalized

    @field_validator("decided_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)
