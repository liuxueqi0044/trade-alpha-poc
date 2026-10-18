from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

pytest.importorskip("pyarrow")
pytest.importorskip("duckdb")
pytest.importorskip("polars")

from trade_alpha.storage import (  # noqa: E402
    BitemporalObservation,
    PublicationFault,
    SnapshotIntegrityError,
    StorageLake,
    observations_to_rows,
)


def observations() -> list[BitemporalObservation]:
    return [
        BitemporalObservation(
            natural_key="trade:2026-05",
            value=100,
            valid_from=datetime(2026, 5, 1, tzinfo=UTC),
            observed_at=datetime(2026, 8, 1, tzinfo=UTC),
            system_from=datetime(2026, 8, 1, tzinfo=UTC),
            system_to=datetime(2026, 10, 5, tzinfo=UTC),
            source_local_time="2026-08-01T08:00:00+08:00",
            source_timezone="Asia/Shanghai",
        ),
        BitemporalObservation(
            natural_key="trade:2026-05",
            value=108,
            valid_from=datetime(2026, 5, 1, tzinfo=UTC),
            observed_at=datetime(2026, 10, 5, tzinfo=UTC),
            system_from=datetime(2026, 10, 5, tzinfo=UTC),
            source_local_time="2026-10-05T08:00:00+08:00",
            source_timezone="Asia/Shanghai",
        ),
    ]


def build(lake: StorageLake, snapshot: str, rows: list[dict] | None = None):
    return lake.build_snapshot(
        region="gold",
        snapshot_id=snapshot,
        tables={"observations": rows or observations_to_rows(observations())},
        code_version="git-fixture",
        config_version="config-fixture",
    )


def test_m04_ac_001_dual_vintage_as_of_has_no_future_leakage(tmp_path) -> None:
    lake = StorageLake(tmp_path)
    build(lake, "pit")
    august = lake.as_of_observations("pit", datetime(2026, 8, 24, tzinfo=UTC))
    october = lake.as_of_observations("pit", datetime(2026, 10, 10, tzinfo=UTC))
    assert [row["value"] for row in august] == [100]
    assert [row["value"] for row in october] == [108]


def test_m04_ac_002_atomic_publication_keeps_old_alias_during_fault(tmp_path) -> None:
    lake = StorageLake(tmp_path)
    build(lake, "old")
    with pytest.raises(PublicationFault):
        lake.build_snapshot(
            region="gold",
            snapshot_id="interrupted",
            tables={"observations": observations_to_rows(observations())},
            code_version="git-fixture",
            config_version="config-fixture",
            fault_at="before_alias",
        )
    assert lake.published_snapshot() == "old"
    build(lake, "recovered")
    assert lake.published_snapshot() == "recovered"


def test_m04_ac_003_replay_has_equal_file_hashes_schema_and_rows(tmp_path) -> None:
    first = build(StorageLake(tmp_path / "one"), "one")
    second = build(StorageLake(tmp_path / "two"), "two")
    assert [(item.rows, item.schema_hash, item.content_hash) for item in first.files] == [
        (item.rows, item.schema_hash, item.content_hash) for item in second.files
    ]


def test_m04_ac_006_integrity_scanner_locates_corrupt_parquet(tmp_path) -> None:
    lake = StorageLake(tmp_path)
    manifest = build(lake, "good")
    target = tmp_path / "gold" / "snapshots" / "good" / manifest.files[0].path
    target.write_bytes(target.read_bytes() + b"corrupt")
    with pytest.raises(SnapshotIntegrityError, match="hash_mismatch"):
        lake.assert_snapshot_integrity(manifest)
    with pytest.raises(SnapshotIntegrityError, match="hash_mismatch"):
        lake.as_of_observations("good", datetime(2026, 8, 24, tzinfo=UTC))


def test_m04_ac_006_integrity_scanner_also_detects_corrupt_raw(tmp_path) -> None:
    lake = StorageLake(tmp_path)
    raw = lake.put_raw("fixture", datetime(2026, 8, 1, tzinfo=UTC), b"immutable")
    raw.write_bytes(b"modified")
    with pytest.raises(SnapshotIntegrityError, match="raw_hash_mismatch"):
        lake.assert_raw_integrity(
            raw,
            "sha256:3e58bada6a180c0d7f817bdae51fba96a461575b309bfbc17a6918d20c6617c7",
        )


def test_m04_ac_007_snapshot_backup_restore_rehearsal_has_hashes(tmp_path) -> None:
    class FakePostgres:
        def __init__(self) -> None:
            self.backups: list[object] = []
            self.restores: list[object] = []

        def backup(self, destination: object) -> None:
            self.backups.append(destination)

        def restore(self, source: object) -> None:
            self.restores.append(source)

    lake = StorageLake(tmp_path / "source")
    build(lake, "recoverable")
    postgres = FakePostgres()
    backup = lake.backup_recovery_unit("recoverable", tmp_path / "backup", postgres)
    restored = StorageLake(tmp_path / "restored").restore_snapshot(
        backup,
        postgres=postgres,
        postgres_backup=backup / "postgres-backup",
    )
    assert restored.hashes_match
    assert restored.postgres_rehearsed
    assert len(postgres.backups) == len(postgres.restores) == 1
    assert restored.elapsed_seconds < restored.rto_seconds


def test_m04_ac_012_concurrent_alias_reads_are_always_complete(tmp_path) -> None:
    lake = StorageLake(tmp_path)
    build(lake, "old")
    barrier = threading.Barrier(2)
    seen: list[str] = []
    failures: list[BaseException] = []

    def reader() -> None:
        try:
            barrier.wait()
            for _ in range(100):
                snapshot = lake.published_snapshot()
                if snapshot is not None:
                    seen.append(snapshot)
        except BaseException as exc:  # pragma: no cover
            failures.append(exc)

    def publisher() -> None:
        try:
            barrier.wait()
            build(lake, "new")
        except BaseException as exc:  # pragma: no cover
            failures.append(exc)

    threads = [threading.Thread(target=reader), threading.Thread(target=publisher)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not failures
    assert set(seen) <= {"old", "new"}
    assert lake.published_snapshot() == "new"
