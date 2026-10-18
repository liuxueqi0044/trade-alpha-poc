"""Real Parquet snapshots, atomic publication, and DuckDB point-in-time reads."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Protocol

from .engines import require_analytical_engines
from .models import (
    REGION_CONTRACTS,
    BitemporalObservation,
    RecoveryReport,
    RegionName,
    SnapshotFile,
    SnapshotManifest,
)


class SnapshotIntegrityError(RuntimeError):
    pass


class PublicationFault(RuntimeError):
    """Deterministic fault used for publication/recovery drills."""


class PostgresBackupAdapter(Protocol):
    """Boundary only: production supplies a transactional PostgreSQL adapter."""

    def backup(self, destination: Path) -> None: ...

    def restore(self, source: Path) -> None: ...


class StorageLake:
    """Filesystem lake with a published alias that is the only consumer entrypoint."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        for region in REGION_CONTRACTS:
            (self.root / region / "snapshots").mkdir(parents=True, exist_ok=True)
        (self.root / "metadata" / "snapshots").mkdir(parents=True, exist_ok=True)
        (self.root / "staging").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _digest(path: Path) -> str:
        with path.open("rb") as handle:
            return f"sha256:{hashlib.file_digest(handle, 'sha256').hexdigest()}"

    @staticmethod
    def _safe_component(value: str) -> str:
        if not value or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for char in value
        ):
            raise ValueError(
                "snapshot/source IDs may contain only alphanumeric characters, _ and -"
            )
        return value

    def raw_path(
        self, source_id: str, retrieved_at: datetime, content_hash: str, extension: str
    ) -> Path:
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        source = self._safe_component(source_id)
        digest = content_hash.removeprefix("sha256:")
        return (
            self.root
            / "raw"
            / source
            / f"retrieved_date={retrieved_at.astimezone(UTC).date().isoformat()}"
            / f"{digest}.{extension}"
        )

    def put_raw(
        self, source_id: str, retrieved_at: datetime, content: bytes, extension: str = "bin"
    ) -> Path:
        """Create raw evidence once; an existing path is verified rather than replaced."""

        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        path = self.raw_path(source_id, retrieved_at, digest, extension)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if self._digest(path) != digest:
                raise SnapshotIntegrityError(f"raw hash path is corrupt: {path}")
            return path
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if self._digest(path) != digest:
                    raise SnapshotIntegrityError(
                        f"raw concurrent write is corrupt: {path}"
                    ) from None
            finally:
                Path(temporary).unlink(missing_ok=True)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return path

    def assert_raw_integrity(self, path: str | Path, content_hash: str) -> None:
        target = Path(path)
        if self._digest(target) != content_hash:
            raise SnapshotIntegrityError(f"raw_hash_mismatch:{target}")

    def build_snapshot(
        self,
        *,
        region: RegionName,
        snapshot_id: str,
        tables: Mapping[str, Sequence[Mapping[str, Any]]],
        code_version: str,
        config_version: str,
        fault_at: str | None = None,
    ) -> SnapshotManifest:
        """Write real Parquet files into staging and atomically advance gold's alias.

        ``fault_at='before_alias'`` simulates process death after durable snapshot
        creation.  Consumers continue to resolve the old alias in that case.
        """

        if region != "gold":
            raise ValueError("only gold snapshots have a published consumer alias")
        snapshot = self._safe_component(snapshot_id)
        pa, parquet, _, pl = require_analytical_engines()
        stage = self.root / "staging" / f"{region}-{snapshot}"
        if stage.exists():
            raise FileExistsError(f"staging snapshot already exists: {stage}")
        stage.mkdir(parents=True)
        files: list[SnapshotFile] = []
        for name in sorted(tables):
            table_name = self._safe_component(name)
            # Polars builds the deterministic columnar frame; Arrow writes the
            # physical Parquet artifact with explicit compression.
            frame = pl.DataFrame(list(tables[name]))
            if frame.width:
                frame = frame.sort(frame.columns)
            arrow_table = frame.to_arrow()
            target = stage / f"{table_name}.parquet"
            parquet.write_table(arrow_table, target, compression="zstd")
            files.append(self._describe_parquet(target, stage, parquet))
        manifest = self._manifest(region, snapshot, files, code_version, config_version)
        manifest_path = stage / "manifest.json"
        self._write_json_atomic(manifest_path, manifest.model_dump(mode="json"))
        self.assert_snapshot_integrity(manifest, base=stage)
        final = self.root / region / "snapshots" / snapshot
        if final.exists():
            raise FileExistsError(f"snapshot already exists: {final}")
        os.replace(stage, final)
        self._write_json_atomic(
            self.root / "metadata" / "snapshots" / f"{snapshot}.json",
            manifest.model_dump(mode="json"),
        )
        if fault_at == "before_alias":
            raise PublicationFault("fault injected before published alias update")
        self._write_json_atomic(self.root / region / "published.json", {"snapshot_id": snapshot})
        return manifest

    def _describe_parquet(self, target: Path, base: Path, parquet: Any) -> SnapshotFile:
        metadata = parquet.ParquetFile(target).metadata
        table = parquet.read_table(target)
        min_values: dict[str, str | int | float | None] = {}
        max_values: dict[str, str | int | float | None] = {}
        for field in table.schema:
            values = table.column(field.name).to_pylist()
            non_null = [value for value in values if value is not None]
            if non_null:
                min_values[field.name] = self._json_scalar(min(non_null))
                max_values[field.name] = self._json_scalar(max(non_null))
        schema_hash = f"sha256:{hashlib.sha256(str(table.schema).encode()).hexdigest()}"
        return SnapshotFile(
            path=str(target.relative_to(base)).replace("\\", "/"),
            rows=metadata.num_rows,
            min_values=min_values,
            max_values=max_values,
            schema_hash=schema_hash,
            content_hash=self._digest(target),
        )

    @staticmethod
    def _json_scalar(value: Any) -> str | int | float | None:
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat()
        if isinstance(value, (str, int, float)) or value is None:
            return value
        return str(value)

    @staticmethod
    def _manifest(
        region: RegionName,
        snapshot: str,
        files: list[SnapshotFile],
        code_version: str,
        config_version: str,
    ) -> SnapshotManifest:
        unsigned = {
            "snapshot_id": snapshot,
            "region": region,
            "files": [item.model_dump(mode="json") for item in files],
            "code_version": code_version,
            "config_version": config_version,
        }
        payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        manifest_hash = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        return SnapshotManifest(
            snapshot_id=snapshot,
            region=region,
            files=tuple(files),
            code_version=code_version,
            config_version=config_version,
            manifest_hash=manifest_hash,
        )

    @staticmethod
    def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(8):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt == 7:
                        raise
                    sleep(0.005 * (attempt + 1))
        finally:
            Path(temporary).unlink(missing_ok=True)

    def published_snapshot(self, region: RegionName = "gold") -> str | None:
        path = self.root / region / "published.json"
        value: Any | None = None
        for attempt in range(8):
            if not path.exists():
                return None
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                break
            except (FileNotFoundError, PermissionError):
                if attempt == 7:
                    raise
                sleep(0.005 * (attempt + 1))
        snapshot = value.get("snapshot_id") if isinstance(value, dict) else None
        if not isinstance(snapshot, str):
            raise SnapshotIntegrityError(f"invalid published alias: {path}")
        manifest = self.snapshot_manifest(snapshot)
        if manifest.region != region:
            raise SnapshotIntegrityError(f"published alias region mismatch: {path}")
        self.assert_snapshot_integrity(manifest)
        return snapshot

    def snapshot_manifest(self, snapshot_id: str) -> SnapshotManifest:
        path = self.root / "metadata" / "snapshots" / f"{self._safe_component(snapshot_id)}.json"
        return SnapshotManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def assert_snapshot_integrity(
        self, manifest: SnapshotManifest, *, base: Path | None = None
    ) -> None:
        directory = base or self.root / manifest.region / "snapshots" / manifest.snapshot_id
        errors: list[str] = []
        for file in manifest.files:
            target = directory / file.path
            if not target.exists():
                errors.append(f"missing:{file.path}")
            elif self._digest(target) != file.content_hash:
                errors.append(f"hash_mismatch:{file.path}")
        if errors:
            raise SnapshotIntegrityError(",".join(errors))

    def as_of_observations(self, snapshot_id: str, as_of: datetime) -> list[dict[str, Any]]:
        """Execute the no-future bitemporal predicate in DuckDB over Parquet."""

        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        self.assert_snapshot_integrity(self.snapshot_manifest(snapshot_id))
        _, _, duckdb, _ = require_analytical_engines()
        path = (
            self.root
            / "gold"
            / "snapshots"
            / self._safe_component(snapshot_id)
            / "observations.parquet"
        )
        if not path.exists():
            raise FileNotFoundError(path)
        query = """
            SELECT * FROM read_parquet(?)
            WHERE system_from <= ?
              AND (system_to IS NULL OR ? < system_to)
              AND observed_at <= ?
              AND valid_from <= ?
              AND (valid_to IS NULL OR ? < valid_to)
            ORDER BY natural_key, system_from
        """
        connection = duckdb.connect(":memory:")
        try:
            result = connection.execute(query, [str(path), as_of, as_of, as_of, as_of, as_of])
            columns = [column[0] for column in result.description]
            return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        finally:
            connection.close()

    def backup_snapshot(self, snapshot_id: str, destination: str | Path) -> Path:
        """Copy an immutable published snapshot plus its manifest as a recovery unit."""

        snapshot = self._safe_component(snapshot_id)
        self.assert_snapshot_integrity(self.snapshot_manifest(snapshot))
        backup = Path(destination) / snapshot
        if backup.exists():
            raise FileExistsError(backup)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.root / "gold" / "snapshots" / snapshot, backup)
        shutil.copy2(
            self.root / "metadata" / "snapshots" / f"{snapshot}.json", backup / "manifest.json"
        )
        return backup

    def backup_recovery_unit(
        self, snapshot_id: str, destination: str | Path, postgres: PostgresBackupAdapter
    ) -> Path:
        """Back up a Parquet snapshot and injected PostgreSQL state together."""

        backup = self.backup_snapshot(snapshot_id, destination)
        postgres.backup(backup / "postgres-backup")
        return backup

    def backup_raw_file(self, raw_file: str | Path, destination: str | Path) -> Path:
        """Copy a raw object to a second location and verify the copied bytes."""

        source = Path(raw_file)
        digest = self._digest(source)
        target = Path(destination) / source.name
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self.assert_raw_integrity(target, digest)
        return target

    def restore_snapshot(
        self,
        backup: str | Path,
        *,
        postgres: PostgresBackupAdapter | None = None,
        postgres_backup: str | Path | None = None,
    ) -> RecoveryReport:
        started = monotonic()
        source = Path(backup)
        manifest = SnapshotManifest.model_validate_json(
            (source / "manifest.json").read_text(encoding="utf-8")
        )
        target = self.root / "gold" / "snapshots" / manifest.snapshot_id
        if target.exists():
            raise FileExistsError(target)
        shutil.copytree(source, target)
        self._write_json_atomic(
            self.root / "metadata" / "snapshots" / f"{manifest.snapshot_id}.json",
            manifest.model_dump(mode="json"),
        )
        self.assert_snapshot_integrity(manifest)
        rehearsed = False
        if postgres is not None:
            if postgres_backup is None:
                raise ValueError("postgres_backup is required with a PostgreSQL adapter")
            postgres.restore(Path(postgres_backup))
            rehearsed = True
        return RecoveryReport(
            snapshot_id=manifest.snapshot_id,
            elapsed_seconds=monotonic() - started,
            hashes_match=True,
            postgres_rehearsed=rehearsed,
        )


def observations_to_rows(observations: Sequence[BitemporalObservation]) -> list[dict[str, Any]]:
    """Canonical row conversion retaining UTC system values and source local time."""

    return [observation.model_dump(mode="python") for observation in observations]
