"""Immutable governance contracts shared by the M10 control plane."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_alpha.contracts.common import require_utc


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class Role(StrEnum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    APPROVER = "approver"
    ADMIN = "admin"


class Action(StrEnum):
    READ = "read"
    READ_RAW = "read_raw"
    EXPORT = "export"
    PROPOSE_EXPERIMENT = "propose_experiment"
    REVIEW = "review"
    REVIEW_EXECUTION = "review_execution"
    OPERATE_PIPELINE = "operate_pipeline"
    APPROVE_EXPERIMENT = "approve_experiment"
    APPROVE_EXECUTION = "approve_execution"
    MANAGE_SYSTEM = "manage_system"
    MANAGE_ROLE = "manage_role"


class SoftwareLicense(FrozenModel):
    repo: str = Field(min_length=1)
    version: str = Field(min_length=1)
    spdx: str = Field(min_length=1)
    modified: bool
    distribution: str = Field(min_length=1)
    notice_requirement: str = Field(min_length=1)


class DataLicense(FrozenModel):
    owner: str | None = None
    license_id: str | None = None
    attribution: str | None = None
    commercial_use: str | None = None
    derivatives: str | None = None
    redistribution: str | None = None
    region_restrictions: str | None = None


class SourceGovernance(FrozenModel):
    """A deliberately nullable record so policy, rather than parsing, blocks gaps."""

    source_id: str = Field(min_length=1)
    enabled: bool
    api_terms_url: str | None = None
    api_use_status: str | None = None
    caching_policy: str | None = None
    retention_policy: str | None = None
    redistribution_status: str | None = None
    data: DataLicense | None = None
    software: SoftwareLicense | None = None


class Identity(FrozenModel):
    actor_id: str = Field(min_length=1)
    roles: frozenset[Role] = Field(min_length=1)
    grants: frozenset[str] = frozenset()
    client_identity: str = Field(min_length=1)


class ProtectedResource(FrozenModel):
    resource_id: str = Field(min_length=1)
    classification: DataClassification
    owner: str = Field(min_length=1)
    source_id: str | None = None


class PolicyDecision(FrozenModel):
    allowed: bool
    reason: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    action: Action
    resource_id: str = Field(min_length=1)
    evaluated_at: datetime

    @field_validator("evaluated_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class AuditEvent(FrozenModel):
    sequence: int = Field(ge=0)
    actor: str = Field(min_length=1)
    client_identity: str = Field(min_length=1)
    action: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    before_hash: str = Field(min_length=1)
    after_hash: str = Field(min_length=1)
    occurred_at: datetime
    reason: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    previous_hash: str = Field(min_length=1)
    event_hash: str = Field(min_length=1)

    @field_validator("occurred_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class AuditVerification(FrozenModel):
    valid: bool
    first_invalid_sequence: int | None = None


def stable_hash(value: Any) -> str:
    """Hash JSON-compatible governance state without leaking it into audit logs."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class VersionManifest(FrozenModel):
    architecture_spec_version: str = Field(min_length=1)
    code_sha: str = Field(min_length=1)
    environment_lock_hash: str = Field(min_length=1)
    source_versions: dict[str, str] = Field(min_length=1)
    schema_versions: dict[str, str] = Field(min_length=1)
    parser_versions: dict[str, str] = Field(min_length=1)
    prompt_versions: dict[str, str] = Field(min_length=1)
    model_versions: dict[str, str] = Field(min_length=1)
    entity_resolution_version: str = Field(min_length=1)
    feature_set_version: str = Field(min_length=1)
    signal_version: str = Field(min_length=1)
    mandate_version: str = Field(min_length=1)
    input_snapshot_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_unknowns(self) -> VersionManifest:
        values: list[str] = [
            self.architecture_spec_version,
            self.code_sha,
            self.environment_lock_hash,
            self.entity_resolution_version,
            self.feature_set_version,
            self.signal_version,
            self.mandate_version,
            *self.source_versions.values(),
            *self.schema_versions.values(),
            *self.parser_versions.values(),
            *self.prompt_versions.values(),
            *self.model_versions.values(),
            *self.input_snapshot_ids,
        ]
        if any(value.strip().lower() in {"unknown", "n/a", "none", ""} for value in values):
            raise ValueError("published version manifests cannot contain unknown values")
        return self

    @property
    def manifest_hash(self) -> str:
        return stable_hash(self)


class ExportReceipt(FrozenModel):
    export_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    classification: DataClassification
    snapshot_id: str = Field(min_length=1)
    filter_hash: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    exported_at: datetime
    content_hash: str = Field(min_length=1)

    @field_validator("exported_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class Tombstone(FrozenModel):
    subject_hash: str = Field(min_length=1)
    deleted_at: datetime
    layers_processed: tuple[str, ...] = Field(min_length=1)
    backup_disposition: str = Field(min_length=1)
    derivation_rebuild_required: bool
    correlation_id: str = Field(min_length=1)

    @field_validator("deleted_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class SBOMComponent(FrozenModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    license_id: str = Field(min_length=1)
    direct: bool = False


class LicenseWaiver(FrozenModel):
    component: str = Field(min_length=1)
    adr_ref: str = Field(min_length=1)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class Vulnerability(FrozenModel):
    vulnerability_id: str = Field(min_length=1)
    component: str = Field(min_length=1)
    severity: str = Field(min_length=1)


class VulnerabilityWaiver(FrozenModel):
    vulnerability_id: str = Field(min_length=1)
    adr_ref: str = Field(min_length=1)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)
