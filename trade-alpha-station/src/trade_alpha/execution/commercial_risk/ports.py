"""Narrow source, repository, authorization, and audit ports for EP-08."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.costing.models import CostScenarioSnapshot
from trade_alpha.execution.specifications.models import ProductSpecVersion
from trade_alpha.execution.supplier_quotes.models import (
    QuoteNormalizationRun,
    SupplierComparisonMatrix,
    SupplierQuoteSnapshot,
)
from trade_alpha.execution.suppliers.models import SupplierQualificationSnapshot
from trade_alpha.governance import AppendOnlyAuditLog, Identity, Role

from .models import (
    RiskAssessmentInput,
    RiskDecision,
    RiskPolicyVersion,
    TradeRiskAssessmentSnapshot,
)


class TradeRiskSourceReader(Protocol):
    """Read exact, immutable upstream snapshots; never perform external writes."""

    def read(self, *, as_of: datetime) -> RiskAssessmentInput: ...


class ProductSpecRiskSource(Protocol):
    def get_version(self, spec_version_id: str) -> ProductSpecVersion | None: ...


class QualificationRiskSource(Protocol):
    def snapshot_as_of(
        self, supplier_id: str, product_spec_version_id: str, as_of: datetime
    ) -> SupplierQualificationSnapshot | None: ...


class QuoteRiskSource(Protocol):
    def snapshot(self, snapshot_id: str) -> SupplierQuoteSnapshot | None: ...
    def matrix(self, matrix_id: str) -> SupplierComparisonMatrix | None: ...


class NormalizationRiskSource(Protocol):
    def normalization_run(self, run_id: str) -> QuoteNormalizationRun | None: ...


class CostRiskSource(Protocol):
    def snapshot(self, snapshot_id: str) -> CostScenarioSnapshot | None: ...


class TradeRiskRepositoryPort(Protocol):
    def save_assessment(
        self, assessment: TradeRiskAssessmentSnapshot
    ) -> TradeRiskAssessmentSnapshot: ...

    def get_assessment(self, assessment_id: str) -> TradeRiskAssessmentSnapshot | None: ...

    def save_decision(self, decision: RiskDecision) -> RiskDecision: ...

    def decisions_for(
        self, assessment_id: str, *, as_of: datetime | None = None
    ) -> tuple[RiskDecision, ...]: ...


class RiskDecisionAuthorizationPort(Protocol):
    def may_decide(self, actor_id: str, actor_role: str, action: str) -> bool: ...


class ExecutionAuditPort(Protocol):
    def append(
        self,
        *,
        actor_id: str,
        client_identity: str,
        action: str,
        resource: str,
        before: Any,
        after: Any,
        occurred_at: datetime,
        reason: str,
        correlation_id: str,
    ) -> Any: ...


class RepositoryTradeRiskSourceReader:
    """Rehydrate risk inputs from authoritative upstream repositories by exact ID."""

    def __init__(
        self,
        references: RiskAssessmentInput,
        *,
        product_specs: ProductSpecRiskSource,
        qualifications: QualificationRiskSource,
        quotes: QuoteRiskSource,
        normalizations: NormalizationRiskSource,
        costs: CostRiskSource,
    ) -> None:
        if type(references) is not RiskAssessmentInput:
            raise TypeError("risk source references have an invalid type")
        self.references = references
        self.product_specs = product_specs
        self.qualifications = qualifications
        self.quotes = quotes
        self.normalizations = normalizations
        self.costs = costs

    def read(self, *, as_of: datetime) -> RiskAssessmentInput:
        moment = require_utc(as_of)
        references = self.references
        product_spec = self.product_specs.get_version(references.product_spec.spec_version_id)
        qualification = self.qualifications.snapshot_as_of(
            references.qualification.supplier_id,
            references.product_spec.spec_version_id,
            moment,
        )
        quote_snapshots = tuple(
            self.quotes.snapshot(item.supplier_quote_snapshot_id)
            for item in references.quote_snapshots
        )
        normalization = self.normalizations.normalization_run(
            references.normalization_run.normalization_run_id
        )
        comparison = self.quotes.matrix(references.comparison_matrix.comparison_matrix_id)
        cost_scenarios = tuple(
            self.costs.snapshot(item.cost_scenario_snapshot_id)
            for item in references.cost_scenarios
        )
        if (
            type(product_spec) is not ProductSpecVersion
            or type(qualification) is not SupplierQualificationSnapshot
            or qualification.qualification_snapshot_id
            != references.qualification.qualification_snapshot_id
            or any(type(item) is not SupplierQuoteSnapshot for item in quote_snapshots)
            or type(normalization) is not QuoteNormalizationRun
            or type(comparison) is not SupplierComparisonMatrix
            or any(type(item) is not CostScenarioSnapshot for item in cost_scenarios)
        ):
            raise ValueError("risk source repository is missing an exact referenced snapshot")
        return references.model_copy(
            update={
                "product_spec": product_spec,
                "qualification": qualification,
                "quote_snapshots": quote_snapshots,
                "normalization_run": normalization,
                "comparison_matrix": comparison,
                "cost_scenarios": cost_scenarios,
            }
        )


class AllowlistedRiskDecisionAuthorization:
    def __init__(self, policy: RiskPolicyVersion) -> None:
        self.policy = policy

    def may_decide(self, actor_id: str, actor_role: str, action: str) -> bool:
        return (
            bool(actor_id.strip())
            and action == "risk.review"
            and actor_role in self.policy.allowed_human_roles
        )


class InMemoryExecutionAudit:
    """Test/local sink with the same append shape as the M10 adapter."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(self, **event: Any) -> dict[str, Any]:
        self.events.append(dict(event))
        return self.events[-1]


class M10ExecutionAuditAdapter:
    """Adapt EP-08 audit calls to M10's hash-chained append-only journal."""

    def __init__(self, journal: AppendOnlyAuditLog) -> None:
        self.journal = journal

    def append(self, **event: Any) -> Any:
        identity = Identity(
            actor_id=str(event["actor_id"]),
            roles=frozenset({Role.REVIEWER}),
            client_identity=str(event["client_identity"]),
        )
        return self.journal.append(
            identity=identity,
            action=str(event["action"]),
            resource=str(event["resource"]),
            before=event["before"],
            after=event["after"],
            occurred_at=event["occurred_at"],
            reason=str(event["reason"]),
            correlation_id=str(event["correlation_id"]),
        )
