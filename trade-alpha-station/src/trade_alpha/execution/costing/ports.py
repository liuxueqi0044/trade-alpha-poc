"""Read-only sources and persistence boundaries for EP-07."""

from __future__ import annotations

from typing import Protocol

from trade_alpha.execution.contracts.models import FxSnapshot
from trade_alpha.execution.supplier_quotes.models import (
    QuoteNormalizationRun,
    SupplierQuoteSnapshot,
)

from .models import CostAssumptionDecision, CostScenarioSnapshot


class CostingSourceReader(Protocol):
    """Trusted reader for immutable EP-06 source records and FX snapshots."""

    def quote_snapshot(self, snapshot_id: str) -> SupplierQuoteSnapshot | None: ...

    def normalization_run(self, run_id: str) -> QuoteNormalizationRun | None: ...

    def fx_snapshot(self, snapshot_id: str) -> FxSnapshot | None: ...

    def product_family_id(self, product_spec_version_id: str) -> str | None: ...


class CostScenarioRepositoryPort(Protocol):
    def save_decision(self, decision: CostAssumptionDecision) -> CostAssumptionDecision: ...

    def decisions_for(self, cost_input_id: str): ...  # type: ignore[no-untyped-def]

    def save_snapshot(self, snapshot: CostScenarioSnapshot) -> CostScenarioSnapshot: ...

    def snapshots_for_key(self, key: str): ...  # type: ignore[no-untyped-def]
