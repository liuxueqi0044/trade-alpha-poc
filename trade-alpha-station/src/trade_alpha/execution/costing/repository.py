"""Append-only in-memory storage and fixtures for EP-07."""

from __future__ import annotations

from datetime import datetime
from typing import TypeVar

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.contracts.models import FxSnapshot
from trade_alpha.execution.supplier_quotes.models import (
    QuoteNormalizationRun,
    SupplierQuoteSnapshot,
)

from .models import CostAssumptionDecision, CostScenarioSnapshot

_Stored = TypeVar("_Stored")


class InMemoryCostScenarioRepository:
    def __init__(self) -> None:
        self._decisions: list[CostAssumptionDecision] = []
        self._snapshots: dict[str, CostScenarioSnapshot] = {}
        self._snapshot_keys: dict[str, list[str]] = {}

    def save_decision(self, decision: CostAssumptionDecision) -> CostAssumptionDecision:
        prior = next((x for x in self._decisions if x.decision_id == decision.decision_id), None)
        if prior is not None:
            if prior != decision:
                raise ValueError("CostAssumptionDecision is immutable")
            return prior
        self._decisions.append(decision)
        return decision

    def decisions_for(
        self, cost_input_id: str, as_of: datetime | None = None
    ) -> tuple[CostAssumptionDecision, ...]:
        moment = require_utc(as_of) if as_of else None
        return tuple(
            sorted(
                (
                    x
                    for x in self._decisions
                    if x.cost_input_id == cost_input_id
                    and (moment is None or x.decided_at <= moment)
                ),
                key=lambda x: (x.decided_at, x.decision_id),
            )
        )

    def save_snapshot(
        self, snapshot: CostScenarioSnapshot, *, scenario_key: str
    ) -> CostScenarioSnapshot:
        prior = self._snapshots.get(snapshot.cost_scenario_snapshot_id)
        if prior is not None:
            if prior != snapshot:
                raise ValueError("CostScenarioSnapshot is immutable")
            return prior
        duplicate = next(
            (
                x
                for x in self._snapshots.values()
                if x.content_fingerprint == snapshot.content_fingerprint
            ),
            None,
        )
        if duplicate is not None:
            return duplicate
        self._snapshots[snapshot.cost_scenario_snapshot_id] = snapshot
        self._snapshot_keys.setdefault(scenario_key, []).append(snapshot.cost_scenario_snapshot_id)
        return snapshot

    def snapshot(self, snapshot_id: str) -> CostScenarioSnapshot | None:
        return self._snapshots.get(snapshot_id)

    def snapshots_for_key(self, key: str) -> tuple[CostScenarioSnapshot, ...]:
        return tuple(
            sorted(
                (self._snapshots[item] for item in self._snapshot_keys.get(key, [])),
                key=lambda x: (x.version, x.as_of, x.cost_scenario_snapshot_id),
            )
        )

    def is_lineage_tip_as_of(self, snapshot_id: str, as_of: datetime) -> bool:
        moment = require_utc(as_of)
        key = next(
            (key for key, values in self._snapshot_keys.items() if snapshot_id in values),
            None,
        )
        if key is None:
            return False
        candidates = tuple(item for item in self.snapshots_for_key(key) if item.as_of <= moment)
        return bool(candidates) and candidates[-1].cost_scenario_snapshot_id == snapshot_id


class InMemoryCostingSourceReader:
    """Test adapter modelling repository-saved EP-06 upstream records."""

    def __init__(self) -> None:
        self._quotes: dict[str, SupplierQuoteSnapshot] = {}
        self._runs: dict[str, QuoteNormalizationRun] = {}
        self._fx: dict[str, FxSnapshot] = {}
        self._product_families: dict[str, str] = {}

    def save_quote_snapshot(self, snapshot: SupplierQuoteSnapshot) -> SupplierQuoteSnapshot:
        return self._save(
            self._quotes, snapshot.supplier_quote_snapshot_id, snapshot, "quote snapshot"
        )

    def save_normalization_run(self, run: QuoteNormalizationRun) -> QuoteNormalizationRun:
        return self._save(self._runs, run.normalization_run_id, run, "normalization run")

    def save_fx_snapshot(self, snapshot_id: str, snapshot: FxSnapshot) -> FxSnapshot:
        return self._save(self._fx, snapshot_id, snapshot, "FX snapshot")

    def quote_snapshot(self, snapshot_id: str) -> SupplierQuoteSnapshot | None:
        return self._quotes.get(snapshot_id)

    def normalization_run(self, run_id: str) -> QuoteNormalizationRun | None:
        return self._runs.get(run_id)

    def fx_snapshot(self, snapshot_id: str) -> FxSnapshot | None:
        return self._fx.get(snapshot_id)

    def save_product_family(self, product_spec_version_id: str, product_family_id: str) -> None:
        prior = self._product_families.get(product_spec_version_id)
        if prior is not None and prior != product_family_id:
            raise ValueError("product specification family scope is immutable")
        self._product_families[product_spec_version_id] = product_family_id

    def product_family_id(self, product_spec_version_id: str) -> str | None:
        return self._product_families.get(product_spec_version_id)

    @staticmethod
    def _save(
        store: dict[str, _Stored], key: str, value: _Stored, label: str
    ) -> _Stored:
        prior = store.get(key)
        if prior is not None and prior != value:
            raise ValueError(f"{label} ID conflicts with immutable content")
        store.setdefault(key, value)
        return store[key]
