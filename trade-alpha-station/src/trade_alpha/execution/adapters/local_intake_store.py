"""Thin local evidence adapter for RFQ intake."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trade_alpha.ingestion import Artifact, EvidenceStore


class LocalArtifactCaptureAdapter:
    """Capture uploads through M01's immutable, content-addressed store."""

    def __init__(self, store: EvidenceStore, *, source_id: str = "local_rfq") -> None:
        self.store = store
        self.source_id = source_id

    def capture(
        self,
        content: bytes,
        *,
        mime_type_detected: str,
        captured_at: datetime | None = None,
    ) -> Artifact:
        when = captured_at or datetime.now(UTC)
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return self.store.put_artifact(
            source_id=self.source_id,
            content=content,
            retrieved_at=when.astimezone(UTC),
            mime_type_detected=mime_type_detected,
        )

    put_artifact = capture

    def raw_path(self, artifact: Artifact) -> Path:
        return self.store.raw_file_for(artifact)
