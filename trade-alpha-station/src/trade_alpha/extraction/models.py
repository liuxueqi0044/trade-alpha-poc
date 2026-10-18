"""Immutable contracts for evidence-first multimodal extraction."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_alpha.contracts.common import Confidence, require_utc, utc_now


class DocumentFormat(StrEnum):
    HTML = "html"
    PDF = "pdf"
    SCANNED_PDF = "scanned_pdf"
    OFFICE = "office"
    IMAGE = "image"
    AUDIO_VIDEO = "audio_video"
    UNKNOWN = "unknown"


BlockType = Literal[
    "paragraph", "heading", "table", "table_cell", "image", "ocr_token", "transcript"
]
ClaimStatus = Literal[
    "proposed",
    "validated_rule",
    "accepted",
    "needs_review",
    "rejected",
    "rejected_schema",
    "superseded",
]
RunStatus = Literal[
    "completed", "partially_enriched", "needs_review", "quarantined", "unreadable"
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def stable_id(kind: str, *parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, "/".join(("trade-alpha", "m02", kind, *parts))))


class RoutingDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_format: DocumentFormat
    primary_parser: str
    fallback_parsers: tuple[str, ...] = ()
    signature: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    declared_mime: str | None = None
    mime_conflict: bool = False

    @property
    def allowed_parsers(self) -> tuple[str, ...]:
        return (self.primary_parser, *self.fallback_parsers)


class ParsedBlock(BaseModel):
    """Backend-neutral block before stable evidence IDs are assigned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    local_id: str = Field(min_length=1)
    block_type: BlockType
    text: str = ""
    page_number: int | None = Field(default=None, ge=1)
    parent_local_id: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    language: str | None = None
    confidence: Confidence = 1.0
    locator_quality: Literal["precise", "coarse"] = "precise"
    table_row: int | None = Field(default=None, ge=0)
    table_col: int | None = Field(default=None, ge=0)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(
        cls, value: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if value is None:
            return value
        x0, y0, x1, y1 = value
        if not all(0 <= item <= 1 for item in value) or x0 >= x1 or y0 >= y1:
            raise ValueError("bbox must be normalized and ordered")
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> ParsedBlock:
        if self.end_ms is not None and self.start_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be after start_ms")
        return self


class DocumentBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    block_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    extraction_run_id: str = Field(min_length=1)
    document_format: DocumentFormat
    block_type: BlockType
    page_number: int | None = Field(default=None, ge=1)
    parent_block_id: str | None = None
    reading_order: int = Field(ge=0)
    bbox: tuple[float, float, float, float] | None = None
    coordinate_system: str = "normalized_top_left"
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    text: str = ""
    language: str | None = None
    confidence: Confidence = 1.0
    parser: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    model_id: str | None = None
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    locator_quality: Literal["precise", "coarse"] = "precise"
    low_confidence: bool = False
    table_row: int | None = Field(default=None, ge=0)
    table_col: int | None = Field(default=None, ge=0)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(
        cls, value: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if value is None:
            return value
        x0, y0, x1, y1 = value
        if not all(0 <= item <= 1 for item in value) or x0 >= x1 or y0 >= y1:
            raise ValueError("bbox must be normalized and ordered")
        return value

    @model_validator(mode="after")
    def validate_locator_shape(self) -> DocumentBlock:
        if self.end_ms is not None and self.start_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be after start_ms")
        if self.block_type == "transcript" and (
            self.start_ms is None or self.end_ms is None
        ):
            raise ValueError("transcript requires a time locator")
        return self

    def has_evidence_locator(self) -> bool:
        if self.document_format is DocumentFormat.HTML:
            return self.reading_order >= 0
        if self.document_format is DocumentFormat.AUDIO_VIDEO:
            return self.start_ms is not None and self.end_ms is not None
        return self.page_number is not None and self.bbox is not None


class CandidateClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    subject_mention: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object_value: str | int | float | bool
    object_type: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    evidence_block_ids: tuple[str, ...] = Field(min_length=1)
    extractor_type: Literal["rule", "llm", "human"]
    extractor_version: str = Field(min_length=1)
    prompt_version: str = "none"
    schema_version: str = "claim/1.0.0"
    confidence: Confidence
    status: ClaimStatus = "proposed"
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def validate_object_type(self) -> CandidateClaim:
        if self.object_type == "date" and not isinstance(self.object_value, str):
            raise ValueError("date object_value must be a string")
        if self.object_type in {"percentage", "amount"} and not isinstance(
            self.object_value, (str, int, float)
        ):
            raise ValueError(f"{self.object_type} object_value has an invalid type")
        return self


class ExtractionRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    extraction_run_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    artifact_id: str = Field(min_length=1)
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_format: DocumentFormat
    parser: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    model_id: str | None = None
    prompt_version: str = "none"
    schema_version: str = "claim/1.0.0"
    created_at: datetime = Field(default_factory=utc_now)
    status: RunStatus
    blocks: tuple[DocumentBlock, ...] = ()
    claims: tuple[CandidateClaim, ...] = ()
    diagnostics: tuple[str, ...] = ()
    dependency_versions: tuple[tuple[str, str], ...] = ()
    untrusted_document: Literal[True] = True

    @field_validator("created_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class QuarantineRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    retryable: bool = True

    @field_validator("created_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    claim_id: str
    decision: Literal["accepted", "rejected"]
    reviewer_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    decided_at: datetime = Field(default_factory=utc_now)
    prior_status: ClaimStatus

    @field_validator("decided_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class ReviewProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    status: Literal["accepted", "rejected"]
    decision_id: str
    decided_at: datetime

    @field_validator("decided_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class SchemaGateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool
    claim: CandidateClaim | None = None
    reason: str


class QualityReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    character_accuracy: float = Field(ge=0, le=1)
    character_error_rate: float = Field(ge=0, le=1)
    table_cell_accuracy: float | None = Field(default=None, ge=0, le=1)
    expected_characters: int = Field(ge=0)
    expected_table_cells: int = Field(default=0, ge=0)
