"""Append-only in-memory persistence for EP-08."""

from __future__ import annotations

from datetime import datetime

from trade_alpha.contracts.common import require_utc

from .models import RiskDecision, TradeRiskAssessmentSnapshot


class InMemoryTradeRiskRepository:
    def __init__(self) -> None:
        self._assessments: dict[str, TradeRiskAssessmentSnapshot] = {}
        self._decisions: dict[str, RiskDecision] = {}

    def save_assessment(
        self, assessment: TradeRiskAssessmentSnapshot
    ) -> TradeRiskAssessmentSnapshot:
        prior = self._assessments.get(assessment.assessment_id)
        if prior is not None and prior != assessment:
            raise ValueError("risk assessment is immutable")
        duplicate = next(
            (
                item
                for item in self._assessments.values()
                if item.result_fingerprint == assessment.result_fingerprint
            ),
            None,
        )
        if duplicate is not None:
            return duplicate
        self._assessments[assessment.assessment_id] = assessment
        return assessment

    def get_assessment(self, assessment_id: str) -> TradeRiskAssessmentSnapshot | None:
        return self._assessments.get(assessment_id)

    def is_lineage_tip_as_of(self, assessment_id: str, as_of: datetime) -> bool:
        moment = require_utc(as_of)
        value = self._assessments.get(assessment_id)
        if value is None:
            return False
        candidates = tuple(
            item
            for item in self._assessments.values()
            if item.supplier_id == value.supplier_id
            and item.product_spec_version_id == value.product_spec_version_id
            and item.as_of <= moment
            and item.created_at <= moment
        )
        tip = max(
            candidates,
            key=lambda item: (item.as_of, item.created_at, item.assessment_id),
            default=None,
        )
        return tip is not None and tip.assessment_id == assessment_id

    def save_decision(self, decision: RiskDecision) -> RiskDecision:
        prior = self._decisions.get(decision.decision_id)
        if prior is not None and prior != decision:
            raise ValueError("risk decision is immutable")
        self._decisions.setdefault(decision.decision_id, decision)
        return self._decisions[decision.decision_id]

    def decisions_for(
        self, assessment_id: str, *, as_of: datetime | None = None
    ) -> tuple[RiskDecision, ...]:
        moment = require_utc(as_of) if as_of is not None else None
        return tuple(
            sorted(
                (
                    item
                    for item in self._decisions.values()
                    if item.assessment_id == assessment_id
                    and (moment is None or item.decided_at <= moment)
                ),
                key=lambda item: (item.decided_at, item.decision_id),
            )
        )


def effective_risk_decisions(decisions: tuple[RiskDecision, ...]) -> tuple[RiskDecision, ...]:
    by_id = {item.decision_id: item for item in decisions}
    superseded: set[str] = set()
    for decision in decisions:
        if decision.supersedes_decision_id is None:
            continue
        prior = by_id.get(decision.supersedes_decision_id)
        if prior is None or prior.assessment_id != decision.assessment_id:
            raise ValueError("risk decision supersedes an unrelated decision")
        superseded.add(prior.decision_id)
    effective = tuple(item for item in decisions if item.decision_id not in superseded)
    finding_ids = [finding_id for item in effective for finding_id in item.finding_ids]
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("multiple effective risk decisions cover one finding")
    return effective
