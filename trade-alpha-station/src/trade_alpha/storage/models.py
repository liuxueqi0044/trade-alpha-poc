"""Storage contracts for M04's physical regions and point-in-time facts."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_alpha.contracts.common import require_utc, utc_now

RegionName = Literal["raw", "bronze", "silver", "gold"]


class RegionContract(BaseModel):
    """A deliberately small, enforceable contract for one physical data region."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: RegionName
    allowed_content: tuple[str, ...]
    forbidden_content: tuple[str, ...]
    mutable: bool
    published: bool


REGION_CONTRACTS: dict[RegionName, RegionContract] = {
    "raw": RegionContract(
        name="raw",
        allowed_content=("source bytes", "retrieval manifests"),
        forbidden_content=("mutable operational state",),
        mutable=False,
        published=False,
    ),
    "bronze": RegionContract(
        name="bronze",
        allowed_content=("blocks", "OCR tokens", "parsed tables", "normalised rows"),
        forbidden_content=("operational state",),
        mutable=True,
        published=False,
    ),
    "silver": RegionContract(
        name="silver",
        allowed_content=("claims", "entities", "relationships", "PIT observations"),
        forbidden_content=("operational state",),
        mutable=True,
        published=False,
    ),
    "gold": RegionContract(
        name="gold",
        allowed_content=("features", "signals", "opportunities", "backtests", "reports"),
        forbidden_content=("work", "tmp", "mutable operational state"),
        mutable=False,
        published=True,
    ),
}


class BitemporalObservation(BaseModel):
    """A fact version visible only in its valid and system time intervals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    natural_key: str = Field(min_length=1)
    value: float
    valid_from: datetime
    valid_to: datetime | None = None
    observed_at: datetime
    system_from: datetime
    system_to: datetime | None = None
    source_local_time: str | None = None
    source_timezone: str | None = None

    @field_validator("valid_from", "valid_to", "observed_at", "system_from", "system_to")
    @classmethod
    def utc_only(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def ordered_intervals(self) -> BitemporalObservation:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        if self.system_to is not None and self.system_to <= self.system_from:
            raise ValueError("system_to must be after system_from")
        if (self.source_local_time is None) != (self.source_timezone is None):
            raise ValueError("source_local_time and source_timezone must be supplied together")
        return self


class MoneyObservation(BaseModel):
    """Monetary data preserves source units and makes every conversion auditable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    original_amount: float
    original_currency: str = Field(min_length=3, max_length=3)
    converted_amount: float | None = None
    converted_currency: str | None = None
    fx_source: str | None = None
    fx_date: datetime | None = None
    fx_version: str | None = None
    fx_rate: float | None = Field(default=None, gt=0)

    @field_validator("original_currency", "converted_currency")
    @classmethod
    def uppercase_currency(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @field_validator("fx_date")
    @classmethod
    def utc_fx_date(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def conversion_provenance(self) -> MoneyObservation:
        converted = self.converted_amount is not None or self.converted_currency is not None
        if converted and (
            not self.converted_currency
            or not self.fx_source
            or not self.fx_date
            or not self.fx_version
            or self.fx_rate is None
        ):
            raise ValueError(
                "converted money requires currency, FX source, date, version, and rate"
            )
        if converted and not math.isclose(
            self.converted_amount or 0,
            self.original_amount * (self.fx_rate or 0),
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError("converted_amount must equal original_amount times fx_rate")
        return self


class SnapshotFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    rows: int = Field(ge=0)
    min_values: dict[str, str | int | float | None] = Field(default_factory=dict)
    max_values: dict[str, str | int | float | None] = Field(default_factory=dict)
    schema_hash: str
    content_hash: str


class SnapshotManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    region: RegionName
    files: tuple[SnapshotFile, ...]
    created_at: datetime = Field(default_factory=utc_now)
    code_version: str
    config_version: str
    manifest_hash: str

    @field_validator("created_at")
    @classmethod
    def utc_created(cls, value: datetime) -> datetime:
        return require_utc(value)


class RecoveryReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    elapsed_seconds: float = Field(ge=0)
    rto_seconds: float = Field(default=4 * 60 * 60, gt=0)
    hashes_match: bool
    postgres_rehearsed: bool
