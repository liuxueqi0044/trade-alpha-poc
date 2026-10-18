"""Model live gate; producing a plan is not authorization to act externally."""

from __future__ import annotations

from dataclasses import dataclass

from .models import ModelApproval


class LiveGateError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    model_id: str
    owner: str
    maximum_budget: float
    failure_conditions: tuple[str, ...]
    rollback: str
    frozen_report_ref: str
    scope: str


def create_limited_live_plan(approval: ModelApproval) -> ExperimentPlan:
    if approval.state != "paper_approved":
        raise LiveGateError("model must be paper_approved")
    if not approval.owner or not approval.max_budget or approval.max_budget <= 0:
        raise LiveGateError("limited live requires owner and positive maximum budget")
    if (
        not approval.failure_conditions
        or not approval.rollback
        or not approval.frozen_report_ref
        or not approval.scope
    ):
        raise LiveGateError(
            "limited live requires failure conditions, rollback, frozen report, and scope"
        )
    return ExperimentPlan(
        model_id=approval.model_id,
        owner=approval.owner,
        maximum_budget=approval.max_budget,
        failure_conditions=approval.failure_conditions,
        rollback=approval.rollback,
        frozen_report_ref=approval.frozen_report_ref,
        scope=approval.scope,
    )
