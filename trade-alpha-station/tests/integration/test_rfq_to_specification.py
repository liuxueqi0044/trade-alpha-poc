from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from trade_alpha.execution.adapters.knowledge_claims import KnowledgeClaimAdapter
from trade_alpha.execution.rfq import RfqIntakeCommand, RfqIntakeService
from trade_alpha.execution.specifications import (
    DecisionAction,
    FieldState,
    GateStatus,
    SpecFieldDecision,
    SpecificationService,
    SpecState,
    load_profile,
)
from trade_alpha.ingestion import EvidenceStore
from trade_alpha.knowledge import KnowledgeStore

NOW = datetime(2026, 8, 27, 1, tzinfo=UTC)
PROFILE_PATH = (
    Path(__file__).parents[2]
    / "configs"
    / "execution"
    / "product-families"
    / "FE-DRY-01-v1.yaml"
)


def test_x_ac_014_rfq_evidence_to_frozen_specification(tmp_path: Path) -> None:
    raw_rfq = (
        "Application scope: general industrial dry dust\n"
        "Dust toxicity: no\n"
        "Dust combustibility: no\n"
        "ATEX required: no\n"
        "OEM model: ACME-FE-7\n"
        "Outer diameter: 325 mm\n"
        "Outer diameter: 330 mm\n"
        "Inner diameter: 200 mm\n"
        "Length: 25.4 mm\n"
        "Length: 1 inch\n"
        "First order quantity: 100 pcs\n"
        "Filter material: polyester\n"
        "Performance requirement: 99.9 percent at 5 micron\n"
        "Packaging requirement: cartons with batch labels"
    )
    evidence = EvidenceStore(tmp_path / "evidence")
    intake = RfqIntakeService(evidence)
    command = RfqIntakeCommand(
        body=raw_rfq,
        channel="paste",
        buyer_entity_id="buyer-demo-1",
        submitted_by="operator-1",
        idempotency_key="rfq-demo-1",
        received_at=NOW,
    )

    receipt = intake.intake(command)
    assert intake.intake(command) == receipt
    assert evidence.raw_file_for(
        evidence.put_artifact(
            source_id="local_rfq",
            content=raw_rfq.encode(),
            retrieved_at=NOW,
            mime_type_detected="text/plain",
        )
    ).read_text(encoding="utf-8") == raw_rfq

    rfq = intake.repository.get_rfq(receipt.rfq_id)
    assert rfq is not None and len(rfq.line_items) == 1
    profile = load_profile(PROFILE_PATH)
    profile_keys = {definition.key for definition in profile.field_definitions}
    candidates = tuple(
        candidate for candidate in rfq.field_candidates if candidate.field_name in profile_keys
    )
    assert candidates
    assert all(candidate.artifact_id == receipt.body_artifact_id for candidate in candidates)
    assert all(candidate.block_id in candidate.evidence_block_ids for candidate in candidates)

    spec_id = rfq.line_items[0].line_item_id
    knowledge = KnowledgeStore()
    claim_adapter = KnowledgeClaimAdapter(knowledge)
    claims_by_field: dict[str, list[str]] = {}
    for candidate in candidates:
        claim = claim_adapter.add_candidate(spec_id, candidate, observed_at=NOW)
        claims_by_field.setdefault(candidate.field_name, []).append(claim.claim_id)

    specifications = SpecificationService(claim_adapter, (profile,))
    initial = specifications.create_spec(
        spec_id,
        profile.ref,
        created_at=NOW,
        rfq_id=rfq.rfq_id,
        rfq_item_id=rfq.line_items[0].line_item_id,
        created_by="operator-1",
    )
    fields = {field.field_key: field for field in initial.fields}
    assert fields["length"].state is FieldState.CANDIDATE
    assert len(fields["length"].source_claim_ids) == 2
    assert fields["outer_diameter"].state is FieldState.CONFLICT
    assert initial.readiness.status is GateStatus.NEEDS_CLARIFICATION

    decision_time = NOW
    for field_key, claim_ids in sorted(claims_by_field.items()):
        if field_key == "length":
            action = DecisionAction.MERGE_EQUIVALENT
            selected_claim_id = None
        else:
            action = DecisionAction.SELECT_CLAIM
            selected_claim_id = claim_ids[0]
        decision_time += timedelta(seconds=1)
        specifications.record_decision(
            SpecFieldDecision(
                decision_id=f"decision-{field_key}",
                spec_id=spec_id,
                field_key=field_key,
                action=action,
                actor_id="reviewer-1",
                reason="verified against buyer RFQ evidence",
                decided_at=decision_time,
                input_claim_ids=tuple(claim_ids),
                selected_claim_id=selected_claim_id,
            )
        )

    frozen = specifications.freeze_for_supplier_rfq(
        spec_id,
        actor_id="approver",
        frozen_at=decision_time + timedelta(seconds=1),
    )
    frozen_fields = {field.field_key: field for field in frozen.fields}
    assert frozen.state is SpecState.FROZEN_FOR_SUPPLIER_RFQ
    assert frozen.readiness.status is GateStatus.READY
    assert frozen_fields["length"].canonical_value == {"amount": "25.4", "unit": "mm"}
    assert frozen_fields["outer_diameter"].canonical_value == {
        "amount": "325",
        "unit": "mm",
    }
    assert any(conflict.state == "resolved" for conflict in frozen.conflicts)
    assert frozen.fingerprint.startswith("sha256:")
    assert specifications.get_as_of(spec_id, NOW).spec_version_id == initial.spec_version_id
    assert not hasattr(intake, "send") and not hasattr(specifications, "send")
