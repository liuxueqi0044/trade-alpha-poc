"""Constraint-first research resource allocation, not trade portfolio construction."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Mandate


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    opportunity_id: str
    country: str
    hs_chapter: str
    score: float
    confidence: float
    research_hours: float
    sample_cost: float
    trial_loss: float
    exploration: bool = False


@dataclass(frozen=True, slots=True)
class AllocationResult:
    selected: tuple[ResourceRequest, ...]
    rejected: dict[str, str]
    research_hours_used: float
    sample_cost_used: float
    trial_loss_used: float
    capital_used: float


def allocate(mandate: Mandate, requests: list[ResourceRequest]) -> AllocationResult:
    selected: list[ResourceRequest] = []
    rejected: dict[str, str] = {}
    country_cost: dict[str, float] = {}
    chapter_cost: dict[str, float] = {}
    research_used = sample_used = trial_loss_used = capital_used = exploration_used = 0.0
    for request in sorted(requests, key=lambda item: item.score, reverse=True):
        cost = request.sample_cost + request.trial_loss
        if request.confidence < mandate.minimum_data_confidence:
            rejected[request.opportunity_id] = "confidence_below_minimum"
        elif request.trial_loss > mandate.max_trial_loss:
            rejected[request.opportunity_id] = "trial_loss_exceeds_cap"
        elif capital_used + cost > mandate.capital_budget:
            rejected[request.opportunity_id] = "capital_budget"
        elif research_used + request.research_hours > mandate.research_hours_budget:
            rejected[request.opportunity_id] = "research_hours_budget"
        elif sample_used + request.sample_cost > mandate.sample_budget:
            rejected[request.opportunity_id] = "sample_budget"
        elif (
            country_cost.get(request.country, 0.0) + cost
            > mandate.capital_budget * mandate.max_country_exposure
        ):
            rejected[request.opportunity_id] = "country_exposure"
        elif (
            chapter_cost.get(request.hs_chapter, 0.0) + cost
            > mandate.capital_budget * mandate.max_hs_chapter_exposure
        ):
            rejected[request.opportunity_id] = "hs_chapter_exposure"
        elif (
            request.exploration
            and exploration_used + cost > mandate.capital_budget * mandate.exploration_budget_share
        ):
            rejected[request.opportunity_id] = "exploration_budget"
        else:
            selected.append(request)
            research_used += request.research_hours
            sample_used += request.sample_cost
            trial_loss_used += request.trial_loss
            capital_used += cost
            country_cost[request.country] = country_cost.get(request.country, 0.0) + cost
            chapter_cost[request.hs_chapter] = chapter_cost.get(request.hs_chapter, 0.0) + cost
            if request.exploration:
                exploration_used += cost
    return AllocationResult(
        tuple(selected),
        rejected,
        research_hours_used=research_used,
        sample_cost_used=sample_used,
        trial_loss_used=trial_loss_used,
        capital_used=capital_used,
    )
