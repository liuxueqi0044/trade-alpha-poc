"""Immutable value objects and state names for evidence ingestion.

The module deliberately keeps its persistent representation JSON-compatible.  It
can therefore be used before optional ingestion dependencies are installed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from trade_alpha.contracts.common import require_utc, utc_now

SourceType = Literal["api", "file", "web", "download", "change_detection"]
AccessMethod = Literal["api", "scrapy", "playwright", "download", "changedetection"]
SourceStatus = Literal["enabled", "disabled"]
BatchStatus = Literal[
    "planned", "running", "complete", "incomplete", "retrying", "blocked_policy", "failed_terminal"
]
CompletenessStatus = Literal["complete", "incomplete", "invalid_payload", "not_modified"]


class RatePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_requests_per_window: int = Field(gt=0)
    window_seconds: float = Field(gt=0)


class SourceRecord(BaseModel):
    """The complete registry entry required before a source may be scheduled."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    owner: str = Field(min_length=1)
    source_type: SourceType
    base_url: HttpUrl
    access_method: AccessMethod
    auth_class: str = "none"
    rate_policy: RatePolicy
    robots_policy: str | None = None
    terms_url: HttpUrl | None = None
    software_license: str | None = None
    data_license: str | None = None
    commercial_use: str | None = None
    redistribution: str | None = None
    retention_policy: str | None = None
    allowed_content_types: tuple[str, ...] = Field(min_length=1)
    parser_profile: str = Field(min_length=1)
    owner_module: Literal["M01"] = "M01"
    status: SourceStatus = "enabled"
    registry_version: str = "1"

    @field_validator("allowed_content_types")
    @classmethod
    def normalise_content_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalised = tuple(item.lower().split(";", 1)[0].strip() for item in value)
        if any(not item or "/" not in item for item in normalised):
            raise ValueError("allowed_content_types must contain MIME types")
        return normalised


class RetrievalEnvelope(BaseModel):
    """One attempted request, distinct from the content artifact it references."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    retrieval_id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str
    request_fingerprint: str
    request_url_redacted: str
    request_method: str = "GET"
    requested_at: datetime = Field(default_factory=utc_now)
    retrieved_at: datetime = Field(default_factory=utc_now)
    http_status: int = Field(ge=0, le=999)
    response_headers_hash: str | None = None
    content_hash: str | None = None
    artifact_id: str | None = None
    content_length: int = Field(ge=0)
    mime_type_detected: str | None = None
    declared_mime_type: str | None = None
    batch_id: str
    page_number: int | None = Field(default=None, ge=1)
    cursor_before: str | None = None
    cursor_after: str | None = None
    completeness_status: CompletenessStatus
    code_sha: str = "unknown"
    connector_version: str
    supersedes_artifact_id: str | None = None
    error_code: str | None = None

    @field_validator("requested_at", "retrieved_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        return require_utc(value)


class Artifact(BaseModel):
    """Metadata for a content-addressed immutable object."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    content_hash: str
    byte_length: int = Field(ge=0)
    storage_path: str
    extension: str
    created_at: datetime = Field(default_factory=utc_now)
    mime_type_detected: str

    @field_validator("created_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        return require_utc(value)


class BatchManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str
    status: BatchStatus = "planned"
    cursor_before: str | None = None
    cursor_after: str | None = None
    # This is a private-to-the-batch checkpoint; it is never a published cursor.
    resume_cursor: str | None = None
    retrieval_ids: list[str] = Field(default_factory=list)
    completed_page_numbers: list[int] = Field(default_factory=list)
    expected_total_pages: int | None = Field(default=None, ge=1)
    error: str | None = None
    policy_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        return require_utc(value)


class PageResponse(BaseModel):
    """Connector-neutral response used by the deterministic pagination runner."""

    model_config = ConfigDict(extra="forbid")

    url: str
    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    content: bytes = b""
    next_cursor: str | None = None
    # ``next_cursor`` drives page traversal only.  A connector must explicitly
    # provide a durable high-water mark before M01 publishes incremental state.
    commit_cursor: str | None = None
    declared_total_pages: int | None = Field(default=None, ge=1)
    original_filename: str | None = None
    request_body: Any | None = None


class IngestionEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["artifact.retrieved", "retrieval.failed", "schema.drift", "policy.blocked"]
    occurred_at: datetime = Field(default_factory=utc_now)
    source_id: str
    batch_id: str
    retrieval_id: str | None = None
    artifact_id: str | None = None
    detail: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        return require_utc(value)
