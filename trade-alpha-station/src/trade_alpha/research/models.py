"""Versioned research inputs and immutable audit records."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trade_alpha.contracts.common import require_utc


class Mandate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mandate_id: str
    version: str
    capital_budget: float = Field(gt=0)
    research_hours_budget: float = Field(gt=0)
    sample_budget: float = Field(ge=0)
    inventory_allowed: bool
    max_trial_loss: float = Field(gt=0)
    target_turnover_days_max: int = Field(gt=0)
    excluded_hs_chapters: tuple[str, ...] = ()
    excluded_regulatory_classes: tuple[str, ...] = ()
    country_risk_max: float = Field(ge=0, le=1)
    minimum_market_value: float = Field(ge=0)
    minimum_data_confidence: float = Field(ge=0, le=1)
    max_country_exposure: float = Field(gt=0, le=1)
    max_hs_chapter_exposure: float = Field(gt=0, le=1)
    exploration_budget_share: float = Field(ge=0, le=1)


class Opportunity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    opportunity_id: str
    hs_code: str
    hs_chapter: str
    import_market: str
    buyer_type: str
    market_value: float
    country_risk: float
    data_confidence: float
    regulatory_class: str | None = None
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def utc_as_of(cls, value: datetime) -> datetime:
        return require_utc(value)


class UniverseDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    opportunity_id: str
    included: bool
    reasons: tuple[str, ...]
    universe_version: str
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def utc_as_of(cls, value: datetime) -> datetime:
        return require_utc(value)


class FeatureDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    feature_name: str
    feature_version: str
    entity_grain: Literal["opportunity"]
    availability_lag_days: int = Field(ge=0)
    missing_policy: Literal["explicit_missing_indicator", "reject"]
    expected_direction: Literal["positive", "negative"]
    weight: float
    status: Literal["approved", "draft"] = "approved"


class FeatureValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    opportunity_id: str
    feature_name: str
    value: float | None
    observed_at: datetime
    available_at: datetime
    feature_version: str

    @field_validator("observed_at", "available_at")
    @classmethod
    def utc_times(cls, value: datetime) -> datetime:
        return require_utc(value)


class ScoreBreakdown(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    opportunity_id: str
    raw_alpha: float
    risk_penalty: float
    confidence: float = Field(ge=0, le=1)
    final_score: float
    feature_contributions: dict[str, float]
    missing_indicators: dict[str, int]
    score_version: str


ModelState = Literal[
    "draft",
    "backtested",
    "reviewed",
    "paper_approved",
    "limited_live",
    "scaled",
    "rejected",
    "retired",
]


class ModelApproval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    state: ModelState
    owner: str | None = None
    max_budget: float | None = None
    failure_conditions: tuple[str, ...] = ()
    rollback: str | None = None
    frozen_report_ref: str | None = None
    scope: str | None = None
