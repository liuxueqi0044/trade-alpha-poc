"""Immutable contracts for M11 source discovery and controlled onboarding."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from trade_alpha.contracts.common import require_utc, utc_now
from trade_alpha.governance import SourceGovernance
from trade_alpha.ingestion import SourceRecord


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CandidateState(StrEnum):
    DISCOVERED = "discovered"
    PROBED = "probed"
    POLICY_PENDING = "policy_pending"
    APPROVED = "approved"
    CONNECTOR_DRAFTED = "connector_drafted"
    GOLDEN_VALIDATED = "golden_validated"
    ENABLED = "enabled"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    STALE = "stale"


class PublisherKind(StrEnum):
    OFFICIAL = "official"
    COMMERCIAL = "commercial"
    COMMUNITY = "community"
    UNKNOWN = "unknown"


class DiscoveryKind(StrEnum):
    CKAN = "ckan"
    OPENAPI_DIRECTORY = "openapi_directory"
    DCAT = "dcat"
    SEED = "seed"


def canonicalize_url(value: str) -> str:
    """Normalize a public URL without removing meaningful query parameters."""

    parts = urlsplit(value.strip())
    scheme = parts.scheme.casefold()
    hostname = (parts.hostname or "").casefold()
    if not scheme or not hostname:
        raise ValueError("candidate URL requires scheme and hostname")
    port = parts.port
    netloc = hostname
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{hostname}:{port}"
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


def stable_candidate_id(canonical_url: str) -> str:
    normalized = canonicalize_url(canonical_url)
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:24]
    return f"source-candidate-{digest}"


class DiscoveryEvidence(FrozenModel):
    kind: DiscoveryKind
    catalog_url: HttpUrl
    catalog_record_id: str = Field(min_length=1)
    directory_license: str | None = None


class SourceDistribution(FrozenModel):
    url: HttpUrl
    format: str = Field(min_length=1)
    media_type: str | None = None
    openapi_url: HttpUrl | None = None

    @field_validator("format")
    @classmethod
    def normalize_format(cls, value: str) -> str:
        return value.strip().casefold()


class SourceCandidate(FrozenModel):
    """A discovered candidate. It is never itself permission to collect data."""

    candidate_id: str = Field(pattern=r"^source-candidate-[a-f0-9]{24}$")
    title: str = Field(min_length=1)
    description: str = ""
    publisher: str = Field(min_length=1)
    publisher_kind: PublisherKind = PublisherKind.UNKNOWN
    canonical_url: HttpUrl
    domains: tuple[str, ...] = ()
    distributions: tuple[SourceDistribution, ...] = Field(min_length=1)
    geographies: tuple[str, ...] = ()
    update_frequency: str | None = None
    auth_class: str = "unknown"
    terms_url: HttpUrl | None = None
    license_claim: str | None = None
    license_evidence_urls: tuple[HttpUrl, ...] = ()
    discovery_evidence: tuple[DiscoveryEvidence, ...] = Field(min_length=1)
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    metadata_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    state: CandidateState = CandidateState.DISCOVERED
    revision: int = Field(default=1, ge=1)

    @field_validator("first_seen_at", "last_seen_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @field_validator("domains", "geographies")
    @classmethod
    def normalized_labels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({item.strip().casefold() for item in value if item.strip()}))

    @model_validator(mode="after")
    def validate_identity(self) -> SourceCandidate:
        expected = stable_candidate_id(str(self.canonical_url))
        if self.candidate_id != expected:
            raise ValueError("candidate_id does not match canonical_url")
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("last_seen_at cannot precede first_seen_at")
        return self


def metadata_hash(values: dict[str, Any]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def candidate_metadata(candidate: SourceCandidate) -> dict[str, Any]:
    return {
        "title": candidate.title,
        "description": candidate.description,
        "publisher": candidate.publisher,
        "publisher_kind": candidate.publisher_kind,
        "canonical_url": canonicalize_url(str(candidate.canonical_url)),
        "domains": candidate.domains,
        "distributions": [item.model_dump(mode="json") for item in candidate.distributions],
        "geographies": candidate.geographies,
        "update_frequency": candidate.update_frequency,
        "auth_class": candidate.auth_class,
        "terms_url": str(candidate.terms_url) if candidate.terms_url else None,
        "license_claim": candidate.license_claim,
        "license_evidence_urls": tuple(str(item) for item in candidate.license_evidence_urls),
    }


class ScoreSignals(FrozenModel):
    relevance: float = Field(ge=0, le=100)
    coverage: float = Field(ge=0, le=100)
    freshness: float = Field(ge=0, le=100)
    machine_readability: float = Field(ge=0, le=100)
    publisher_authority: float = Field(ge=0, le=100)
    stability: float = Field(ge=0, le=100)
    license_clarity: float = Field(ge=0, le=100)


class CandidateScore(FrozenModel):
    candidate_id: str
    signals: ScoreSignals
    weights: dict[str, float]
    priority: float = Field(ge=0, le=100)
    scoring_version: str = "m11-v1"


class PolicyReview(FrozenModel):
    candidate_id: str
    approved: bool
    reviewer: str = Field(min_length=1)
    reviewed_at: datetime
    reason: str = Field(min_length=1)
    terms_url: HttpUrl | None = None
    api_use_status: str | None = None
    caching_policy: str | None = None
    retention_policy: str | None = None
    redistribution_status: str | None = None
    data_owner: str | None = None
    data_license_id: str | None = None
    attribution: str | None = None
    commercial_use: str | None = None
    derivatives: str | None = None
    data_redistribution: str | None = None
    region_restrictions: str | None = None
    evidence_urls: tuple[HttpUrl, ...] = ()

    @field_validator("reviewed_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def complete_approval(self) -> PolicyReview:
        if not self.approved:
            return self
        required = {
            "terms_url": self.terms_url,
            "api_use_status": self.api_use_status,
            "caching_policy": self.caching_policy,
            "retention_policy": self.retention_policy,
            "redistribution_status": self.redistribution_status,
            "data_owner": self.data_owner,
            "data_license_id": self.data_license_id,
            "commercial_use": self.commercial_use,
            "data_redistribution": self.data_redistribution,
            "evidence_urls": self.evidence_urls,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"approved review missing: {','.join(missing)}")
        if self.api_use_status not in {"allowed", "approved", "permitted"}:
            raise ValueError("approved review requires approved API use")
        if self.commercial_use not in {"allowed", "approved", "permitted"}:
            raise ValueError("approved review requires approved commercial use")
        return self


class ConnectorDraft(FrozenModel):
    candidate_id: str
    connector_type: str = Field(min_length=1)
    config: dict[str, Any]
    enabled: bool = False
    generated_at: datetime = Field(default_factory=utc_now)

    @field_validator("generated_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class GoldenValidation(FrozenModel):
    candidate_id: str
    validation_id: str = Field(min_length=1)
    passed: bool
    checks: dict[str, bool] = Field(min_length=1)
    sample_artifact_ids: tuple[str, ...] = ()
    reviewer: str = Field(min_length=1)
    checked_at: datetime

    @field_validator("checked_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def validate_pass(self) -> GoldenValidation:
        if self.passed and (not all(self.checks.values()) or not self.sample_artifact_ids):
            raise ValueError("passed Golden validation requires all checks and sample evidence")
        return self


class OnboardingBundle(FrozenModel):
    candidate: SourceCandidate
    review: PolicyReview
    source_record: SourceRecord
    governance_record: SourceGovernance
    connector: ConnectorDraft
    golden_validation: GoldenValidation | None = None

