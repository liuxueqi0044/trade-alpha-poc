"""Content-addressed, append-only local evidence store.

All commit operations use a same-directory temporary file followed by atomic
replacement.  Existing evidence is verified and reused, never overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Artifact, BatchManifest, IngestionEvent, RetrievalEnvelope


class ImmutableEvidenceError(RuntimeError):
    """Raised when code attempts to replace an immutable evidence object."""


class EvidenceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        for relative in (
            "raw",
            "manifests/retrievals",
            "manifests/batches",
            "quarantine",
            "events",
            "checkpoints",
        ):
            (self.root / relative).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def content_hash(content: bytes) -> str:
        return f"sha256:{hashlib.sha256(content).hexdigest()}"

    @staticmethod
    def extension_for_mime(mime_type: str) -> str:
        return {
            "application/json": "json",
            "text/csv": "csv",
            "text/html": "html",
            "application/pdf": "pdf",
            "application/xml": "xml",
            "text/xml": "xml",
        }.get(mime_type.split(";", 1)[0].lower(), "bin")

    def _raw_path(
        self, source_id: str, retrieved_at: datetime, digest: str, extension: str
    ) -> Path:
        safe_source = re.sub(r"[^a-zA-Z0-9_-]", "_", source_id)
        date = retrieved_at.astimezone(UTC).date().isoformat()
        return self.root / "raw" / safe_source / f"retrieved_date={date}" / f"{digest}.{extension}"

    @staticmethod
    def _atomic_create(path: Path, content: bytes) -> None:
        """Create an object once.  Caller must handle an existing target."""

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            # os.link is atomic and refuses an existing destination.  It avoids
            # os.replace, which would violate append-only evidence semantics.
            try:
                os.link(temp_name, path)
            except FileExistsError:
                raise ImmutableEvidenceError(f"immutable target exists: {path}") from None
            finally:
                Path(temp_name).unlink(missing_ok=True)
            # Read-only is defense in depth; atomic no-overwrite is the guarantee.
            with suppress(OSError):
                path.chmod(0o444)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def put_artifact(
        self,
        *,
        source_id: str,
        content: bytes,
        retrieved_at: datetime,
        mime_type_detected: str,
    ) -> Artifact:
        content_hash = self.content_hash(content)
        digest = content_hash.removeprefix("sha256:")
        catalog_path = self.root / "manifests" / "artifacts" / f"{digest}.json"
        # Artifact identity is global, not scoped to a source or retrieval date.
        # The catalog lets a later retrieval reuse its original immutable object.
        if catalog_path.exists():
            artifact = Artifact.model_validate_json(catalog_path.read_text(encoding="utf-8"))
            if (
                artifact.content_hash != content_hash
                or self.content_hash(self.raw_file_for(artifact).read_bytes()) != content_hash
            ):
                raise ImmutableEvidenceError(f"artifact catalog integrity failure: {catalog_path}")
            return artifact
        extension = self.extension_for_mime(mime_type_detected)
        path = self._raw_path(source_id, retrieved_at, digest, extension)
        if path.exists():
            existing_hash = self.content_hash(path.read_bytes())
            if existing_hash != content_hash:
                raise ImmutableEvidenceError(f"hash path integrity failure: {path}")
        else:
            try:
                self._atomic_create(path, content)
            except ImmutableEvidenceError:
                # Another writer won the race; validate rather than overwrite.
                if not path.exists() or self.content_hash(path.read_bytes()) != content_hash:
                    raise
        artifact = Artifact(
            artifact_id=content_hash,
            content_hash=content_hash,
            byte_length=len(content),
            storage_path=str(path.relative_to(self.root)).replace("\\", "/"),
            extension=extension,
            created_at=retrieved_at,
            mime_type_detected=mime_type_detected,
        )
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._atomic_create(catalog_path, artifact.model_dump_json().encode("utf-8"))
        except ImmutableEvidenceError:
            # A concurrent writer may have created the same content catalog.
            artifact = Artifact.model_validate_json(catalog_path.read_text(encoding="utf-8"))
            if artifact.content_hash != content_hash:
                raise
        return artifact

    def write_immutable(self, relative_path: str | Path, content: bytes) -> Path:
        """Public primitive for tests/administration; refuses every overwrite."""

        path = self.root / relative_path
        if path.exists():
            raise ImmutableEvidenceError(f"immutable target exists: {path}")
        self._atomic_create(path, content)
        return path

    def _atomic_json(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    def save_batch(self, batch: BatchManifest) -> None:
        path = (
            self.root
            / "manifests"
            / "batches"
            / f"source={batch.source_id}"
            / f"batch={batch.batch_id}.json"
        )
        self._atomic_json(path, batch.model_dump(mode="json"))

    def load_batch(self, source_id: str, batch_id: str) -> BatchManifest:
        path = (
            self.root / "manifests" / "batches" / f"source={source_id}" / f"batch={batch_id}.json"
        )
        return BatchManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def append_retrieval(self, retrieval: RetrievalEnvelope) -> None:
        date = retrieval.retrieved_at.date().isoformat()
        path = self.root / "manifests" / "retrievals" / f"date={date}" / "part-00000.jsonl"
        self._append_jsonl(path, retrieval.model_dump(mode="json"))

    def append_event(self, event: IngestionEvent) -> None:
        date = event.occurred_at.date().isoformat()
        self._append_jsonl(
            self.root / "events" / f"date={date}.jsonl", event.model_dump(mode="json")
        )

    def append_quarantine(self, retrieval: RetrievalEnvelope, reason: str) -> None:
        date = retrieval.retrieved_at.date().isoformat()
        value = {"reason": reason, "retrieval": retrieval.model_dump(mode="json")}
        self._append_jsonl(self.root / "quarantine" / f"date={date}.jsonl", value)

    @staticmethod
    def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_APPEND makes each single-line record append-only for local workers.
        payload = (
            json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            + b"\n"
        )
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)

    def get_checkpoint(self, source_id: str) -> str | None:
        path = self.root / "checkpoints" / f"{source_id}.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"invalid checkpoint manifest: {path}")
        cursor = value.get("cursor")
        if cursor is not None and not isinstance(cursor, str):
            raise ValueError(f"invalid checkpoint cursor: {path}")
        return cursor

    def commit_checkpoint(
        self, source_id: str, old_cursor: str | None, new_cursor: str | None, batch_id: str
    ) -> None:
        """Publish a cursor only after its owning batch has completed."""

        self._atomic_json(
            self.root / "checkpoints" / f"{source_id}.json",
            {
                "cursor": new_cursor,
                "batch_id": batch_id,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        self._append_jsonl(
            self.root / "checkpoints" / "audit.jsonl",
            {
                "source_id": source_id,
                "old_cursor": old_cursor,
                "new_cursor": new_cursor,
                "batch_id": batch_id,
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )

    def raw_file_for(self, artifact: Artifact) -> Path:
        return self.root / artifact.storage_path

    def record_url_version(self, source_id: str, redacted_url: str, artifact_id: str) -> str | None:
        """Record the latest observed version and return the artifact it supersedes."""

        key = hashlib.sha256(f"{source_id}\n{redacted_url}".encode()).hexdigest()
        path = self.root / "manifests" / "url_versions" / f"{key}.json"
        prior: str | None = None
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            prior = value.get("artifact_id")
        if prior != artifact_id:
            self._atomic_json(
                path, {"source_id": source_id, "url": redacted_url, "artifact_id": artifact_id}
            )
        return prior if prior != artifact_id else None

    def latest_url_artifact(self, source_id: str, redacted_url: str) -> str | None:
        key = hashlib.sha256(f"{source_id}\n{redacted_url}".encode()).hexdigest()
        path = self.root / "manifests" / "url_versions" / f"{key}.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"invalid URL version manifest: {path}")
        artifact_id = value.get("artifact_id")
        if artifact_id is not None and not isinstance(artifact_id, str):
            raise ValueError(f"invalid URL version artifact_id: {path}")
        return artifact_id
