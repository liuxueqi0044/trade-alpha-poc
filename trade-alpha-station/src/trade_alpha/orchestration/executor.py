"""Reference execution semantics; business transformations remain supplied callables."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import sleep
from uuid import NAMESPACE_URL, uuid5

from .contracts import (
    AlertRouter,
    AssetSpec,
    Issue,
    QualityStatus,
    RetryPolicy,
    RunManifest,
    RunStatus,
    Severity,
    UpstreamInput,
    build_asset_graph,
)


class TransientFailure(RuntimeError):
    """A retryable network/429/5xx failure."""


class SchemaFailure(ValueError):
    """A terminal non-retryable semantic/schema failure."""


class UpstreamBlocked(RuntimeError):
    """Raised before an operation executes when evidence cannot support it."""


@dataclass(frozen=True)
class OperationResult:
    output_snapshot_id: str
    output_hash: str
    row_count: int
    quality_status: QualityStatus = QualityStatus.PASSED


class CheckpointStore:
    """Checkpoint state is separate from publication aliases and output commits."""

    def __init__(self) -> None:
        self._checkpoints: dict[tuple[str, str], str] = {}

    def save(self, asset_key: str, partition_key: str, checkpoint: str) -> None:
        self._checkpoints[(asset_key, partition_key)] = checkpoint

    def get(self, asset_key: str, partition_key: str) -> str | None:
        return self._checkpoints.get((asset_key, partition_key))

    def clear(self, asset_key: str, partition_key: str) -> None:
        self._checkpoints.pop((asset_key, partition_key), None)


class ReferenceExecutor:
    """Deterministic execution contract with retry, checkpoint and idempotent commit."""

    def __init__(
        self,
        retry_policy: RetryPolicy | None = None,
        sleeper: Callable[[float], None] = sleep,
        asset_graph: dict[str, AssetSpec] | None = None,
    ) -> None:
        self.retry_policy = retry_policy or RetryPolicy()
        self._sleeper = sleeper
        self.asset_graph = asset_graph or build_asset_graph()
        self.checkpoints = CheckpointStore()
        self.manifests: list[RunManifest] = []
        self.issues: list[Issue] = []
        self._published: dict[str, OperationResult] = {}
        self.alerts = AlertRouter()

    def execute(
        self,
        asset_key: str,
        partition_key: str,
        upstreams: Sequence[UpstreamInput],
        operation: Callable[[str | None], OperationResult],
        *,
        code_sha: str = "code/test",
        environment_lock_hash: str = "env/test",
        config_hash: str = "config/test",
    ) -> RunManifest:
        self._require_asset_context(asset_key, upstreams)
        self._require_healthy_upstreams(asset_key, partition_key, upstreams)
        publication_key = self.materialization_identity(
            asset_key, partition_key, code_sha, environment_lock_hash, config_hash, upstreams
        )
        started = datetime.now(UTC)
        if existing := self._published.get(publication_key):
            manifest = self._manifest(
                asset_key,
                partition_key,
                started,
                RunStatus.SUCCESS,
                code_sha,
                environment_lock_hash,
                config_hash,
                upstreams,
                existing,
                retry_count=0,
            )
            self.manifests.append(manifest)
            return manifest
        retry_count = 0
        while True:
            try:
                result = operation(self.checkpoints.get(asset_key, partition_key))
                if result.quality_status is QualityStatus.FAILED:
                    raise SchemaFailure("output quality gate failed")
                # The only publication point is after a successful operation and
                # quality gate. Exceptions never create a half-published value.
                self._published[publication_key] = result
                self.checkpoints.clear(asset_key, partition_key)
                manifest = self._manifest(
                    asset_key,
                    partition_key,
                    started,
                    RunStatus.SUCCESS,
                    code_sha,
                    environment_lock_hash,
                    config_hash,
                    upstreams,
                    result,
                    retry_count,
                )
                self.manifests.append(manifest)
                return manifest
            except TransientFailure as exc:
                if retry_count >= self.retry_policy.max_retries:
                    return self._terminal_manifest(
                        asset_key,
                        partition_key,
                        started,
                        code_sha,
                        environment_lock_hash,
                        config_hash,
                        upstreams,
                        retry_count,
                        "transient-exhausted",
                        str(exc),
                    )
                self._sleeper(float(self.retry_policy.backoff_seconds * (2**retry_count)))
                retry_count += 1
            except SchemaFailure as exc:
                return self._terminal_manifest(
                    asset_key,
                    partition_key,
                    started,
                    code_sha,
                    environment_lock_hash,
                    config_hash,
                    upstreams,
                    retry_count,
                    "schema",
                    str(exc),
                )
            except Exception as exc:
                return self._terminal_manifest(
                    asset_key,
                    partition_key,
                    started,
                    code_sha,
                    environment_lock_hash,
                    config_hash,
                    upstreams,
                    retry_count,
                    "unexpected",
                    f"{type(exc).__name__}: {exc}",
                )

    @staticmethod
    def materialization_identity(
        asset_key: str,
        partition_key: str,
        code_sha: str,
        environment_lock_hash: str,
        config_hash: str,
        upstreams: Sequence[UpstreamInput],
    ) -> str:
        """Stable content identity; changed input/code/environment cannot reuse output."""
        payload = {
            "asset_key": asset_key,
            "partition_key": partition_key,
            "code_sha": code_sha,
            "environment_lock_hash": environment_lock_hash,
            "config_hash": config_hash,
            "inputs": sorted(
                (
                    item.asset_key,
                    item.partition_key,
                    item.snapshot_id,
                    item.snapshot_hash,
                )
                for item in upstreams
            ),
        }
        return sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()

    def _require_asset_context(self, asset_key: str, upstreams: Sequence[UpstreamInput]) -> None:
        spec = self.asset_graph.get(asset_key)
        if spec is None:
            raise ValueError(f"unknown asset: {asset_key}")
        expected = set(spec.upstreams)
        supplied = {item.asset_key for item in upstreams}
        if missing := expected - supplied:
            raise ValueError(f"missing upstream evidence for {asset_key}: {sorted(missing)}")

    def _require_healthy_upstreams(
        self, asset_key: str, partition_key: str, upstreams: Sequence[UpstreamInput]
    ) -> None:
        unhealthy = [
            item.asset_key
            for item in upstreams
            if not item.complete or item.quality_status is QualityStatus.FAILED
        ]
        if unhealthy:
            raise UpstreamBlocked(f"{asset_key}/{partition_key} blocked by: {', '.join(unhealthy)}")

    def _terminal_manifest(
        self,
        asset_key: str,
        partition_key: str,
        started: datetime,
        code_sha: str,
        environment_lock_hash: str,
        config_hash: str,
        upstreams: Sequence[UpstreamInput],
        retry_count: int,
        category: str,
        message: str,
    ) -> RunManifest:
        manifest = self._manifest(
            asset_key,
            partition_key,
            started,
            RunStatus.FAILED,
            code_sha,
            environment_lock_hash,
            config_hash,
            upstreams,
            None,
            retry_count,
        )
        self.manifests.append(manifest)
        self.issues.append(
            Issue(Severity.ERROR, asset_key, partition_key, category, message, manifest.run_id)
        )
        return manifest

    @staticmethod
    def _manifest(
        asset_key: str,
        partition_key: str,
        started: datetime,
        status: RunStatus,
        code_sha: str,
        environment_lock_hash: str,
        config_hash: str,
        upstreams: Sequence[UpstreamInput],
        result: OperationResult | None,
        retry_count: int,
    ) -> RunManifest:
        run_id = str(
            uuid5(
                NAMESPACE_URL,
                f"m06/{asset_key}/{partition_key}/{config_hash}/{started.isoformat()}",
            )
        )
        return RunManifest(
            run_id=run_id,
            asset_key=asset_key,
            partition_key=partition_key,
            started_at=started,
            ended_at=datetime.now(UTC),
            status=status,
            code_sha=code_sha,
            environment_lock_hash=environment_lock_hash,
            config_hash=config_hash,
            input_snapshot_ids=tuple(item.snapshot_id for item in upstreams),
            input_hashes=tuple(item.snapshot_hash for item in upstreams),
            output_snapshot_id=result.output_snapshot_id if result else None,
            output_hash=result.output_hash if result else None,
            row_count=result.row_count if result else None,
            quality_status=result.quality_status if result else QualityStatus.FAILED,
            retry_count=retry_count,
        )
