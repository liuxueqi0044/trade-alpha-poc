"""Replaceable adapters for execution-plane domain ports.

Dependency readers are loaded lazily because their domain models also use the
adapter package for lower-level normalization ports.  Keeping that edge lazy
preserves the public imports without making package import order significant.
"""

from typing import TYPE_CHECKING

from .fixture_screening import FixtureScreeningAdapter
from .format_identifiers import FormatOnlyIdentifierAdapter
from .governance_execution import (
    GovernanceApprovalAuditAdapter,
    GovernanceAuditChainVerifierAdapter,
    GovernanceExecutionAuthorizationAdapter,
    GovernanceRiskAuditAdapter,
)

if TYPE_CHECKING:
    from .execution_dependencies import CostScenarioDependencyReader, RiskAssessmentDependencyReader

__all__ = [
    "CostScenarioDependencyReader",
    "FixtureScreeningAdapter",
    "FormatOnlyIdentifierAdapter",
    "GovernanceAuditChainVerifierAdapter",
    "GovernanceApprovalAuditAdapter",
    "GovernanceExecutionAuthorizationAdapter",
    "GovernanceRiskAuditAdapter",
    "RiskAssessmentDependencyReader",
]


def __getattr__(name: str) -> object:
    if name in {"CostScenarioDependencyReader", "RiskAssessmentDependencyReader"}:
        from .execution_dependencies import (
            CostScenarioDependencyReader,
            RiskAssessmentDependencyReader,
        )

        return {
            "CostScenarioDependencyReader": CostScenarioDependencyReader,
            "RiskAssessmentDependencyReader": RiskAssessmentDependencyReader,
        }[name]
    raise AttributeError(name)
