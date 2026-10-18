from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_alpha.orchestration import (
    AlertRouter,
    CostLedger,
    FreshnessCheck,
    LineageIndex,
    PartitionLineageIndex,
    PartitionRef,
    QualityStatus,
    ReferenceExecutor,
    RetryPolicy,
    RunManifest,
    RunStatus,
    SchemaFailure,
    Severity,
    TransientFailure,
    UpstreamBlocked,
    UpstreamInput,
    build_asset_graph,
    dagster_available,
    definitions,
    rehearse_runbook,
    validate_asset_graph,
    validate_runbooks,
)
from trade_alpha.orchestration.contracts import downstream_partitions
from trade_alpha.orchestration.executor import OperationResult

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def upstream(
    asset_key: str = "raw_trade_snapshots",
    *,
    complete: bool = True,
    quality: QualityStatus = QualityStatus.PASSED,
    snapshot_id: str = "snapshot-1",
    snapshot_hash: str = "sha256:input",
) -> UpstreamInput:
    return UpstreamInput(
        asset_key=asset_key,
        partition_key="CN×2026-08-01",
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_hash,
        complete=complete,
        quality_status=quality,
    )


def result() -> OperationResult:
    return OperationResult("snapshot-out", "sha256:output", 3)


def inputs_for(asset_key: str) -> list[UpstreamInput]:
    return [upstream(key) for key in build_asset_graph()[asset_key].upstreams]


def test_complete_main_chain_has_no_orphan_gold_assets() -> None:
    graph = build_asset_graph()
    assert not validate_asset_graph(graph)  # M06-AC-001
    assert graph["research_publication"].upstreams == ("opportunity_snapshot",)
    assert dagster_available and definitions is not None
    dagster_graph = definitions.resolve_asset_graph()
    actual = {
        key.path[-1]: tuple(
            sorted(parent.path[-1] for parent in dagster_graph.get(key).parent_keys)
        )
        for key in dagster_graph.get_all_asset_keys()
    }
    expected = {key: tuple(sorted(spec.upstreams)) for key, spec in graph.items()}
    assert actual == expected


@pytest.mark.parametrize(
    "blocked", [upstream(complete=False), upstream(quality=QualityStatus.FAILED)]
)
def test_incomplete_or_failed_upstream_blocks_gold_before_operation(blocked: UpstreamInput) -> None:
    executor = ReferenceExecutor()
    called = False

    def operation(_: str | None) -> OperationResult:
        nonlocal called
        called = True
        return result()

    with pytest.raises(UpstreamBlocked):
        executor.execute("trade_observations_pit", "2026-08-24×v1", [blocked], operation)
    assert not called and not executor.manifests  # M06-AC-002


def test_transient_failure_recovers_without_duplicate_publication() -> None:
    sleeps: list[float] = []
    executor = ReferenceExecutor(RetryPolicy(max_retries=2, backoff_seconds=3), sleeps.append)
    calls = 0

    def flaky(_: str | None) -> OperationResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientFailure("HTTP 503")
        return result()

    inputs = inputs_for("trade_observations_pit")
    first = executor.execute("trade_observations_pit", "CN×2026-08", inputs, flaky)
    second = executor.execute("trade_observations_pit", "CN×2026-08", inputs, flaky)
    changed = executor.execute(
        "trade_observations_pit",
        "CN×2026-08",
        [upstream(snapshot_hash="sha256:changed")],
        flaky,
    )
    assert first.status is RunStatus.SUCCESS and first.retry_count == 1
    assert second.output_snapshot_id == first.output_snapshot_id
    assert changed.output_snapshot_id == first.output_snapshot_id
    assert sleeps == [3.0]
    assert (
        calls == 3
    )  # unchanged input reused; changed input forced a materialization  # M06-AC-003


def test_terminal_schema_failure_creates_contextual_error_issue_without_retry() -> None:
    executor = ReferenceExecutor()
    calls = 0

    def invalid(_: str | None) -> OperationResult:
        nonlocal calls
        calls += 1
        raise SchemaFailure("required column missing")

    manifest = executor.execute(
        "document_blocks", "artifact-a×ocr-v1", inputs_for("document_blocks"), invalid
    )
    assert manifest.status is RunStatus.FAILED and manifest.retry_count == 0
    assert calls == 1
    assert executor.issues[0].severity is Severity.ERROR
    assert executor.issues[0].category == "schema"  # M06-AC-004

    unexpected = executor.execute(
        "document_blocks",
        "artifact-b×ocr-v1",
        inputs_for("document_blocks"),
        lambda _: (_ for _ in ()).throw(RuntimeError("bad decoder")),
    )
    assert unexpected.status is RunStatus.FAILED
    assert executor.issues[-1].category == "unexpected"


def test_partition_backfill_and_entity_revocation_are_exact() -> None:
    graph = build_asset_graph()
    candidates = downstream_partitions(graph, PartitionRef("trade_observations_pit", "CN×2026-08"))
    assert candidates.reason == "partition-impact-candidates"
    assert {item.asset_key for item in candidates.partitions} >= {
        "trade_observations_pit",
        "feature_inputs",
        "signals",
        "research_publication",
    }
    lineage_edges = PartitionLineageIndex()
    source = PartitionRef("trade_observations_pit", "HS2022×CN×2026-08")
    feature = PartitionRef("feature_inputs", "2026-08-24×feature-v1")
    signal = PartitionRef("signals", "2026-08-24×signal-v1")
    publication = PartitionRef("research_publication", "2026-08-24")
    unrelated = PartitionRef("signals", "2026-09-24×signal-v1")
    lineage_edges.record_edge(source, feature)
    lineage_edges.record_edge(feature, signal)
    lineage_edges.record_edge(signal, publication)
    exact = lineage_edges.backfill_plan(source)
    assert set(exact.partitions) == {source, feature, signal, publication}
    assert unrelated not in exact.partitions  # M06-AC-005

    lineage = LineageIndex()
    expected = PartitionRef("feature_inputs", "2026-08-24×feature-v1")
    signal = PartitionRef("signals", "2026-08-24×signal-v1")
    lineage.record_entity_input("entity-a", expected)
    lineage.record_entity_input("entity-a", signal)
    lineage.record_entity_input("entity-b", PartitionRef("signals", "other"))
    impact = lineage.entity_revocation_plan("entity-a")
    assert set(impact.partitions) == {expected, signal}  # M06-AC-010


def test_manifest_checkpoint_recovery_and_no_half_publication() -> None:
    executor = ReferenceExecutor(RetryPolicy(max_retries=1))
    attempts: list[str | None] = []

    def interrupted(checkpoint: str | None) -> OperationResult:
        attempts.append(checkpoint)
        if checkpoint is None:
            executor.checkpoints.save("feature_inputs", "2026-08-24×v1", "rows:100")
            raise TransientFailure("worker lost")
        assert checkpoint == "rows:100"
        return result()

    manifest = executor.execute(
        "feature_inputs", "2026-08-24×v1", inputs_for("feature_inputs"), interrupted
    )
    assert attempts == [None, "rows:100"]
    assert manifest.output_hash == "sha256:output"
    assert executor.checkpoints.get("feature_inputs", "2026-08-24×v1") is None
    assert len(executor.manifests) == 1  # M06-AC-006, M06-AC-007
    with pytest.raises(ValidationError, match="complete output evidence"):
        RunManifest(
            run_id="bad",
            asset_key="signals",
            partition_key="p",
            started_at=NOW,
            ended_at=NOW,
            status=RunStatus.SUCCESS,
            code_sha="c",
            environment_lock_hash="e",
            config_hash="f",
            quality_status=QualityStatus.PASSED,
            retry_count=0,
        )
    with pytest.raises(ValidationError, match="input snapshot evidence"):
        RunManifest(
            run_id="missing-inputs",
            asset_key="signals",
            partition_key="p",
            started_at=NOW,
            ended_at=NOW,
            status=RunStatus.SUCCESS,
            code_sha="c",
            environment_lock_hash="e",
            config_hash="f",
            output_snapshot_id="s",
            output_hash="h",
            row_count=1,
            quality_status=QualityStatus.PASSED,
            retry_count=0,
        )


def test_alert_freshness_cost_and_executable_runbook() -> None:
    router = AlertRouter()
    assert router.route(Severity.CRITICAL, "hash mismatch").blocks_publication
    assert router.route(Severity.ERROR, "contract").channel == "issue-tracker"
    assert router.route(Severity.WARNING, "stale").notify_human
    assert not router.route(Severity.INFO, "normal").notify_human  # M06-AC-008

    freshness = FreshnessCheck(
        "raw_trade_snapshots", NOW, NOW + timedelta(days=2), timedelta(days=1)
    )
    assert freshness.is_stale  # M06-AC-009
    costs = CostLedger(budget=2.0)
    costs.record("ocr", 3, 1.0)
    assert costs.over_budget  # M06-AC-011
    runbook = Path("docs/runbooks/m06-critical-assets.md")
    assert not validate_runbooks(runbook)  # M06-AC-012
    assert "-m pytest tests/m06" in rehearse_runbook(runbook, "feature_inputs", "worker-lost")
