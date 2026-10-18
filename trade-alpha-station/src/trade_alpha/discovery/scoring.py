"""Explainable prioritization for M11 candidates."""

from __future__ import annotations

import re

from .models import CandidateScore, PublisherKind, ScoreSignals, SourceCandidate

WEIGHTS: dict[str, float] = {
    "relevance": 0.25,
    "coverage": 0.15,
    "freshness": 0.15,
    "machine_readability": 0.15,
    "publisher_authority": 0.10,
    "stability": 0.10,
    "license_clarity": 0.10,
}


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[\w-]+", value.casefold()) if len(token) > 1}


def derive_signals(candidate: SourceCandidate, business_terms: tuple[str, ...]) -> ScoreSignals:
    wanted = set().union(*(_tokens(item) for item in business_terms)) if business_terms else set()
    corpus = _tokens(" ".join((candidate.title, candidate.description, *candidate.domains)))
    relevance = 50.0 if not wanted else min(100.0, 100.0 * len(wanted & corpus) / len(wanted))
    coverage = min(
        100.0,
        25.0 + 10.0 * len(candidate.geographies) + 10.0 * len(candidate.distributions),
    )
    frequency = (candidate.update_frequency or "").casefold()
    freshness = 90.0 if any(x in frequency for x in ("real", "daily", "hour")) else 75.0
    if any(x in frequency for x in ("week", "month")):
        freshness = 60.0
    if not frequency:
        freshness = 30.0
    formats = {item.format for item in candidate.distributions}
    readable = {"json", "jsonl", "csv", "xml", "parquet", "openapi", "api"}
    machine_readability = 95.0 if formats & readable else 35.0
    authority = {
        PublisherKind.OFFICIAL: 95.0,
        PublisherKind.COMMERCIAL: 75.0,
        PublisherKind.COMMUNITY: 65.0,
        PublisherKind.UNKNOWN: 35.0,
    }[candidate.publisher_kind]
    stability = 80.0 if str(candidate.canonical_url).startswith("https://") else 30.0
    clarity_fields = sum(
        (
            candidate.terms_url is not None,
            bool(candidate.license_claim),
            bool(candidate.license_evidence_urls),
        )
    )
    license_clarity = {0: 0.0, 1: 35.0, 2: 70.0, 3: 90.0}[clarity_fields]
    return ScoreSignals(
        relevance=relevance,
        coverage=coverage,
        freshness=freshness,
        machine_readability=machine_readability,
        publisher_authority=authority,
        stability=stability,
        license_clarity=license_clarity,
    )


def score_candidate(candidate: SourceCandidate, business_terms: tuple[str, ...]) -> CandidateScore:
    signals = derive_signals(candidate, business_terms)
    values = signals.model_dump()
    priority = round(sum(values[name] * weight for name, weight in WEIGHTS.items()), 4)
    return CandidateScore(
        candidate_id=candidate.candidate_id,
        signals=signals,
        weights=dict(WEIGHTS),
        priority=priority,
    )
