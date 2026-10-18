"""Configuration-driven universe construction with auditable exclusions."""

from __future__ import annotations

from .models import Mandate, Opportunity, UniverseDecision


def build_universe(mandate: Mandate, opportunities: list[Opportunity]) -> list[UniverseDecision]:
    decisions: list[UniverseDecision] = []
    for opportunity in opportunities:
        reasons: list[str] = []
        if opportunity.hs_chapter in mandate.excluded_hs_chapters:
            reasons.append("excluded_hs_chapter")
        if opportunity.regulatory_class in mandate.excluded_regulatory_classes:
            reasons.append("excluded_regulatory_class")
        if opportunity.country_risk > mandate.country_risk_max:
            reasons.append("country_risk_exceeds_mandate")
        if opportunity.market_value < mandate.minimum_market_value:
            reasons.append("market_value_below_minimum")
        if opportunity.data_confidence < mandate.minimum_data_confidence:
            reasons.append("data_confidence_below_minimum")
        decisions.append(
            UniverseDecision(
                opportunity_id=opportunity.opportunity_id,
                included=not reasons,
                reasons=tuple(reasons or ["included_by_mandate"]),
                universe_version=mandate.version,
                as_of=opportunity.as_of,
            )
        )
    return decisions
