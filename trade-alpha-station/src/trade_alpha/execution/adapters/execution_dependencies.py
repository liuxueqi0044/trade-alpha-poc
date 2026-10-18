"""Repository-backed EP-07/EP-08 readers for EP-09 and EP-10."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.commercial_risk import (
    RiskDisposition,
    TradeRiskAssessmentSnapshot,
    assessment_content_fingerprint,
)
from trade_alpha.execution.costing import CostScenarioSnapshot
from trade_alpha.execution.operational_readiness import (
    ExecutionDependencyKind,
    ExecutionDependencySnapshot,
)


class CostSnapshotRepository(Protocol):
    def snapshot(self, snapshot_id: str) -> CostScenarioSnapshot | None: ...
    def is_lineage_tip_as_of(self, snapshot_id: str, as_of: datetime) -> bool: ...


class RiskSnapshotRepository(Protocol):
    def get_assessment(self, assessment_id: str) -> TradeRiskAssessmentSnapshot | None: ...
    def is_lineage_tip_as_of(self, assessment_id: str, as_of: datetime) -> bool: ...


class CostScenarioDependencyReader:
    def __init__(self, repository: CostSnapshotRepository) -> None:
        self.repository = repository

    def get_saved_as_of(
        self, dependency_id: str, as_of: datetime
    ) -> ExecutionDependencySnapshot | None:
        moment = require_utc(as_of)
        value = self.repository.snapshot(dependency_id)
        if value is None or value.as_of > moment:
            return None
        is_tip = self.repository.is_lineage_tip_as_of(dependency_id, moment)
        if not is_tip:
            return None
        complete = (
            not value.unresolved_required_components
            and value.landed_cost_per_unit is not None
            and value.minimum_sale_price is not None
        )
        return ExecutionDependencySnapshot(
            dependency_id=value.cost_scenario_snapshot_id,
            kind=ExecutionDependencyKind.COST,
            content_fingerprint=value.content_fingerprint,
            as_of=value.as_of,
            complete=complete,
            hard_block=not complete,
            is_lineage_tip=is_tip,
        )


class RiskAssessmentDependencyReader:
    def __init__(self, repository: RiskSnapshotRepository) -> None:
        self.repository = repository

    def get_saved_as_of(
        self, dependency_id: str, as_of: datetime
    ) -> ExecutionDependencySnapshot | None:
        moment = require_utc(as_of)
        value = self.repository.get_assessment(dependency_id)
        if value is None or value.as_of > moment or value.created_at > moment:
            return None
        is_tip = self.repository.is_lineage_tip_as_of(dependency_id, moment)
        if not is_tip:
            return None
        if value.result_fingerprint != assessment_content_fingerprint(value):
            raise ValueError("risk assessment content fingerprint mismatch")
        expired = value.valid_until is not None and value.valid_until < moment
        hard_block = expired or value.disposition in {
            RiskDisposition.BLOCKED,
            RiskDisposition.EXPIRED,
        }
        return ExecutionDependencySnapshot(
            dependency_id=value.assessment_id,
            kind=ExecutionDependencyKind.RISK,
            content_fingerprint=value.result_fingerprint,
            as_of=value.as_of,
            valid_until=value.valid_until,
            complete=not hard_block,
            hard_block=hard_block,
            is_lineage_tip=is_tip,
        )
