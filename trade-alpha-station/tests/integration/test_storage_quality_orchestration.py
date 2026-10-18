from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trade_alpha.orchestration import (
    OperationResult,
    ReferenceExecutor,
    RunStatus,
    UpstreamInput,
)
from trade_alpha.orchestration import (
    QualityStatus as OrchestrationQuality,
)
from trade_alpha.quality import build_lineage, publish_gold, validate_contract
from trade_alpha.storage import BitemporalObservation, StorageLake, observations_to_rows


def test_m04_m05_m06_snapshot_quality_and_materialization_chain(tmp_path: Path) -> None:
    august = datetime(2026, 8, 24, tzinfo=UTC)
    observations = (
        BitemporalObservation(
            natural_key="trade:US:847130:2026-05",
            value=100,
            valid_from=datetime(2026, 5, 1, tzinfo=UTC),
            observed_at=datetime(2026, 8, 1, tzinfo=UTC),
            system_from=datetime(2026, 8, 1, tzinfo=UTC),
            system_to=datetime(2026, 10, 5, tzinfo=UTC),
        ),
        BitemporalObservation(
            natural_key="trade:US:847130:2026-05",
            value=108,
            valid_from=datetime(2026, 5, 1, tzinfo=UTC),
            observed_at=datetime(2026, 10, 5, tzinfo=UTC),
            system_from=datetime(2026, 10, 5, tzinfo=UTC),
        ),
    )
    lake = StorageLake(tmp_path / "lake")
    storage_manifest = lake.build_snapshot(
        region="gold",
        snapshot_id="trade-pit-v1",
        tables={"observations": observations_to_rows(observations)},
        code_version="integration-code",
        config_version="integration-config",
    )
    assert lake.published_snapshot() == storage_manifest.snapshot_id
    visible = lake.as_of_observations(storage_manifest.snapshot_id, august)
    assert [row["value"] for row in visible] == [100]

    quality_rows = [
        {
            "observation_id": row["natural_key"],
            "source_snapshot_id": storage_manifest.snapshot_id,
            "country_code": "US",
            "hs": {
                "classification_system": "HS",
                "version": "2022",
                "level": 6,
                "code": "847130",
            },
            "observed_at": row["observed_at"],
            "value": row["value"],
            "currency": "USD",
            "quantity": 10.0,
            "quantity_unit": "kg",
        }
        for row in visible
    ]
    report = validate_contract(quality_rows, snapshot_id="quality-gold-v1")
    lineage = build_lineage(
        snapshot_id=report.snapshot_id,
        input_snapshot_ids=(storage_manifest.snapshot_id,),
        model_name="fct_trade_observation_pit",
        model_version="1.0.0",
        code_sha="integration-code",
        rows=quality_rows,
        created_at=august,
    )
    published = publish_gold(
        report,
        issues=(),
        completeness_status="complete",
        lineage=lineage,
        known_countries={"US"},
        evaluated_at=august,
    )
    assert published.status.value == "published"

    upstreams = (
        UpstreamInput(
            asset_key="trade_observations_pit",
            partition_key="HS2022×US×2026-05",
            snapshot_id=storage_manifest.snapshot_id,
            snapshot_hash=storage_manifest.manifest_hash,
            complete=True,
            quality_status=OrchestrationQuality.PASSED,
        ),
        UpstreamInput(
            asset_key="resolved_entities",
            partition_key="entity-resolution-v1",
            snapshot_id="entities-v1",
            snapshot_hash="sha256:entities",
            complete=True,
            quality_status=OrchestrationQuality.PASSED,
        ),
    )
    executor = ReferenceExecutor(sleeper=lambda _seconds: None)
    run = executor.execute(
        "feature_inputs",
        "2026-08-24×feature-v1",
        upstreams,
        lambda _checkpoint: OperationResult(
            output_snapshot_id="feature-inputs-v1",
            output_hash=lineage.output_hash,
            row_count=len(published.accepted),
        ),
        code_sha="integration-code",
        environment_lock_hash="requirements-dev-lock",
        config_hash="feature-config-v1",
    )
    assert run.status is RunStatus.SUCCESS
    assert run.input_snapshot_ids == (storage_manifest.snapshot_id, "entities-v1")
    assert run.output_hash == lineage.output_hash

