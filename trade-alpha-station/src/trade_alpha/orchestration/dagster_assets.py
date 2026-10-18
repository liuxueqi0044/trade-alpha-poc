"""Real Dagster asset declarations, kept intentionally free of business logic.

The reference executor supplies runtime semantics. ``definitions`` is a real
Dagster ``Definitions`` object whenever Dagster is installed; the import
fallback only keeps non-orchestrator consumers importable in minimal environments.
"""

from __future__ import annotations

definitions: object | None

try:
    from dagster import Definitions, asset
except ImportError:  # pragma: no cover - exercised in an environment with Dagster installed.
    definitions = None
    dagster_available = False
else:
    dagster_available = True

    @asset
    def source_registry() -> dict[str, str]:
        return {"asset": "source_registry"}

    @asset(deps=[source_registry])
    def raw_trade_snapshots() -> dict[str, str]:
        return {"asset": "raw_trade_snapshots"}

    @asset(deps=[source_registry])
    def raw_web_snapshots() -> dict[str, str]:
        return {"asset": "raw_web_snapshots"}

    @asset(deps=[raw_trade_snapshots])
    def trade_observations_pit() -> dict[str, str]:
        return {"asset": "trade_observations_pit"}

    @asset(deps=[raw_web_snapshots])
    def document_blocks() -> dict[str, str]:
        return {"asset": "document_blocks"}

    @asset(deps=[document_blocks])
    def candidate_claims() -> dict[str, str]:
        return {"asset": "candidate_claims"}

    @asset(deps=[candidate_claims])
    def resolved_entities() -> dict[str, str]:
        return {"asset": "resolved_entities"}

    @asset(deps=[trade_observations_pit, resolved_entities])
    def feature_inputs() -> dict[str, str]:
        return {"asset": "feature_inputs"}

    @asset(deps=[feature_inputs])
    def signals() -> dict[str, str]:
        return {"asset": "signals"}

    @asset(deps=[signals])
    def opportunity_snapshot() -> dict[str, str]:
        return {"asset": "opportunity_snapshot"}

    @asset(deps=[opportunity_snapshot])
    def research_publication() -> dict[str, str]:
        return {"asset": "research_publication"}

    @asset(deps=[research_publication])
    def experiment_outcomes() -> dict[str, str]:
        return {"asset": "experiment_outcomes"}

    @asset(deps=[experiment_outcomes])
    def evaluation() -> dict[str, str]:
        return {"asset": "evaluation"}

    definitions = Definitions(
        assets=[
            source_registry,
            raw_trade_snapshots,
            raw_web_snapshots,
            trade_observations_pit,
            document_blocks,
            candidate_claims,
            resolved_entities,
            feature_inputs,
            signals,
            opportunity_snapshot,
            research_publication,
            experiment_outcomes,
            evaluation,
        ]
    )
