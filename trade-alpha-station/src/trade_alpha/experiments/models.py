"""Immutable PostgreSQL-compatible domain records for M09."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_alpha.contracts.common import require_utc


class Role(StrEnum):
    ANALYST = "analyst"
    APPROVER = "approver"
    ADMIN = "admin"


class ExperimentStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    EVALUATED = "evaluated"
    REJECTED = "rejected"


class PrivacyClass(StrEnum):
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class OutcomeKind(StrEnum):
    SUCCESS = "success"
    REJECTED = "rejected"
    FAILED = "failed"
    NO_RESPONSE = "no_response"
    QUALITY_ISSUE = "quality_issue"
    CENSORED = "censored"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Money(FrozenModel):
    amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalized_currency(cls, value: str) -> str:
        if not value.isalpha() or value != value.upper():
            raise ValueError("currency must be uppercase ISO alphabetic code")
        return value


class Quantity(FrozenModel):
    amount: Decimal = Field(gt=0)
    unit: str = Field(min_length=1)


class Budget(FrozenModel):
    research_hours: Decimal = Field(gt=0)
    outreach_count: int = Field(gt=0)
    cash_max: Money
    max_loss: Money

    @model_validator(mode="after")
    def same_currency(self) -> Budget:
        if self.cash_max.currency != self.max_loss.currency:
            raise ValueError("cash_max and max_loss must use the same currency")
        return self


class OpportunitySnapshot(FrozenModel):
    opportunity_snapshot_id: str = Field(min_length=1)
    snapshot_hash: str = Field(min_length=1)
    frozen_at: datetime

    @field_validator("frozen_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class Experiment(FrozenModel):
    experiment_id: str = Field(min_length=1)
    experiment_type: str = Field(min_length=1)
    opportunity_snapshot: OpportunitySnapshot
    mandate_version: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    approver: str = Field(min_length=1)
    budget: Budget | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    success_definition: str | None = None
    stop_conditions: tuple[str, ...] = ()
    status: ExperimentStatus = ExperimentStatus.PROPOSED

    @field_validator("start_at", "end_at")
    @classmethod
    def utc_optional_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def time_ordered(self) -> Experiment:
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class ActionDraft(FrozenModel):
    action_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    estimated_cost: Money
    estimated_loss: Money
    risk_level: str = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class BusinessEvent(FrozenModel):
    event_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    occurred_at: datetime
    recorded_at: datetime
    idempotency_key: str | None = None
    entity_id: str | None = None
    money: Money | None = None
    quantity: Quantity | None = None
    artifact_id: str | None = None
    causation_event_id: str | None = None
    privacy_class: PrivacyClass = PrivacyClass.INTERNAL
    schema_version: str = "experiment-event/1.0.0"
    reversal_of_event_id: str | None = None

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def transactional_contract(self) -> BusinessEvent:
        transaction_events = {"trial_order_created", "payment_received", "shipment_dispatched"}
        evidence_events = {
            "supplier_quote_received",
            "sample_sent",
            "inspection_completed",
            "payment_received",
            "quality_issue_opened",
        }
        if self.event_type in transaction_events and (self.money is None or self.quantity is None):
            raise ValueError("transaction event requires original money and quantity")
        if self.event_type in evidence_events and not self.artifact_id:
            raise ValueError("event requires M01 artifact reference")
        return self


class OutcomeLabel(FrozenModel):
    label_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    kind: OutcomeKind
    actor: str = Field(min_length=1)
    observed_at: datetime
    detail: str = Field(min_length=1)
    source_event_id: str | None = None
    observation_window_end: datetime | None = None

    @field_validator("observed_at", "observation_window_end")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)
