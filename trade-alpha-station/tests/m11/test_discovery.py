from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from trade_alpha.discovery import (
    APIsGuruAdapter,
    CandidateRegistry,
    CandidateState,
    CKANAdapter,
    DiscoveryEvidence,
    DiscoveryKind,
    GoldenValidation,
    PolicyReview,
    PublisherKind,
    SourceCandidate,
    SourceDistribution,
    draft_source_record,
    enable_after_golden,
    load_seed_catalog,
    metadata_hash,
    policy_approved_bundle,
    score_candidate,
    stable_candidate_id,
)
from trade_alpha.discovery.models import candidate_metadata
from trade_alpha.governance import LicenseRegistry
from trade_alpha.ingestion import SourceRegistry

NOW = datetime(2026, 8, 25, tzinfo=UTC)


class FakeFetcher:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((url, params))
        return self.payload


def candidate(
    *,
    canonical_url: str = "https://data.example.org/dataset/trade",
    title: str = "Global Trade Flows",
    state: CandidateState = CandidateState.DISCOVERED,
    evidence_kind: DiscoveryKind = DiscoveryKind.SEED,
) -> SourceCandidate:
    item = SourceCandidate(
        candidate_id=stable_candidate_id(canonical_url),
        title=title,
        description="Monthly trade and procurement data by HS code",
        publisher="Official Statistics Office",
        publisher_kind=PublisherKind.OFFICIAL,
        canonical_url=canonical_url,
        domains=("trade", "procurement"),
        distributions=(
            SourceDistribution(
                url="https://data.example.org/api/trade",
                format="json",
                media_type="application/json",
            ),
        ),
        geographies=("global",),
        update_frequency="daily",
        auth_class="none",
        terms_url="https://data.example.org/terms",
        license_claim="CC-BY-4.0",
        license_evidence_urls=("https://data.example.org/terms",),
        discovery_evidence=(
            DiscoveryEvidence(
                kind=evidence_kind,
                catalog_url="https://catalog.example.org/",
                catalog_record_id=f"record-{evidence_kind}",
            ),
        ),
        first_seen_at=NOW,
        last_seen_at=NOW,
        metadata_hash=f"sha256:{'0' * 64}",
        state=state,
    )
    return item.model_copy(update={"metadata_hash": metadata_hash(candidate_metadata(item))})


def approved_review(item: SourceCandidate) -> PolicyReview:
    return PolicyReview(
        candidate_id=item.candidate_id,
        approved=True,
        reviewer="legal-reviewer",
        reviewed_at=NOW,
        reason="official terms reviewed",
        terms_url="https://data.example.org/terms",
        api_use_status="approved",
        caching_policy="raw cache allowed for 30 days",
        retention_policy="raw 30 days; derived 3 years",
        redistribution_status="allowed",
        data_owner="Official Statistics Office",
        data_license_id="CC-BY-4.0",
        attribution="Source: Official Statistics Office",
        commercial_use="allowed",
        derivatives="allowed",
        data_redistribution="allowed",
        region_restrictions="none",
        evidence_urls=("https://data.example.org/terms",),
    )


def test_ckan_package_search_becomes_candidate() -> None:
    fetcher = FakeFetcher(
        {
            "success": True,
            "result": {
                "results": [
                    {
                        "id": "trade-1",
                        "title": "Trade by HS",
                        "notes": "Monthly official flows",
                        "organization": {"title": "Statistics Office"},
                        "tags": [{"name": "trade"}, {"name": "HS"}],
                        "license_id": "cc-by",
                        "license_url": "https://catalog.example.org/license",
                        "resources": [
                            {
                                "url": "https://catalog.example.org/trade.csv",
                                "format": "CSV",
                                "mimetype": "text/csv",
                            }
                        ],
                    }
                ]
            },
        }
    )
    results = CKANAdapter("https://catalog.example.org", fetcher).discover(
        "trade", max_results=10, observed_at=NOW
    )

    assert len(results) == 1
    assert results[0].domains == ("hs", "trade")
    assert results[0].distributions[0].format == "csv"
    assert results[0].metadata_hash != f"sha256:{'0' * 64}"
    assert fetcher.calls[0][1] == {"q": "trade", "rows": 10}


def test_openapi_directory_keeps_definition_rights_separate() -> None:
    fetcher = FakeFetcher(
        {
            "trade.example.com": {
                "preferred": "1.0",
                "versions": {
                    "1.0": {
                        "swaggerUrl": "https://api.apis.guru/v2/specs/trade/1.0/openapi.json",
                        "info": {
                            "title": "Trade Signals API",
                            "description": "Commercial trade demand signals",
                            "termsOfService": "https://trade.example.com/terms",
                            "license": {"name": "Apache-2.0"},
                            "x-apisguru-categories": ["data"],
                        },
                    }
                },
            }
        }
    )
    results = APIsGuruAdapter(fetcher).discover("trade", observed_at=NOW)

    assert len(results) == 1
    assert results[0].license_claim is None
    assert results[0].discovery_evidence[0].directory_license is not None
    assert results[0].distributions[0].openapi_url is not None


def test_registry_deduplicates_canonical_url_and_merges_provenance() -> None:
    registry = CandidateRegistry()
    first = registry.upsert(candidate(evidence_kind=DiscoveryKind.CKAN))
    second = registry.upsert(candidate(evidence_kind=DiscoveryKind.OPENAPI_DIRECTORY))

    assert first.candidate_id == second.candidate_id
    assert len(second.discovery_evidence) == 2
    assert len(registry.history(first.candidate_id)) == 2


def test_illegal_state_transition_is_blocked() -> None:
    registry = CandidateRegistry()
    item = registry.upsert(candidate())

    with pytest.raises(ValueError, match="illegal candidate transition"):
        registry.move(item.candidate_id, CandidateState.ENABLED)


def test_discovered_candidate_creates_disabled_source_draft() -> None:
    draft = draft_source_record(candidate(), owner="data-platform")

    assert draft.status == "disabled"
    assert draft.commercial_use is None
    assert draft.redistribution is None
    assert draft.retention_policy is None
    assert draft.parser_profile.startswith("m11-draft:")


def test_approved_review_requires_every_legal_field() -> None:
    item = candidate()
    with pytest.raises(ValidationError, match="approved review missing"):
        PolicyReview(
            candidate_id=item.candidate_id,
            approved=True,
            reviewer="legal",
            reviewed_at=NOW,
            reason="incomplete",
        )


def test_policy_then_golden_are_independent_gates() -> None:
    item = candidate(state=CandidateState.APPROVED)
    bundle = policy_approved_bundle(item, approved_review(item), owner="data-platform")
    license_registry = LicenseRegistry((bundle.governance_record,))

    assert license_registry.source_policy(bundle.source_record.source_id).allowed
    assert bundle.source_record.status == "disabled"
    assert not bundle.connector.enabled

    validation = GoldenValidation(
        candidate_id=item.candidate_id,
        validation_id="golden-001",
        passed=True,
        checks={"schema": True, "primary_key": True, "freshness": True},
        sample_artifact_ids=("artifact-001",),
        reviewer="quality-reviewer",
        checked_at=NOW,
    )
    enabled = enable_after_golden(bundle, validation)
    source_registry = SourceRegistry(
        (enabled.source_record,), policy_hooks=(license_registry.source_policy,)
    )

    assert enabled.source_record.status == "enabled"
    assert enabled.connector.enabled
    assert enabled.candidate.state is CandidateState.ENABLED
    assert source_registry.policy_gate(enabled.source_record.source_id).allowed


def test_failed_golden_cannot_enable() -> None:
    item = candidate(state=CandidateState.APPROVED)
    bundle = policy_approved_bundle(item, approved_review(item), owner="data-platform")
    failed = GoldenValidation(
        candidate_id=item.candidate_id,
        validation_id="golden-failed",
        passed=False,
        checks={"schema": False},
        reviewer="quality-reviewer",
        checked_at=NOW,
    )

    with pytest.raises(PermissionError, match="passed Golden"):
        enable_after_golden(bundle, failed)


def test_scoring_is_explainable_and_does_not_approve() -> None:
    item = candidate()
    score = score_candidate(item, ("trade", "procurement", "hs"))

    expected = sum(
        score.signals.model_dump()[name] * weight for name, weight in score.weights.items()
    )
    assert score.priority == round(expected, 4)
    assert item.state is CandidateState.DISCOVERED
    assert set(score.weights) == set(type(score.signals).model_fields)


def test_material_change_returns_enabled_candidate_to_policy_review() -> None:
    registry = CandidateRegistry()
    enabled = registry.upsert(candidate(state=CandidateState.ENABLED))
    changed = candidate(state=CandidateState.DISCOVERED).model_copy(
        update={"license_claim": "new restrictive terms"}
    )
    changed = changed.model_copy(
        update={"metadata_hash": metadata_hash(candidate_metadata(changed))}
    )

    revised = registry.upsert(changed)

    assert revised.state is CandidateState.POLICY_PENDING
    assert revised.revision == enabled.revision + 1
    assert registry.history(enabled.candidate_id)[0].state is CandidateState.ENABLED


def test_repeated_probe_failure_marks_stale_without_deleting_history() -> None:
    registry = CandidateRegistry()
    item = registry.upsert(candidate())
    assert registry.record_probe_failure(item.candidate_id).state is CandidateState.DISCOVERED
    assert registry.record_probe_failure(item.candidate_id).state is CandidateState.DISCOVERED
    stale = registry.record_probe_failure(item.candidate_id)

    assert stale.state is CandidateState.STALE
    assert len(registry.history(item.candidate_id)) == 2


def test_curated_seed_catalog_is_valid_and_deny_by_default() -> None:
    project = Path(__file__).resolve().parents[2]
    seeds = load_seed_catalog(
        project / "configs" / "source-radar" / "initial-sources.yaml", observed_at=NOW
    )

    assert len(seeds) == 10
    assert {item.publisher_kind for item in seeds} >= {
        PublisherKind.OFFICIAL,
        PublisherKind.COMMUNITY,
    }
    assert all(item.state is CandidateState.DISCOVERED for item in seeds)
    assert all(
        draft_source_record(item, owner="data-platform").status == "disabled"
        for item in seeds
    )
