"""Catalog adapters that discover candidates without ingesting business data."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urljoin

from pydantic import HttpUrl

from trade_alpha.contracts.common import utc_now

from .models import (
    CandidateState,
    DiscoveryEvidence,
    DiscoveryKind,
    PublisherKind,
    SourceCandidate,
    SourceDistribution,
    candidate_metadata,
    metadata_hash,
    stable_candidate_id,
)


class JsonFetcher(Protocol):
    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]: ...


def _text(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped.startswith(("https://", "http://")) else None


def _finish_candidate(candidate: SourceCandidate) -> SourceCandidate:
    return candidate.model_copy(
        update={"metadata_hash": metadata_hash(candidate_metadata(candidate))}
    )


class CKANAdapter:
    def __init__(
        self,
        catalog_url: str,
        fetcher: JsonFetcher,
        *,
        publisher_kind: PublisherKind = PublisherKind.OFFICIAL,
    ) -> None:
        self.catalog_url = catalog_url.rstrip("/")
        self.fetcher = fetcher
        self.publisher_kind = publisher_kind

    def discover(
        self, query: str, *, max_results: int = 50, observed_at: datetime | None = None
    ) -> tuple[SourceCandidate, ...]:
        if not 1 <= max_results <= 200:
            raise ValueError("max_results must be between 1 and 200")
        payload = self.fetcher.get_json(
            f"{self.catalog_url}/api/3/action/package_search",
            params={"q": query, "rows": max_results},
        )
        result = payload.get("result")
        packages = result.get("results") if isinstance(result, Mapping) else None
        if payload.get("success") is not True or not isinstance(packages, list):
            raise ValueError("invalid CKAN package_search response")
        now = observed_at or utc_now()
        candidates: list[SourceCandidate] = []
        for package in packages[:max_results]:
            if not isinstance(package, Mapping):
                continue
            package_id = _text(package.get("id"), _text(package.get("name"), "unknown"))
            dataset_url = urljoin(f"{self.catalog_url}/", f"dataset/{package_id}")
            resources = package.get("resources")
            distributions: list[SourceDistribution] = []
            if isinstance(resources, list):
                for resource in resources:
                    if not isinstance(resource, Mapping):
                        continue
                    resource_url = _http_url(resource.get("url"))
                    if not resource_url:
                        continue
                    resource_format = _text(resource.get("format"), "unknown")
                    media_type = _text(resource.get("mimetype")) or None
                    distributions.append(
                        SourceDistribution(
                            url=HttpUrl(resource_url),
                            format=resource_format,
                            media_type=media_type,
                        )
                    )
            if not distributions:
                continue
            organization = package.get("organization")
            publisher = "unknown publisher"
            if isinstance(organization, Mapping):
                publisher = _text(
                    organization.get("title"),
                    _text(organization.get("name"), publisher),
                )
            tags = package.get("tags")
            domains: tuple[str, ...] = ()
            if isinstance(tags, list):
                domains = tuple(
                    _text(tag.get("name"))
                    for tag in tags
                    if isinstance(tag, Mapping) and _text(tag.get("name"))
                )
            license_url = _http_url(package.get("license_url"))
            license_claim = _text(package.get("license_id")) or None
            candidate = SourceCandidate(
                candidate_id=stable_candidate_id(dataset_url),
                title=_text(package.get("title"), package_id),
                description=_text(package.get("notes")),
                publisher=publisher,
                publisher_kind=self.publisher_kind,
                canonical_url=HttpUrl(dataset_url),
                domains=domains,
                distributions=tuple(distributions),
                update_frequency=_text(package.get("frequency")) or None,
                auth_class="unknown",
                terms_url=HttpUrl(license_url) if license_url else None,
                license_claim=license_claim,
                license_evidence_urls=(HttpUrl(license_url),) if license_url else (),
                discovery_evidence=(
                    DiscoveryEvidence(
                        kind=DiscoveryKind.CKAN,
                        catalog_url=HttpUrl(
                            f"{self.catalog_url}/api/3/action/package_search"
                        ),
                        catalog_record_id=package_id,
                    ),
                ),
                first_seen_at=now,
                last_seen_at=now,
                metadata_hash=f"sha256:{'0' * 64}",
                state=CandidateState.DISCOVERED,
            )
            candidates.append(_finish_candidate(candidate))
        return tuple(candidates)


class APIsGuruAdapter:
    DIRECTORY_URL = "https://api.apis.guru/v2/list.json"

    def __init__(self, fetcher: JsonFetcher) -> None:
        self.fetcher = fetcher

    def discover(
        self, query: str, *, max_results: int = 50, observed_at: datetime | None = None
    ) -> tuple[SourceCandidate, ...]:
        if not 1 <= max_results <= 200:
            raise ValueError("max_results must be between 1 and 200")
        payload = self.fetcher.get_json(self.DIRECTORY_URL)
        needle = query.casefold().strip()
        now = observed_at or utc_now()
        candidates: list[SourceCandidate] = []
        for provider, provider_data in payload.items():
            if len(candidates) >= max_results:
                break
            if not isinstance(provider, str) or not isinstance(provider_data, Mapping):
                continue
            versions = provider_data.get("versions")
            preferred = provider_data.get("preferred")
            if not isinstance(versions, Mapping):
                continue
            version_data = versions.get(preferred) if isinstance(preferred, str) else None
            if not isinstance(version_data, Mapping):
                version_data = next(
                    (value for value in versions.values() if isinstance(value, Mapping)),
                    None,
                )
            if not isinstance(version_data, Mapping):
                continue
            info = version_data.get("info")
            info = info if isinstance(info, Mapping) else {}
            title = _text(info.get("title"), provider)
            description = _text(info.get("description"))
            categories = info.get("x-apisguru-categories")
            domains: tuple[str, ...] = ()
            if isinstance(categories, list):
                domains = tuple(item for item in categories if isinstance(item, str))
            haystack = " ".join((provider, title, description, *domains)).casefold()
            if needle and needle not in haystack:
                continue
            spec_url = _http_url(version_data.get("swaggerUrl"))
            if not spec_url:
                continue
            terms_url = _http_url(info.get("termsOfService"))
            candidate = SourceCandidate(
                candidate_id=stable_candidate_id(spec_url),
                title=title,
                description=description,
                publisher=provider,
                publisher_kind=PublisherKind.COMMERCIAL,
                canonical_url=HttpUrl(spec_url),
                domains=domains,
                distributions=(
                    SourceDistribution(
                        url=HttpUrl(spec_url),
                        format="openapi",
                        openapi_url=HttpUrl(spec_url),
                    ),
                ),
                update_frequency="weekly directory validation",
                auth_class="unknown",
                terms_url=HttpUrl(terms_url) if terms_url else None,
                # Directory/spec licensing is deliberately not copied into data rights.
                license_claim=None,
                license_evidence_urls=(HttpUrl(terms_url),) if terms_url else (),
                discovery_evidence=(
                    DiscoveryEvidence(
                        kind=DiscoveryKind.OPENAPI_DIRECTORY,
                        catalog_url=HttpUrl(self.DIRECTORY_URL),
                        catalog_record_id=f"{provider}:{preferred or 'unknown'}",
                        directory_license="CC0/fair-use definitions; target data rights separate",
                    ),
                ),
                first_seen_at=now,
                last_seen_at=now,
                metadata_hash=f"sha256:{'0' * 64}",
            )
            candidates.append(_finish_candidate(candidate))
        return tuple(candidates)
