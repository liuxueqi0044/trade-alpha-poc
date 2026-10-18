from __future__ import annotations

import io
import sys
import types
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trade_alpha.execution.rfq import (
    FeDry01CandidateExtractor,
    RfqIntakeCommand,
    RfqIntakeService,
)
from trade_alpha.execution.rfq.service import _sniff
from trade_alpha.execution.specifications.profiles import load_profile
from trade_alpha.extraction.docling_backend import DoclingBackend
from trade_alpha.extraction.models import DocumentFormat
from trade_alpha.ingestion import EvidenceStore


def office_zip(prefix: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", b"types")
        entry = {
            "word": "document.xml",
            "xl": "workbook.xml",
            "ppt": "presentation.xml",
        }[prefix]
        archive.writestr(f"{prefix}/{entry}", b"content")
    return output.getvalue()


def test_office_signature_classification_and_native_quarantine() -> None:
    assert _sniff(office_zip("word"), None, "x.docx")[0].endswith("wordprocessingml.document")
    assert _sniff(office_zip("xl"), None, "x.xlsx")[0].endswith("spreadsheetml.sheet")
    assert _sniff(office_zip("ppt"), None, "x.pptx")[0].endswith("presentationml.presentation")


def test_archive_bounds_are_checked_from_metadata_without_expanding() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"A" * (2 * 1024 * 1024))
    mime, issue = _sniff(output.getvalue(), None, "large.docx")
    assert mime == "application/zip"
    assert issue == "archive_compression_ratio_exceeded"

    many = io.BytesIO()
    with zipfile.ZipFile(many, "w") as archive:
        for index in range(10_001):
            archive.writestr(f"word/{index}.xml", b"")
    assert _sniff(many.getvalue(), None, "many.docx")[1] == "archive_entry_count_exceeded"


def test_attachment_sniff_issue_is_persisted_and_affects_status(tmp_path: Path) -> None:
    service = RfqIntakeService(EvidenceStore(tmp_path))
    receipt = service.intake(
        RfqIntakeCommand(body="1. widget", attachments=(b"PK\x03\x04broken",))
    )
    assert receipt.status == "needs_review"
    assert service.repository.quarantine
    assert service.repository.quarantine[0].artifact_id == receipt.attachments[0].artifact_id


def test_abnormal_archive_is_quarantined_before_any_parser_call(tmp_path: Path) -> None:
    class SpyExtraction:
        backends = {}
        ocr_low_confidence_threshold = 0.75

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("abnormal archive must not reach a parser")

    spy = SpyExtraction()
    service = RfqIntakeService(EvidenceStore(tmp_path), extraction_service=spy)  # type: ignore[arg-type]
    receipt = service.intake(
        RfqIntakeCommand(body="1. widget", attachments=(b"PK\x03\x04broken",))
    )
    assert spy.calls == 0
    assert receipt.status == "needs_review"


def test_inbound_identity_distinguishes_arrivals_but_retries_are_idempotent(tmp_path: Path) -> None:
    service = RfqIntakeService(EvidenceStore(tmp_path))
    first = service.intake(
        RfqIntakeCommand(body="same", received_at=datetime(2026, 1, 1, tzinfo=UTC))
    )
    second = service.intake(
        RfqIntakeCommand(body="same", received_at=datetime(2026, 1, 2, tzinfo=UTC))
    )
    assert first.receipt_id != second.receipt_id
    assert first.body_artifact_id == second.body_artifact_id

    keyed = RfqIntakeCommand(body="same", external_message_id="mail-1")
    keyed_receipt = service.intake(keyed)
    assert keyed_receipt == service.intake(keyed)
    with pytest.raises(ValueError, match="different intake payload"):
        service.intake(RfqIntakeCommand(body="changed", external_message_id="mail-1"))
    other_buyer = service.intake(
        RfqIntakeCommand(body="changed", external_message_id="mail-1", buyer_entity_id="buyer-2")
    )
    assert other_buyer.receipt_id != keyed_receipt.receipt_id
    assert all("mail-1" not in key for key in service.repository._fingerprint_by_identity)


def test_extraction_configuration_change_invalidates_cache(tmp_path: Path) -> None:
    service = RfqIntakeService(EvidenceStore(tmp_path))
    first = service.intake(
        RfqIntakeCommand(
            body="<html><p>same</p></html>",
            received_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    fallback = service.extraction_service.backends["html-fallback"]
    fallback.version = "changed-for-test"
    second = service.intake(
        RfqIntakeCommand(
            body="<html><p>same</p></html>",
            received_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    )
    assert first.body_artifact_id == second.body_artifact_id
    assert len(service.repository.runs_for(first.body_artifact_id)) == 2


def test_fe_dry_01_extracts_labelled_fields_and_marks_ambiguity(tmp_path: Path) -> None:
    service = RfqIntakeService(
        EvidenceStore(tmp_path), candidate_extractors=(FeDry01CandidateExtractor(),)
    )
    receipt = service.intake(
        RfqIntakeCommand(
            body="OEM model: ACME-7\nOuter diameter: 10 mm\nInner diameter: 5 mm\n"
            "Length: 20 mm\nMaterial: steel or aluminum\nPerformance: 100V\n"
            "Packaging: 5 per carton\nDust toxicity: TBD"
        )
    )
    rfq = service.repository.get_rfq(receipt.rfq_id)
    assert rfq is not None
    fields = {candidate.field_name: candidate for candidate in rfq.field_candidates}
    assert {
        "oem_model_reference",
        "outer_diameter",
        "inner_diameter",
        "length",
        "filter_material",
        "performance_requirement",
        "packaging_requirement",
        "dust_toxicity",
    } <= set(fields)
    assert fields["dust_toxicity"].status == "needs_review"
    assert fields["filter_material"].status == "needs_review"


def test_fe_dry_01_keys_and_values_match_profile(tmp_path: Path) -> None:
    service = RfqIntakeService(EvidenceStore(tmp_path))
    receipt = service.intake(
        RfqIntakeCommand(
            body="Outer diameter: 10 mm\nInner diameter: 5 mm\nLength: 20 mm\n"
            "First order quantity: 25 pcs\nFilter material: PTFE"
        )
    )
    rfq = service.repository.get_rfq(receipt.rfq_id)
    assert rfq is not None
    profile = load_profile("configs/execution/product-families/FE-DRY-01-v1.yaml")
    profile_keys = {definition.key for definition in profile.field_definitions}
    fields = {candidate.field_name: candidate for candidate in rfq.field_candidates}
    assert set(fields) <= profile_keys | {"description", "currency", "unit", "quantity"}
    assert fields["outer_diameter"].value == "10 mm"
    assert fields["first_order_quantity"].value == "25 piece"


def test_fe_dry_01_application_scope_is_explicit_and_normalized(tmp_path: Path) -> None:
    service = RfqIntakeService(EvidenceStore(tmp_path))
    receipt = service.intake(
        RfqIntakeCommand(
            body="Application scope: general industrial dry dust\nIntended use: medical"
        )
    )
    rfq = service.repository.get_rfq(receipt.rfq_id)
    assert rfq is not None
    fields = {candidate.field_name: candidate for candidate in rfq.field_candidates}
    assert fields["application_scope"].value == "medical"
    assert fields["application_scope"].status == "proposed"

    first = service.intake(
        RfqIntakeCommand(
            body="Application scope: general industrial dry dust",
            received_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    )
    first_rfq = service.repository.get_rfq(first.rfq_id)
    assert first_rfq is not None
    assert any(
        candidate.value == "general_industrial_dry_dust"
        for candidate in first_rfq.field_candidates
        if candidate.field_name == "application_scope"
    )


def test_unnumbered_specification_is_one_review_line(tmp_path: Path) -> None:
    service = RfqIntakeService(EvidenceStore(tmp_path))
    receipt = service.intake(
        RfqIntakeCommand(body="Material: PTFE\nLength: 20 mm\nPackaging: carton")
    )
    rfq = service.repository.get_rfq(receipt.rfq_id)
    assert rfq is not None and len(rfq.line_items) == 1
    assert rfq.line_items[0].status == "needs_review"


def test_docling_receives_a_named_document_stream(monkeypatch) -> None:
    docling = types.ModuleType("docling")
    docling.__path__ = []
    converter_module = types.ModuleType("docling.document_converter")
    base_module = types.ModuleType("docling.datamodel.base_models")
    seen: list[object] = []

    class DocumentStream:
        def __init__(self, *, name: str, stream: io.BytesIO) -> None:
            self.name = name
            self.stream = stream

    class Converter:
        def convert(self, *, source: object) -> object:
            seen.append(source)
            return types.SimpleNamespace(
                document=types.SimpleNamespace(export_to_markdown=lambda: "hello")
            )

    converter_module.DocumentConverter = Converter
    base_module.DocumentStream = DocumentStream
    monkeypatch.setitem(sys.modules, "docling", docling)
    monkeypatch.setitem(sys.modules, "docling.document_converter", converter_module)
    monkeypatch.setitem(sys.modules, "docling.datamodel", types.ModuleType("docling.datamodel"))
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", base_module)

    blocks = DoclingBackend().parse(b"%PDF-fake", DocumentFormat.PDF)
    assert blocks[0].text == "hello"
    assert len(seen) == 1
    assert isinstance(seen[0], DocumentStream)
    assert seen[0].name == "input.pdf"
    assert seen[0].stream.read() == b"%PDF-fake"
