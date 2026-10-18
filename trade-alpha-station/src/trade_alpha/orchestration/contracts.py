"""Business-neutral reference contracts for the M06 orchestration layer."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trade_alpha.contracts.common import require_utc


class QualityStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"


class RunStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"


class Severity(StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class AssetSpec:
    key: str
    upstreams: tuple[str, ...] = ()
    partition_scheme: str = "unpartitioned"
    tier: Literal["raw", "silver", "gold", "publication"] = "silver"


@dataclass(frozen=True)
class PartitionRef:
    asset_key: str
    partition_key: str


@dataclass(frozen=True)
class BackfillPlan:
    reason: str
    partitions: tuple[PartitionRef, ...]


@dataclass(frozen=True)
class UpstreamInput:
    asset_key: str
    partition_key: str
    snapshot_id: str
    snapshot_hash: str
    complete: bool
    quality_status: QualityStatus


class RunManifest(BaseModel):
    """Immutable materialization evidence; incomplete runs cannot publish."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    asset_key: str = Field(min_length=1)
    partition_key: str = Field(min_length=1)
    started_at: datetime
    ended_at: datetime
    status: RunStatus
    code_sha: str = Field(min_length=1)
    environment_lock_hash: str = Field(min_length=1)
    config_hash: str = Field(min_length=1)
    input_snapshot_ids: tuple[str, ...] = ()
    input_hashes: tuple[str, ...] = ()
    output_snapshot_id: str | None = None
    output_hash: str | None = None
    row_count: int | None = Field(default=None, ge=0)
    quality_status: QualityStatus
    retry_count: int = Field(ge=0)

    @model_validator(mode="after")
    def publication_is_complete(self) -> RunManifest:
        require_utc(self.started_at)
        require_utc(self.ended_at)
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not be before started_at")
        if self.status is RunStatus.SUCCESS and (
            not self.output_snapshot_id or not self.output_hash or self.row_count is None
        ):
            raise ValueError("successful materialization requires complete output evidence")
        if self.status is RunStatus.SUCCESS and self.quality_status is QualityStatus.FAILED:
            raise ValueError("quality failure cannot be published")
        if (
            self.status is RunStatus.SUCCESS
            and self.asset_key != "source_registry"
            and (not self.input_snapshot_ids or not self.input_hashes)
        ):
            raise ValueError("non-root published asset requires input snapshot evidence")
        if len(self.input_snapshot_ids) != len(self.input_hashes):
            raise ValueError("input_snapshot_ids and input_hashes must have equal lengths")
        return self


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 2
    backoff_seconds: int = 1


@dataclass(frozen=True)
class Issue:
    severity: Severity
    asset_key: str
    partition_key: str
    category: str
    message: str
    run_id: str


@dataclass(frozen=True)
class Alert:
    severity: Severity
    channel: str
    blocks_publication: bool
    notify_human: bool
    message: str


class AlertRouter:
    """Pure routing policy; adapters own notification side effects."""

    _ROUTES = {
        Severity.CRITICAL: ("pager", True, True),
        Severity.ERROR: ("issue-tracker", True, True),
        Severity.WARNING: ("operations-feed", False, True),
        Severity.INFO: ("structured-log", False, False),
    }

    def route(self, severity: Severity, message: str) -> Alert:
        channel, blocks, human = self._ROUTES[severity]
        return Alert(severity, channel, blocks, human, message)


@dataclass(frozen=True)
class FreshnessCheck:
    asset_key: str
    observed_at: datetime
    checked_at: datetime
    sla: timedelta

    @property
    def is_stale(self) -> bool:
        return require_utc(self.checked_at) - require_utc(self.observed_at) > self.sla


@dataclass
class CostLedger:
    budget: float
    usage: dict[str, float] = field(default_factory=dict)

    def record(self, operation: str, units: float, unit_cost: float) -> float:
        if units < 0 or unit_cost < 0:
            raise ValueError("cost units and unit cost must be non-negative")
        cost = units * unit_cost
        self.usage[operation] = self.usage.get(operation, 0.0) + cost
        return cost

    @property
    def total(self) -> float:
        return sum(self.usage.values())

    @property
    def over_budget(self) -> bool:
        return self.total > self.budget


class LineageIndex:
    """Exact entity-to-partition lineage for targeted rematerialization."""

    def __init__(self) -> None:
        self._entity_partitions: dict[str, set[PartitionRef]] = defaultdict(set)

    def record_entity_input(self, entity_id: str, partition: PartitionRef) -> None:
        self._entity_partitions[entity_id].add(partition)

    def entity_revocation_plan(self, entity_id: str) -> BackfillPlan:
        return BackfillPlan(
            reason=f"entity-link-revoked:{entity_id}",
            partitions=tuple(
                sorted(
                    self._entity_partitions[entity_id],
                    key=lambda item: (item.asset_key, item.partition_key),
                )
            ),
        )


class PartitionLineageIndex:
    """Concrete observed partition edges; this is the only exact backfill source."""

    def __init__(self) -> None:
        self._downstream: dict[PartitionRef, set[PartitionRef]] = defaultdict(set)

    def record_edge(self, upstream: PartitionRef, downstream: PartitionRef) -> None:
        self._downstream[upstream].add(downstream)

    def backfill_plan(self, changed: PartitionRef) -> BackfillPlan:
        queue = deque([changed])
        seen = {changed}
        targets: list[PartitionRef] = []
        while queue:
            current = queue.popleft()
            targets.append(current)
            for child in sorted(
                self._downstream[current], key=lambda item: (item.asset_key, item.partition_key)
            ):
                if child not in seen:
                    seen.add(child)
                    queue.append(child)
        return BackfillPlan("partition-revision", tuple(targets))


def build_asset_graph() -> dict[str, AssetSpec]:
    """The complete main chain, named for business objects rather than scripts."""
    specs = (
        AssetSpec("source_registry", tier="raw"),
        AssetSpec("raw_trade_snapshots", ("source_registry",), "source×retrieval-date", "raw"),
        AssetSpec("raw_web_snapshots", ("source_registry",), "source×retrieval-date", "raw"),
        AssetSpec(
            "trade_observations_pit", ("raw_trade_snapshots",), "classification×reporter×period"
        ),
        AssetSpec("document_blocks", ("raw_web_snapshots",), "artifact×extraction-profile"),
        AssetSpec("candidate_claims", ("document_blocks",), "observed-date"),
        AssetSpec("resolved_entities", ("candidate_claims",), "resolution-version"),
        AssetSpec(
            "feature_inputs",
            ("trade_observations_pit", "resolved_entities"),
            "as-of×feature-version",
            "gold",
        ),
        AssetSpec("signals", ("feature_inputs",), "as-of×signal-version", "gold"),
        AssetSpec("opportunity_snapshot", ("signals",), "as-of-date", "gold"),
        AssetSpec("research_publication", ("opportunity_snapshot",), "as-of-date", "publication"),
        AssetSpec("experiment_outcomes", ("research_publication",), "experiment-id×fold"),
        AssetSpec("evaluation", ("experiment_outcomes",), "evaluation-fold"),
    )
    return {spec.key: spec for spec in specs}


def validate_asset_graph(graph: dict[str, AssetSpec]) -> tuple[str, ...]:
    """Return validation errors; an empty result proves the P0 main-chain shape."""
    errors: list[str] = []
    expected = build_asset_graph()
    for key, spec in graph.items():
        for upstream in spec.upstreams:
            if upstream not in graph:
                errors.append(f"{key} depends on missing {upstream}")
    for key in ("feature_inputs", "signals", "opportunity_snapshot", "research_publication"):
        if key not in graph:
            errors.append(f"missing main-chain asset {key}")
        elif not graph[key].upstreams:
            errors.append(f"orphan gold/publication asset {key}")
    for key in expected:
        if key not in graph:
            errors.append(f"missing required asset {key}")
    return tuple(errors)


def downstream_partitions(graph: dict[str, AssetSpec], changed: PartitionRef) -> BackfillPlan:
    """Return graph-level impact candidates, not an exact execution backfill plan.

    Partition schemes differ across assets, so callers must use
    :class:`PartitionLineageIndex` to obtain concrete partitions.
    """
    children: dict[str, list[str]] = defaultdict(list)
    for spec in graph.values():
        for upstream in spec.upstreams:
            children[upstream].append(spec.key)
    queue = deque([changed.asset_key])
    seen = {changed.asset_key}
    targets = [changed]
    while queue:
        current = queue.popleft()
        for child in sorted(children[current]):
            if child not in seen:
                seen.add(child)
                queue.append(child)
                targets.append(PartitionRef(child, "<mapped-partition-required>"))
    return BackfillPlan("partition-impact-candidates", tuple(targets))
