"""Evidence-first contracts for RFQ intake and product extraction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trade_alpha.contracts.common import EvidenceLocator, require_utc, utc_now


def stable_id(kind: str, *parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, "/".join(("trade-alpha", "ep02", kind, *parts))))


def fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


class IntakeAttachment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    content: bytes
    filename: str = "attachment"
    declared_mime: str | None = None

    @field_validator("filename")
    @classmethod
    def filename_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("filename must not be blank")
        return value


class RfqIntakeCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    body: bytes
    body_filename: str = "body.txt"
    body_mime: str | None = None
    attachments: tuple[IntakeAttachment, ...] = ()
    idempotency_key: str | None = None
    external_message_id: str | None = None
    buyer_entity_id: str | None = None
    channel: str | None = None
    submitted_by: str | None = None
    sender: str | None = None
    subject: str | None = None
    received_at: datetime = Field(default_factory=utc_now)

    @field_validator("body", mode="before")
    @classmethod
    def body_bytes(cls, value: object) -> bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        raise TypeError("body must be bytes or text")

    @field_validator("attachments", mode="before")
    @classmethod
    def attachment_values(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(
                {"content": item} if isinstance(item, bytes) else item for item in value
            )
        raise TypeError("attachments must be a sequence")

    @field_validator("received_at")
    @classmethod
    def received_at_utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    def command_fingerprint(self) -> str:
        payload: dict[str, object] = {
            "idempotency_key": self.idempotency_key,
            "external_message_id": self.external_message_id,
            "body": bytes(self.body).hex(),
            "body_filename": self.body_filename,
            "body_mime": self.body_mime,
            "attachments": [
                {
                    "content": item.content.hex(),
                    "filename": item.filename,
                    "mime": item.declared_mime,
                }
                for item in self.attachments
            ],
            "sender": self.sender,
            "subject": self.subject,
            "buyer_entity_id": self.buyer_entity_id,
            "channel": self.channel,
            "submitted_by": self.submitted_by,
        }
        if self.idempotency_key is None and self.external_message_id is None:
            payload["received_at"] = self.received_at.isoformat()
        return fingerprint(payload)

    def identity_key(self) -> str | None:
        kind: str
        value: str | None
        if self.idempotency_key is not None:
            kind, value = "idempotency", self.idempotency_key
        elif self.external_message_id is not None:
            kind, value = "external_message", self.external_message_id
        else:
            return None
        # Scope the dedupe key to the participant/channel and retain only its
        # digest in the repository, never the raw external identifier.
        return fingerprint(
            {
                "kind": kind,
                "value": value,
                "buyer_entity_id": self.buyer_entity_id,
                "channel": self.channel,
                "submitted_by": self.submitted_by,
                "sender": self.sender,
            }
        )


class ArtifactLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(min_length=1)
    role: Literal["body", "attachment"]
    filename: str
    mime_type: str
    parent_artifact_id: str | None = None
    byte_length: int = Field(ge=0)

    @property
    def artifact_role(self) -> str:
        return self.role


class RfqFieldCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1)
    rfq_id: str = Field(min_length=1)
    line_item_id: str | None = None
    field_name: str = Field(min_length=1)
    value: str | int | float | bool
    value_type: str = "text"
    artifact_id: str = Field(min_length=1)
    block_id: str = Field(min_length=1)
    evidence_block_ids: tuple[str, ...] = ()
    locator: EvidenceLocator
    extraction_run_id: str = Field(min_length=1)
    extractor_type: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    extraction_fingerprint: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    status: Literal["proposed", "needs_review", "rejected_schema"] = "proposed"
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def created_at_utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @field_validator("evidence_block_ids", mode="before")
    @classmethod
    def default_evidence_blocks(cls, value: object) -> tuple[str, ...]:
        return tuple(cast(Iterable[str], value)) if value else ()

    @property
    def evidence_locator(self) -> EvidenceLocator:
        return self.locator


class RfqLineItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    line_item_id: str = Field(min_length=1)
    line_number: int = Field(ge=1)
    description: str = Field(min_length=1)
    raw_text: str = ""
    quantity: Decimal | None = None
    unit: str | None = None
    currency: str | None = None
    candidate_ids: tuple[str, ...] = ()
    status: Literal["proposed", "needs_review"] = "proposed"


class Rfq(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rfq_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    receipt_id: str = Field(min_length=1)
    artifact_links: tuple[ArtifactLink, ...] = Field(min_length=1)
    line_items: tuple[RfqLineItem, ...] = ()
    field_candidates: tuple[RfqFieldCandidate, ...] = ()
    status: Literal["proposed", "needs_review"] = "proposed"
    intake_fingerprint: str = Field(min_length=1)
    extraction_fingerprint: str = Field(min_length=1)
    candidate_fingerprint: str = "not-computed"
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def created_at_utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @property
    def ingest_fingerprint(self) -> str:
        return self.intake_fingerprint

    @property
    def candidate_extraction_fingerprint(self) -> str:
        return self.candidate_fingerprint

    @property
    def artifact_links_all(self) -> tuple[ArtifactLink, ...]:
        return self.artifact_links


class IntakeReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(min_length=1)
    intake_fingerprint: str = Field(min_length=1)
    body: ArtifactLink
    attachments: tuple[ArtifactLink, ...] = ()
    rfq_id: str = Field(min_length=1)
    extraction_run_ids: tuple[str, ...] = ()
    extraction_fingerprint: str = Field(min_length=1)
    candidate_fingerprint: str = "not-computed"
    status: Literal["completed", "needs_review", "quarantined"]
    received_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("received_at", "created_at")
    @classmethod
    def timestamps_utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @property
    def ingest_fingerprint(self) -> str:
        return self.intake_fingerprint

    @property
    def body_artifact_id(self) -> str:
        return self.body.artifact_id

    @property
    def attachment_artifact_ids(self) -> tuple[str, ...]:
        return tuple(item.artifact_id for item in self.attachments)

    @property
    def artifact_links(self) -> tuple[ArtifactLink, ...]:
        return (self.body, *self.attachments)


class IntakeQuarantineRecord(BaseModel):
    """EP-02 native quarantine entry for a captured local artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quarantine_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    details: str | None = None
    mime_type: str = Field(min_length=1)
    byte_length: int = Field(ge=0)
    observed_at: datetime = Field(default_factory=utc_now)
    retryable: bool = True

    @field_validator("observed_at")
    @classmethod
    def observed_at_utc(cls, value: datetime) -> datetime:
        return require_utc(value)


CandidateStatus = Literal["proposed", "needs_review", "rejected_schema"]
