from __future__ import annotations

from pathlib import Path

from trade_alpha.execution.rfq import IntakeAttachment, RfqIntakeCommand, RfqIntakeService
from trade_alpha.extraction.docling_backend import DoclingBackend
from trade_alpha.ingestion import EvidenceStore


def make_service(tmp_path: Path) -> RfqIntakeService:
    return RfqIntakeService(EvidenceStore(tmp_path))


def test_ep02_text_and_attachment_are_independent_evidence(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    receipt = service.intake(
        RfqIntakeCommand(
            body="1. Steel bolt Qty: 100 pcs USD\n2. Nut Qty: 20 pcs EUR",
            attachments=(
                IntakeAttachment(content=b"<html><p>2026-08-24</p></html>", filename="quote.html"),
            ),
        )
    )
    rfq = service.repository.get_rfq(receipt.rfq_id)
    assert rfq is not None
    assert len(rfq.line_items) == 2
    assert receipt.body.artifact_id != receipt.attachments[0].artifact_id
    assert receipt.attachments[0].parent_artifact_id == receipt.body.artifact_id
    assert all(
        candidate.block_id in candidate.evidence_block_ids for candidate in rfq.field_candidates
    )
    assert all(
        candidate.locator.artifact_id == candidate.artifact_id
        for candidate in rfq.field_candidates
    )


def test_ep02_duplicate_submission_has_no_new_receipt_or_run(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    command = RfqIntakeCommand(body="1. widget Qty: 3 pcs")
    first = service.intake(command)
    second = service.intake(command)
    assert first == second
    assert len(service.repository.all_receipts()) == 1
    assert len(service.repository.all_rfqs()) == 1
    assert len(service.repository.runs_for(first.body.artifact_id)) == 1


def test_ep02_corrupt_pdf_is_quarantined_without_confirmation(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    receipt = service.intake(RfqIntakeCommand(body=b"%PDF-1.7\nnot a pdf"))
    assert receipt.status in {"needs_review", "quarantined"}
    rfq = service.repository.get_rfq(receipt.rfq_id)
    assert rfq is not None and all(item.status != "confirmed" for item in rfq.field_candidates)
    assert service.repository.quarantine


def test_ep02_docling_boundary_is_lazy_and_optional() -> None:
    backend = DoclingBackend()
    if not backend.available():
        assert backend.version == "optional"
