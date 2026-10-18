"""Transparent, recomputable alpha/risk/confidence scoring."""

from __future__ import annotations

from .models import FeatureDefinition, ScoreBreakdown


def score_opportunity(
    opportunity_id: str,
    definitions: list[FeatureDefinition],
    vector: dict[str, float | None],
    missing: dict[str, int],
    *,
    risk_components: dict[str, float],
    confidence_components: dict[str, float],
    score_version: str,
) -> ScoreBreakdown:
    contributions: dict[str, float] = {}
    for definition in definitions:
        value = vector[definition.feature_name]
        if value is None:
            contributions[definition.feature_name] = 0.0
            continue
        direction = 1.0 if definition.expected_direction == "positive" else -1.0
        contributions[definition.feature_name] = direction * definition.weight * value
    raw_alpha = sum(contributions.values())
    risk_penalty = sum(risk_components.values())
    confidence = 1.0
    for value in confidence_components.values():
        confidence *= max(0.0, min(1.0, value))
    final_score = (raw_alpha - risk_penalty) * confidence
    return ScoreBreakdown(
        opportunity_id=opportunity_id,
        raw_alpha=raw_alpha,
        risk_penalty=risk_penalty,
        confidence=confidence,
        final_score=final_score,
        feature_contributions=contributions,
        missing_indicators=missing,
        score_version=score_version,
    )


def recompute_score(score: ScoreBreakdown) -> float:
    return (sum(score.feature_contributions.values()) - score.risk_penalty) * score.confidence
