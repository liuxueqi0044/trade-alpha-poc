"""Shared immutable value objects used by M01-M03."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class VersionRef(BaseModel):
    """A semantic identifier for code, schema, model, parser, or prompt versions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class EvidenceLocator(BaseModel):
    """A precise, immutable pointer into an artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(min_length=1)
    block_ids: tuple[str, ...] = ()
    page_number: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    quality: Literal["precise", "coarse"] = "precise"

    @field_validator("bbox")
    @classmethod
    def validate_bbox(
        cls, value: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if value is None:
            return value
        x0, y0, x1, y1 = value
        if not all(0.0 <= item <= 1.0 for item in value) or x0 >= x1 or y0 >= y1:
            raise ValueError("bbox must be normalized and ordered")
        return value

    @field_validator("end_ms")
    @classmethod
    def validate_time_range(cls, value: int | None, info: object) -> int | None:
        if value is None:
            return value
        data = getattr(info, "data", {})
        start = data.get("start_ms")
        if start is not None and value <= start:
            raise ValueError("end_ms must be greater than start_ms")
        return value


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def require_utc(value: datetime) -> datetime:
    """Reject naive timestamps and normalize aware timestamps to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
