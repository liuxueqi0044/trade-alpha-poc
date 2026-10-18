"""Deterministic matching, with an optional boundary for Splink deployments."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rapidfuzz.fuzz import ratio

from .models import EntityKind, LinkState
from .normalize import NormalizedIdentity, blocking_keys


@dataclass(frozen=True)
class MatchThresholds:
    review_lower: float = 0.72
    auto_link_threshold: float = 0.985
    auto_link_enabled: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.review_lower <= self.auto_link_threshold <= 1:
            raise ValueError("thresholds require 0 <= review_lower <= auto_link_threshold <= 1")


@dataclass(frozen=True)
class CandidatePair:
    left_id: str
    right_id: str
    shared_blocks: tuple[str, ...]


@dataclass(frozen=True)
class MatchDecision:
    probability: float
    state: LinkState
    reason: str


@dataclass(frozen=True)
class LinkEvaluation:
    """Auditable precision report for a frozen human-labelled fixture."""

    labeled_count: int
    auto_link_count: int
    true_positives: int
    false_positives: int

    @property
    def auto_link_precision(self) -> float:
        return self.true_positives / self.auto_link_count if self.auto_link_count else 1.0


class Matcher(Protocol):
    def score(self, left: NormalizedIdentity, right: NormalizedIdentity) -> float: ...


class RapidFuzzMatcher:
    """Explainable high-precision scorer; score is deterministic and bounded."""

    version = "rapidfuzz/1.0.0"

    def __init__(self, thresholds: MatchThresholds | None = None) -> None:
        self.thresholds = thresholds or MatchThresholds()

    def candidates(
        self, entity_type: EntityKind, records: Sequence[tuple[str, NormalizedIdentity]]
    ) -> tuple[CandidatePair, ...]:
        indexed: dict[str, list[str]] = {}
        for record_id, identity in records:
            for key in blocking_keys(entity_type, identity):
                indexed.setdefault(key, []).append(record_id)
        pairs: dict[tuple[str, str], set[str]] = {}
        for key, ids in indexed.items():
            for position, left_id in enumerate(sorted(set(ids))):
                for right_id in sorted(set(ids))[position + 1 :]:
                    pair = (left_id, right_id)
                    pairs.setdefault(pair, set()).add(key)
        return tuple(
            CandidatePair(a, b, tuple(sorted(keys))) for (a, b), keys in sorted(pairs.items())
        )

    def score(self, left: NormalizedIdentity, right: NormalizedIdentity) -> float:
        # Exact identifiers dominate. Conflicting exact identifiers prevent a
        # false merge even when display names happen to coincide.
        if left.registration_number and right.registration_number:
            return 1.0 if left.registration_number == right.registration_number else 0.0
        exact_contacts = {
            value
            for value in (left.domain, left.email_domain, left.phone)
            if value and value in (right.domain, right.email_domain, right.phone)
        }
        if exact_contacts:
            return 0.995
        if left.country and right.country and left.country != right.country:
            return 0.0
        name = ratio(left.normalized_name, right.normalized_name) / 100
        location_bonus = (
            0.04
            if (
                (left.postal_code and left.postal_code == right.postal_code)
                or (left.h3 and left.h3 == right.h3)
            )
            else 0.0
        )
        return round(min(0.98, name * 0.94 + location_bonus), 6)

    def decide(self, left: NormalizedIdentity, right: NormalizedIdentity) -> MatchDecision:
        probability = self.score(left, right)
        if self.thresholds.auto_link_enabled and probability >= self.thresholds.auto_link_threshold:
            return MatchDecision(
                probability, LinkState.AUTO_LINKED, "calibrated high-precision threshold"
            )
        if probability >= self.thresholds.review_lower:
            return MatchDecision(
                probability, LinkState.REVIEW, "gray-zone candidate requires human review"
            )
        return MatchDecision(probability, LinkState.SEPARATE, "below review threshold")

    @staticmethod
    def calibrate(
        labeled: Sequence[tuple[float, bool]], minimum_precision: float = 0.99
    ) -> MatchThresholds:
        """Choose the lowest threshold satisfying precision, conservatively.

        No qualifying candidate explicitly disables automatic linking; high
        probability pairs remain available to a human review queue.
        """
        # Ascending is intentional: the first qualifying cut has the greatest
        # coverage while still meeting the stated precision constraint.
        for threshold in sorted({score for score, _ in labeled}):
            selected = [truth for score, truth in labeled if score >= threshold]
            if selected and sum(selected) / len(selected) >= minimum_precision:
                return MatchThresholds(
                    auto_link_threshold=threshold,
                    review_lower=min(0.72, max(0.0, threshold - 0.001)),
                )
        return MatchThresholds(
            auto_link_threshold=1.0,
            review_lower=0.72,
            auto_link_enabled=False,
        )


def evaluate_labeled_pairs(
    matcher: RapidFuzzMatcher,
    labeled: Sequence[tuple[NormalizedIdentity, NormalizedIdentity, bool]],
) -> LinkEvaluation:
    """Evaluate only decisions allowed to auto-link; review is not a fact."""
    automatic = [
        truth
        for left, right, truth in labeled
        if matcher.decide(left, right).state is LinkState.AUTO_LINKED
    ]
    true_positives = sum(automatic)
    return LinkEvaluation(
        labeled_count=len(labeled),
        auto_link_count=len(automatic),
        true_positives=true_positives,
        false_positives=len(automatic) - true_positives,
    )


class SplinkAdapter:
    """Optional adapter seam. The core never imports Splink unless requested."""

    def __init__(self, linker: object) -> None:
        self.linker = linker

    @classmethod
    def available(cls) -> bool:
        try:
            import splink  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            return False
        return True

    def score(self, left: NormalizedIdentity, right: NormalizedIdentity) -> float:
        raise NotImplementedError("configure a Splink comparison model in the deployment adapter")
