"""Validated loader for manually curated source-radar seeds."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import HttpUrl

from trade_alpha.contracts.common import utc_now

from .models import (
    DiscoveryEvidence,
    DiscoveryKind,
    PublisherKind,
    SourceCandidate,
    SourceDistribution,
    candidate_metadata,
    metadata_hash,
    stable_candidate_id,
)


def _required_text(item: Mapping[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"seed missing {field}")
    return value.strip()


def load_seed_catalog(
    path: str | Path, *, observed_at: datetime | None = None, max_sources: int = 200
) -> tuple[SourceCandidate, ...]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("sources"), list):
        raise ValueError("seed catalog requires a sources list")
    raw_sources = payload["sources"]
    if len(raw_sources) > max_sources:
        raise ValueError("seed catalog exceeds source budget")
    now = observed_at or utc_now()
    candidates: list[SourceCandidate] = []
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            raise ValueError("seed source must be an object")
        canonical_url = _required_text(raw, "canonical_url")
        distribution_rows = raw.get("distributions")
        if not isinstance(distribution_rows, list) or not distribution_rows:
            raise ValueError("seed source requires distributions")
        distributions = tuple(
            SourceDistribution(
                url=HttpUrl(_required_text(row, "url")),
                format=_required_text(row, "format"),
                media_type=row.get("media_type")
                if isinstance(row.get("media_type"), str)
                else None,
                openapi_url=HttpUrl(str(row.get("openapi_url")))
                if isinstance(row.get("openapi_url"), str)
                else None,
            )
            for row in distribution_rows
            if isinstance(row, Mapping)
        )
        terms_url = raw.get("terms_url") if isinstance(raw.get("terms_url"), str) else None
        evidence_urls = (terms_url,) if terms_url else ()
        candidate = SourceCandidate(
            candidate_id=stable_candidate_id(canonical_url),
            title=_required_text(raw, "title"),
            description=str(raw.get("description") or ""),
            publisher=_required_text(raw, "publisher"),
            publisher_kind=PublisherKind(str(raw.get("publisher_kind") or "unknown")),
            canonical_url=HttpUrl(canonical_url),
            domains=tuple(str(item) for item in raw.get("domains", []) if isinstance(item, str)),
            distributions=distributions,
            geographies=tuple(
                str(item) for item in raw.get("geographies", []) if isinstance(item, str)
            ),
            update_frequency=str(raw.get("update_frequency"))
            if raw.get("update_frequency")
            else None,
            auth_class=str(raw.get("auth_class") or "unknown"),
            terms_url=HttpUrl(terms_url) if terms_url else None,
            license_claim=str(raw.get("license_claim")) if raw.get("license_claim") else None,
            license_evidence_urls=tuple(HttpUrl(item) for item in evidence_urls),
            discovery_evidence=(
                DiscoveryEvidence(
                    kind=DiscoveryKind.SEED,
                    catalog_url=HttpUrl(canonical_url),
                    catalog_record_id=_required_text(raw, "seed_id"),
                ),
            ),
            first_seen_at=now,
            last_seen_at=now,
            metadata_hash=f"sha256:{'0' * 64}",
        )
        candidates.append(
            candidate.model_copy(
                update={"metadata_hash": metadata_hash(candidate_metadata(candidate))}
            )
        )
    return tuple(candidates)
