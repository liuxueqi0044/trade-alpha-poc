"""RFQ intake orchestration with an evidence-only safety boundary."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Mapping, Sequence
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from typing import Any, Literal, cast
from uuid import uuid4

from trade_alpha.execution.adapters.local_intake_store import LocalArtifactCaptureAdapter
from trade_alpha.extraction import (
    CandidateClaim,
    DocumentBlock,
    DocumentFormat,
    ExtractionRun,
    ExtractionService,
)
from trade_alpha.extraction.claims import rule_extract
from trade_alpha.extraction.models import sha256_bytes, sha256_text
from trade_alpha.ingestion import EvidenceStore

from .extractors import CandidateExtractor, FeDry01CandidateExtractor
from .models import (
    ArtifactLink,
    IntakeQuarantineRecord,
    IntakeReceipt,
    Rfq,
    RfqFieldCandidate,
    RfqIntakeCommand,
    RfqLineItem,
    fingerprint,
    stable_id,
)
from .repository import RfqRepository

_QTY = re.compile(r"\b(?:qty|quantity|amount)\s*[:=#]?\s*([0-9][0-9,.]*)\s*([A-Za-z]+)?", re.I)
_CURRENCY = re.compile(r"\b(USD|EUR|CNY|RMB|GBP|JPY|CHF)\b", re.I)
_NUMBERED = re.compile(r"^\s*(?:item\s*)?(\d+)[.)\-:]\s*(.+?)\s*$", re.I)

MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 1_000.0


def _normalise_mime(value: str | None) -> str | None:
    return value.split(";", 1)[0].strip().lower() if value else None


def _sniff(data: bytes, declared: str | None, filename: str) -> tuple[str, str | None]:
    """Return a signature-derived MIME and an integrity issue, if any."""

    del filename
    if data.startswith(b"%PDF-"):
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(data)) as document:
                if getattr(getattr(document, "doc", None), "is_encrypted", False):
                    return "application/pdf", "encrypted_pdf"
                if not document.pages:
                    return "application/pdf", "corrupt_pdf"
        except Exception:
            return "application/pdf", "corrupt_pdf"
        return "application/pdf", None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", None
    if data.startswith((b"\xff\xd8\xff",)):
        return "image/jpeg", None
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", None
    if data.startswith(b"BM"):
        return "image/bmp", None
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                total_size = sum(max(0, info.file_size) for info in infos)
                if len(infos) > MAX_ARCHIVE_ENTRIES:
                    return "application/zip", "archive_entry_count_exceeded"
                if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    return "application/zip", "archive_uncompressed_size_exceeded"
                for info in infos:
                    if info.flag_bits & 0x1:
                        return "application/zip", "encrypted_or_empty_archive"
                    if info.file_size > 0 and (
                        info.compress_size == 0
                        or info.file_size / info.compress_size > MAX_ARCHIVE_COMPRESSION_RATIO
                    ):
                        return "application/zip", "archive_compression_ratio_exceeded"
                if not names:
                    return "application/zip", "encrypted_or_empty_archive"
                prefixes = {name.split("/", 1)[0] for name in names if "/" in name}
                office_prefixes = prefixes.intersection({"word", "xl", "ppt"})
                if not office_prefixes:
                    return "application/zip", "unrecognised_archive"
                if len(office_prefixes) > 1:
                    return "application/zip", "ambiguous_office_archive"
                office_prefix = next(iter(office_prefixes))
                required = {
                    "word": "word/document.xml",
                    "xl": "xl/workbook.xml",
                    "ppt": "ppt/presentation.xml",
                }
                if "[Content_Types].xml" not in names or required[office_prefix] not in names:
                    return "application/zip", "invalid_office_archive"
        except (OSError, zipfile.BadZipFile):
            return "application/zip", "corrupt_archive"
        office_mimes = {
            "word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xl": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ppt": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        return office_mimes[office_prefix], None
    lowered = data[:4096].lstrip().lower()
    if any(marker in lowered for marker in (b"<html", b"<!doctype html", b"<article")):
        return "text/html", None
    if _looks_like_email(data):
        return "message/rfc822", None
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return _normalise_mime(declared) or "application/octet-stream", "unrecognised_binary"
    # A declaration never upgrades a signature to a potentially unsafe type.
    return "text/plain", None


def _looks_like_email(data: bytes) -> bool:
    first = data[:4096].decode("ascii", errors="ignore").lower()
    return bool(re.search(r"(?:^|\n)(from|to|subject|date|mime-version):", first))


def _email_text(data: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(data)
    parts: list[str] = []
    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() == "text/plain":
            content = part.get_content()
            if isinstance(content, str):
                parts.append(content)
    if parts:
        return "\n".join(parts)
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return str(payload or "")


def _locator(block: DocumentBlock) -> Any:
    from trade_alpha.contracts.common import EvidenceLocator

    return EvidenceLocator(
        artifact_id=block.artifact_id,
        block_ids=(block.block_id,),
        page_number=block.page_number,
        bbox=block.bbox,
        start_ms=block.start_ms,
        end_ms=block.end_ms,
        quality=block.locator_quality,
    )


def _plain_run(artifact_id: str, data: bytes, text: str, parser: str) -> ExtractionRun:
    run_id = str(uuid4())
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    if not paragraphs and text.strip():
        paragraphs = [text.strip()]
    blocks = tuple(
        DocumentBlock(
            block_id=stable_id("text-block", artifact_id, str(index), sha256_text(value)),
            artifact_id=artifact_id,
            extraction_run_id=run_id,
            document_format=DocumentFormat.HTML,
            block_type="paragraph",
            reading_order=index,
            text=value,
            parser=parser,
            parser_version="1.0.0",
            content_hash=sha256_text(value),
        )
        for index, value in enumerate(paragraphs)
    )
    claims = rule_extract(blocks)
    return ExtractionRun(
        extraction_run_id=run_id,
        artifact_id=artifact_id,
        raw_sha256=sha256_bytes(data),
        document_format=DocumentFormat.HTML,
        parser=parser,
        parser_version="1.0.0",
        status="completed" if blocks else "unreadable",
        blocks=blocks,
        claims=claims,
        diagnostics=() if blocks else ("empty_text",),
        dependency_versions=((parser, "1.0.0"),),
    )


class RfqIntakeService:
    """Capture, parse, and project RFQs without outbound capabilities."""

    def __init__(
        self,
        store: EvidenceStore,
        *,
        repository: RfqRepository | None = None,
        capture_adapter: LocalArtifactCaptureAdapter | None = None,
        extraction_service: ExtractionService | None = None,
        candidate_extractors: Sequence[CandidateExtractor] = (),
    ) -> None:
        self.repository = repository or RfqRepository()
        self.capture = capture_adapter or LocalArtifactCaptureAdapter(store)
        self.extraction_service = extraction_service or ExtractionService()
        self.candidate_extractors = tuple(candidate_extractors) or (FeDry01CandidateExtractor(),)

    def _quarantine(
        self,
        artifact_id: str,
        data: bytes,
        mime: str,
        reason: str,
        details: str | None = None,
    ) -> IntakeQuarantineRecord:
        """Append a native, local quarantine record (never an HTTP envelope)."""

        record = IntakeQuarantineRecord(
            quarantine_id=stable_id("quarantine", artifact_id, reason, details or ""),
            artifact_id=artifact_id,
            reason=reason,
            details=details,
            mime_type=mime,
            byte_length=len(data),
        )
        return self.repository.save_quarantine(record)

    def extraction_configuration_signature(self) -> str:
        """Fingerprint parser registry and quality knobs for cache safety."""

        return fingerprint(
            {
                "backends": tuple(
                    sorted(
                        (name, getattr(backend, "version", "unknown"))
                        for name, backend in self.extraction_service.backends.items()
                    )
                ),
                "ocr_threshold": self.extraction_service.ocr_low_confidence_threshold,
            }
        )

    def _extract(
        self,
        link: ArtifactLink,
        data: bytes,
        *,
        parser_profile: str | None = None,
        integrity_issue: str | None = None,
    ) -> tuple[ExtractionRun, str]:
        extraction_fp = fingerprint({
            "artifact_id": link.artifact_id,
            "mime": link.mime_type,
            "parser": parser_profile,
            "configuration": self.extraction_configuration_signature(),
        })
        cached = self.repository.extraction_for(link.artifact_id, extraction_fp)
        if cached is not None:
            return cached, extraction_fp
        if integrity_issue:
            run = _plain_run(link.artifact_id, data, "", "none").model_copy(
                update={
                    "status": "quarantined",
                    "blocks": (),
                    "claims": (),
                    "diagnostics": (integrity_issue,),
                }
            )
            self._quarantine(link.artifact_id, data, link.mime_type, integrity_issue)
            return (
                self.repository.save_extraction(link.artifact_id, extraction_fp, run),
                extraction_fp,
            )
        if link.mime_type == "text/plain":
            run = _plain_run(link.artifact_id, data, data.decode("utf-8"), "plain-text")
        elif link.mime_type == "message/rfc822":
            run = _plain_run(link.artifact_id, data, _email_text(data), "email-text")
        else:
            run = self.extraction_service.extract(
                link.artifact_id,
                data,
                declared_mime=link.mime_type,
                filename=link.filename,
                parser_profile=parser_profile,
            )
        if run.status in {"quarantined", "unreadable"}:
            self._quarantine(
                link.artifact_id,
                data,
                link.mime_type,
                ";".join(run.diagnostics) or "unreadable",
            )
        return self.repository.save_extraction(link.artifact_id, extraction_fp, run), extraction_fp

    @staticmethod
    def _candidate_from_claim(
        rfq_id: str, claim: CandidateClaim, block: DocumentBlock, extraction_fp: str
    ) -> RfqFieldCandidate:
        status: Literal["proposed", "needs_review"] = (
            "needs_review" if block.low_confidence else "proposed"
        )
        return RfqFieldCandidate(
            candidate_id=stable_id("candidate", rfq_id, claim.claim_id),
            rfq_id=rfq_id,
            field_name=claim.predicate,
            value=claim.object_value,
            value_type=claim.object_type,
            artifact_id=block.artifact_id,
            block_id=block.block_id,
            evidence_block_ids=(block.block_id,),
            locator=_locator(block),
            extraction_run_id=block.extraction_run_id,
            extractor_type=claim.extractor_type,
            extractor_version=claim.extractor_version,
            extraction_fingerprint=extraction_fp,
            confidence=claim.confidence,
            status=status,
        )

    @staticmethod
    def _line_items(rfq_id: str, run: ExtractionRun) -> tuple[RfqLineItem, ...]:
        values: list[tuple[str, DocumentBlock]] = []
        for block in run.blocks:
            for line in block.text.splitlines():
                line = line.strip()
                if line:
                    values.append((line, block))
        if not values:
            return ()
        numbered = any(_NUMBERED.match(line) for line, _ in values)
        if not numbered:
            raw, _block = values[0]
            raw = "\n".join(line for line, _ in values)
            qty_match = _QTY.search(raw)
            currency_match = _CURRENCY.search(raw)
            return (
                RfqLineItem(
                    line_item_id=stable_id("line-item", rfq_id, "1", raw),
                    line_number=1,
                    description=raw,
                    raw_text=raw,
                    quantity=(
                        Decimal(qty_match.group(1).replace(",", ""))
                        if qty_match is not None
                        else None
                    ),
                    unit=qty_match.group(2) if qty_match is not None else None,
                    currency=currency_match.group(1).upper() if currency_match else None,
                    status="needs_review",
                ),
            )
        items: list[RfqLineItem] = []
        for index, (raw, _block) in enumerate(values, 1):
            match = _NUMBERED.match(raw)
            description = match.group(2).strip() if match else raw
            if numbered and match is None:
                continue
            qty_match = _QTY.search(raw)
            quantity = None
            unit = None
            if qty_match:
                quantity = qty_match.group(1).replace(",", "")
                unit = qty_match.group(2)
            currency_match = _CURRENCY.search(raw)
            items.append(
                RfqLineItem(
                    line_item_id=stable_id("line-item", rfq_id, str(index), raw),
                    line_number=int(match.group(1)) if match else index,
                    description=description,
                    raw_text=raw,
                    quantity=Decimal(quantity) if quantity is not None else None,
                    unit=unit,
                    currency=currency_match.group(1).upper() if currency_match else None,
                    status="needs_review" if qty_match is None else "proposed",
                )
            )
        return tuple(items)

    @staticmethod
    def _line_candidates(
        rfq_id: str,
        run: ExtractionRun,
        extraction_fp: str,
        line_items: list[RfqLineItem],
    ) -> tuple[RfqFieldCandidate, ...]:
        """Project stable, evidence-linked product fields from each line."""

        candidates: list[RfqFieldCandidate] = []
        item_index = 0
        numbered = any(_NUMBERED.match(item.raw_text) for item in line_items)
        for block in run.blocks:
            for raw in (line.strip() for line in block.text.splitlines() if line.strip()):
                match = _NUMBERED.match(raw)
                if item_index >= len(line_items):
                    continue
                if match is not None:
                    description = match.group(2).strip()
                elif numbered:
                    continue
                else:
                    description = raw
                item = line_items[item_index]
                item_index += 1
                values: list[tuple[str, str, str]] = [("description", description, "text")]
                quantity = _QTY.search(raw)
                if quantity:
                    values.append(("quantity", quantity.group(1).replace(",", ""), "number"))
                    if quantity.group(2):
                        values.append(("unit", quantity.group(2), "text"))
                currency = _CURRENCY.search(raw)
                if currency:
                    values.append(("currency", currency.group(1).upper(), "currency"))
                ids: list[str] = []
                for field_name, value, value_type in values:
                    candidate_id = stable_id(
                        "line-candidate", rfq_id, item.line_item_id, field_name
                    )
                    ids.append(candidate_id)
                    candidates.append(
                        RfqFieldCandidate(
                            candidate_id=candidate_id,
                            rfq_id=rfq_id,
                            line_item_id=item.line_item_id,
                            field_name=field_name,
                            value=value,
                            value_type=value_type,
                            artifact_id=block.artifact_id,
                            block_id=block.block_id,
                            evidence_block_ids=(block.block_id,),
                            locator=_locator(block),
                            extraction_run_id=block.extraction_run_id,
                            extractor_type="rule",
                            extractor_version="rfq-line-rules/1.0.0",
                            extraction_fingerprint=extraction_fp,
                            confidence=block.confidence,
                            status="needs_review" if block.low_confidence else "proposed",
                        )
                    )
                # Models are frozen; the caller replaces the corresponding line.
                line_items[item_index - 1] = item.model_copy(update={"candidate_ids": tuple(ids)})
        return tuple(candidates)

    def intake(self, command: RfqIntakeCommand) -> IntakeReceipt:
        intake_fp = command.command_fingerprint()
        identity = command.identity_key()
        prior_identity = self.repository.receipt_for_identity(identity) if identity else None
        if prior_identity is not None and prior_identity.intake_fingerprint != intake_fp:
            raise ValueError("identity already used for a different intake payload")
        prior = self.repository.receipt_for_fingerprint(intake_fp)
        if prior is not None:
            return prior

        body_mime, body_issue = _sniff(
            bytes(command.body), command.body_mime, command.body_filename
        )
        body_artifact = self.capture.capture(
            bytes(command.body), mime_type_detected=body_mime, captured_at=command.received_at
        )
        body_link = ArtifactLink(
            artifact_id=body_artifact.artifact_id,
            role="body",
            filename=command.body_filename,
            mime_type=body_mime,
            byte_length=len(command.body),
        )
        attachments = list(command.attachments)
        attachment_links: list[ArtifactLink] = []
        attachment_data: list[bytes] = []
        quarantine_issues: list[tuple[str, bytes, str, str]] = []
        for attachment in attachments:
            mime, issue = _sniff(attachment.content, attachment.declared_mime, attachment.filename)
            artifact = self.capture.capture(
                attachment.content, mime_type_detected=mime, captured_at=command.received_at
            )
            attachment_links.append(
                ArtifactLink(
                    artifact_id=artifact.artifact_id,
                    role="attachment",
                    filename=attachment.filename,
                    mime_type=mime,
                    parent_artifact_id=body_artifact.artifact_id,
                    byte_length=len(attachment.content),
                )
            )
            attachment_data.append(attachment.content)
            if issue:
                quarantine_issues.append((artifact.artifact_id, attachment.content, mime, issue))

        # EML attachments are captured separately while retaining the original
        # message as the body artifact.
        if body_mime == "message/rfc822":
            try:
                message = BytesParser(policy=policy.default).parsebytes(bytes(command.body))
                for part in message.walk():
                    if part.get_content_disposition() != "attachment":
                        continue
                    embedded_value = part.get_payload(decode=True)
                    embedded = embedded_value if isinstance(embedded_value, bytes) else b""
                    name = part.get_filename() or "eml-attachment"
                    mime, issue = _sniff(embedded, part.get_content_type(), name)
                    artifact = self.capture.capture(
                        embedded, mime_type_detected=mime, captured_at=command.received_at
                    )
                    attachment_links.append(
                        ArtifactLink(
                            artifact_id=artifact.artifact_id,
                            role="attachment",
                            filename=name,
                            mime_type=mime,
                            parent_artifact_id=body_artifact.artifact_id,
                            byte_length=len(embedded),
                        )
                    )
                    attachment_data.append(embedded)
                    if issue:
                        quarantine_issues.append((artifact.artifact_id, embedded, mime, issue))
            except Exception:
                body_issue = body_issue or "malformed_email"

        links = (body_link, *attachment_links)
        runs: list[ExtractionRun] = []
        extraction_fps: list[str] = []
        body_run, body_fp = self._extract(
            body_link, bytes(command.body), integrity_issue=body_issue
        )
        runs.append(body_run)
        extraction_fps.append(body_fp)
        for link, data in zip(attachment_links, attachment_data, strict=True):
            issue = next(
                (item[3] for item in quarantine_issues if item[0] == link.artifact_id), None
            )
            run, run_fp = self._extract(link, data, integrity_issue=issue)
            runs.append(run)
            extraction_fps.append(run_fp)
        if body_issue:
            self._quarantine(body_link.artifact_id, bytes(command.body), body_mime, body_issue)
        for artifact_id, data, mime, issue in quarantine_issues:
            self._quarantine(artifact_id, data, mime, issue)

        rfq_id = stable_id("rfq", intake_fp)
        all_blocks = tuple(block for run in runs for block in run.blocks)
        candidates: list[RfqFieldCandidate] = []
        for run, run_fp in zip(runs, extraction_fps, strict=True):
            block_by_id = {block.block_id: block for block in run.blocks}
            for claim in run.claims:
                block = block_by_id.get(claim.evidence_block_ids[0])
                if block is not None:
                    candidates.append(self._candidate_from_claim(rfq_id, claim, block, run_fp))
        line_items = list(self._line_items(rfq_id, body_run))
        candidates.extend(self._line_candidates(rfq_id, body_run, body_fp, line_items))
        candidate_fp = fingerprint(
            {
                "artifacts": [link.artifact_id for link in links],
                "extractors": [(item.name, item.version) for item in self.candidate_extractors],
            }
        )
        # Extensions are intentionally data-only.  Invalid extension output is
        # rejected and never allowed to become a confirmation.
        blocks_by_id = {block.block_id: block for block in all_blocks}
        for extractor in self.candidate_extractors:
            try:
                extras = extractor.extract(rfq_id=rfq_id, blocks=all_blocks, runs=runs)
                for value in extras:
                    if isinstance(value, RfqFieldCandidate):
                        block = blocks_by_id.get(value.block_id)
                        if (
                            value.rfq_id == rfq_id
                            and block is not None
                            and block.artifact_id == value.artifact_id
                            and value.locator.artifact_id == value.artifact_id
                            and value.block_id in value.evidence_block_ids
                        ):
                            candidates.append(value)
                        continue
                    if isinstance(value, Mapping):
                        # Extensions may return dictionaries, but the core
                        # still requires a fully evidenced, proposed object.
                        try:
                            candidate = RfqFieldCandidate.model_validate(value)
                            block = blocks_by_id.get(candidate.block_id)
                            if (
                                candidate.rfq_id == rfq_id
                                and block is not None
                                and block.artifact_id == candidate.artifact_id
                                and candidate.locator.artifact_id == candidate.artifact_id
                                and candidate.block_id in candidate.evidence_block_ids
                            ):
                                candidates.append(
                                    candidate
                                )
                        except Exception:
                            continue
            except Exception:
                continue
        self.repository.save_candidate_projection(candidate_fp, candidates)

        needs_review = (
            any(run.status in {"needs_review", "quarantined", "unreadable"} for run in runs)
            or any(item.status == "needs_review" for item in line_items)
            or body_issue is not None
            or bool(quarantine_issues)
        )
        status = "needs_review" if needs_review else "proposed"
        extraction_fp = fingerprint({"artifacts": [link.artifact_id for link in links]})
        rfq = self.repository.save_rfq(
            Rfq(
                rfq_id=rfq_id,
                revision=1,
                receipt_id=stable_id("receipt", intake_fp),
                artifact_links=links,
                line_items=tuple(line_items),
                field_candidates=tuple(candidates),
                status=cast(Literal["proposed", "needs_review"], status),
                intake_fingerprint=intake_fp,
                extraction_fingerprint=extraction_fp,
                candidate_fingerprint=candidate_fp,
                created_at=command.received_at,
            )
        )
        receipt_status = "needs_review" if status == "needs_review" else "completed"
        if body_run.status == "quarantined" and not all_blocks:
            receipt_status = "quarantined"
        receipt = IntakeReceipt(
            receipt_id=stable_id("receipt", intake_fp),
            intake_fingerprint=intake_fp,
            body=body_link,
            attachments=tuple(attachment_links),
            rfq_id=rfq.rfq_id,
            extraction_run_ids=tuple(run.extraction_run_id for run in runs),
            extraction_fingerprint=extraction_fp,
            candidate_fingerprint=candidate_fp,
            status=cast(Literal["completed", "needs_review", "quarantined"], receipt_status),
            received_at=command.received_at,
            created_at=command.received_at,
        )
        return self.repository.save_receipt_with_identity(receipt, identity=identity)

    # Common spelling used by adapter callers.
    receive = intake
    ingest = intake
