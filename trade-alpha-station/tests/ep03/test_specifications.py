from __future__ import annotations

# ruff: noqa: E501, I001

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from trade_alpha.contracts.common import VersionRef
from trade_alpha.contracts.common import EvidenceLocator
from trade_alpha.execution.adapters.knowledge_claims import KnowledgeClaimAdapter
from trade_alpha.execution.rfq.models import RfqFieldCandidate
from trade_alpha.execution.specifications import (
    DecisionAction,
    FieldDefinition,
    FieldState,
    GateStatus,
    ProductFamilyProfile,
    SpecFieldDecision,
    SpecificationService,
    SpecState,
    ValueKind,
    load_profile,
)
from trade_alpha.execution.specifications.models import SpecFieldProjection
from trade_alpha.execution.specifications.repository import InMemoryProductSpecRepository
from trade_alpha.knowledge import Claim, KnowledgeStore, ReviewStatus

NOW = datetime(2026, 8, 26, tzinfo=UTC)


def profile(*, tolerance: str = "0") -> ProductFamilyProfile:
    return ProductFamilyProfile(
        profile_id="FE-DRY-01",
        profile_version="1.0.0",
        hard_exclusions=("toxic_dust", "combustible_dust", "atex", "medical", "pressure"),
        field_definitions=(
            FieldDefinition(
                key="length",
                value_kind=ValueKind.QUANTITY,
                canonical_unit="mm",
                equivalence_tolerance=Decimal(tolerance),
                conflict_policy="tolerance" if tolerance != "0" else "strict",
            ),
            FieldDefinition(
                key="dust_toxicity",
                value_kind=ValueKind.BOOLEAN,
                criticality="safety_scope",
                required_at=("supplier_rfq",),
            ),
            FieldDefinition(
                key="dust_combustibility",
                value_kind=ValueKind.BOOLEAN,
                criticality="safety_scope",
                required_at=("supplier_rfq",),
            ),
            FieldDefinition(
                key="atex_required",
                value_kind=ValueKind.BOOLEAN,
                criticality="safety_scope",
                required_at=("supplier_rfq",),
            ),
            FieldDefinition(
                key="material",
                value_kind=ValueKind.TEXT,
                requirement_mode="supplier_proposable",
                required_at=("supplier_rfq",),
            ),
        ),
        normalizer_versions=(VersionRef(name="pint", version="0.25.3"),),
    )


def claim(
    claim_id: str,
    field: str,
    value: object,
    *,
    at: datetime = NOW,
    status: ReviewStatus = ReviewStatus.ACCEPTED,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        subject_ref="product-spec:spec-1",
        predicate=f"spec.{field}",
        object_value={"raw": value, "typed": value, "canonical": None},
        source_id="fixture",
        artifact_id=f"artifact-{claim_id}",
        evidence_block_ids=(f"block-{claim_id}",),
        observed_at=at,
        confidence=0.9,
        status=status,
        decision_basis="rule",
        system_from=at,
    )


def service(
    *items: Claim, profile_value: ProductFamilyProfile | None = None
) -> tuple[SpecificationService, KnowledgeStore]:
    store = KnowledgeStore()
    for item in items:
        store.add_claim(item)
    value = profile_value or profile()
    return SpecificationService(store, (value,)), store


def make_spec(service_value: SpecificationService, at: datetime = NOW):
    return service_value.create_spec(
        "spec-1", VersionRef(name="FE-DRY-01", version="1.0.0"), created_at=at
    )


def test_ep03_ac_001_fields_keep_claim_document_block_and_artifact_lineage() -> None:
    svc, _ = service(claim("c1", "length", "25.4 mm"))
    version = make_spec(svc)
    field = next(item for item in version.fields if item.field_key == "length")
    assert field.source_claim_ids == ("c1",)
    assert field.artifact_ids == ("artifact-c1",)
    assert field.evidence_block_ids == ("block-c1",)


def test_ep03_ac_002_equivalent_units_group_without_merging_claims() -> None:
    svc, _ = service(claim("mm", "length", "25.4 mm"), claim("in", "length", "1 inch"))
    version = make_spec(svc)
    field = next(item for item in version.fields if item.field_key == "length")
    assert field.state is FieldState.CANDIDATE
    assert field.source_claim_ids == ("mm", "in")
    assert not version.conflicts


def test_ep03_ac_003_conflicting_dimensions_are_retained() -> None:
    svc, _ = service(claim("a", "length", "325 mm"), claim("b", "length", "330 mm"))
    version = make_spec(svc)
    assert (
        next(item for item in version.fields if item.field_key == "length").state
        is FieldState.CONFLICT
    )
    assert version.conflicts[0].claim_ids == ("a", "b")


def test_ep03_ac_004_unknown_safety_scope_needs_clarification() -> None:
    svc, _ = service(claim("c1", "length", "25.4 mm"))
    assert make_spec(svc).readiness.status is GateStatus.NEEDS_CLARIFICATION
    unknown, _ = service(
        claim("t", "dust_toxicity", "unknown"),
        claim("c", "dust_combustibility", False),
        claim("a", "atex_required", False),
    )
    assert make_spec(unknown).readiness.status is GateStatus.NEEDS_CLARIFICATION


def test_ep03_ac_005_hard_exclusion_blocks() -> None:
    svc, _ = service(
        claim("safe1", "dust_toxicity", False),
        claim("safe2", "dust_combustibility", True),
        claim("safe3", "atex_required", False),
    )
    assert make_spec(svc).state is SpecState.BLOCKED


def test_ep03_ac_006_decision_is_append_only_and_creates_new_version() -> None:
    svc, _ = service(claim("a", "length", "325 mm"), claim("b", "length", "330 mm"))
    first = make_spec(svc)
    second = svc.record_decision(
        SpecFieldDecision(
            decision_id="d1",
            spec_id="spec-1",
            field_key="length",
            action=DecisionAction.SELECT_CLAIM,
            actor_id="reviewer",
            reason="drawing verified",
            decided_at=NOW + timedelta(seconds=1),
            input_claim_ids=("a", "b"),
            selected_claim_id="a",
        )
    )
    assert first.spec_version_id != second.spec_version_id
    assert first.state is SpecState.REVIEW_REQUIRED
    assert (
        next(item for item in second.fields if item.field_key == "length").state
        is FieldState.CONFIRMED
    )


def test_ep03_ac_007_idempotent_projection_and_profile_change() -> None:
    svc, _ = service(claim("c1", "length", "25.4 mm"))
    first = make_spec(svc)
    assert svc.reproject("spec-1", as_of=NOW).spec_version_id == first.spec_version_id
    changed = profile(tolerance="1")
    changed = changed.model_copy(update={"profile_version": "1.0.1"})
    svc.register_profile(changed)
    assert (
        svc.reproject("spec-1", as_of=NOW, profile_ref=changed.ref).spec_version_id
        != first.spec_version_id
    )


def test_ep03_ac_008_to_010_freeze_is_authorized_internal_version_only() -> None:
    claims = [
        claim("l", "length", "25.4 mm"),
        claim("t", "dust_toxicity", False),
        claim("c", "dust_combustibility", False),
        claim("a", "atex_required", False),
    ]
    svc, _ = service(*claims)
    make_spec(svc)
    for offset, field_key, claim_id in (
        (1, "length", "l"),
        (2, "dust_toxicity", "t"),
        (3, "dust_combustibility", "c"),
        (4, "atex_required", "a"),
    ):
        svc.record_decision(
            SpecFieldDecision(
                decision_id=f"select-{claim_id}",
                spec_id="spec-1",
                field_key=field_key,
                action=DecisionAction.SELECT_CLAIM,
                actor_id="reviewer",
                reason="human confirmed",
                decided_at=NOW + timedelta(seconds=offset),
                input_claim_ids=(claim_id,),
                selected_claim_id=claim_id,
            )
        )
    svc.record_decision(
        SpecFieldDecision(
            decision_id="open",
            spec_id="spec-1",
            field_key="material",
            action=DecisionAction.OPEN_TO_SUPPLIER_PROPOSAL,
            actor_id="reviewer",
            reason="supplier may propose",
            decided_at=NOW + timedelta(seconds=5),
        )
    )
    frozen = svc.freeze_for_supplier_rfq(
        "spec-1", actor_id="approver", frozen_at=NOW + timedelta(seconds=6)
    )
    assert frozen.state is SpecState.FROZEN_FOR_SUPPLIER_RFQ
    assert frozen.profile_ref.name == "FE-DRY-01" and frozen.claim_ids and frozen.decision_ids
    assert frozen.normalizer_versions and frozen.fingerprint.startswith("sha256:")
    with pytest.raises(PermissionError, match="authorized"):
        svc.freeze_for_supplier_rfq("spec-1", actor_id="reviewer", frozen_at=NOW + timedelta(seconds=7))


def test_ep03_ac_009_as_of_and_field_diff() -> None:
    svc, _ = service(claim("a", "length", "325 mm"), claim("b", "length", "330 mm"))
    first = make_spec(svc)
    second = svc.record_decision(
        SpecFieldDecision(
            decision_id="d1",
            spec_id="spec-1",
            field_key="length",
            action=DecisionAction.SELECT_CLAIM,
            actor_id="reviewer",
                reason="drawing verified",
                decided_at=NOW + timedelta(seconds=1),
                input_claim_ids=("a", "b"),
                selected_claim_id="a",
        )
    )
    assert svc.get_as_of("spec-1", NOW).spec_version_id == first.spec_version_id
    assert "length" in svc.diff(first.spec_version_id, second.spec_version_id).state_changed_fields


def test_claim_accepted_is_not_field_confirmed() -> None:
    svc, _ = service(claim("c1", "length", "25.4 mm", status=ReviewStatus.ACCEPTED))
    field = next(item for item in make_spec(svc).fields if item.field_key == "length")
    assert field.state is FieldState.CANDIDATE


def test_decision_invariants_and_field_claim_membership_are_enforced() -> None:
    with pytest.raises(ValueError, match="requires selected"):
        SpecFieldDecision(
            decision_id="bad",
            spec_id="spec-1",
            field_key="length",
            action=DecisionAction.SELECT_CLAIM,
            actor_id="reviewer",
            reason="missing selection",
            decided_at=NOW,
        )
    svc, _ = service(claim("length", "length", "25.4 mm"))
    make_spec(svc)
    with pytest.raises(ValueError, match="does not belong"):
        svc.record_decision(
            SpecFieldDecision(
                decision_id="foreign",
                spec_id="spec-1",
                field_key="length",
                action=DecisionAction.SELECT_CLAIM,
                actor_id="reviewer",
                reason="invalid claim",
                decided_at=NOW + timedelta(seconds=1),
                selected_claim_id="other-field",
            )
        )


def test_normalizer_ignores_untrusted_canonical_and_decision_actions_are_explicit() -> None:
    item = claim("c1", "length", "25.4 mm").model_copy(
        update={"object_value": {"raw": "25.4 mm", "typed": "25.4 mm", "canonical": "999 mm"}}
    )
    svc, _ = service(item)
    first = make_spec(svc)
    field = next(value for value in first.fields if value.field_key == "length")
    assert field.canonical_value == {"amount": "25.4", "unit": "mm"}
    rejected = svc.record_decision(
        SpecFieldDecision(
            decision_id="reject", spec_id="spec-1", field_key="length", action=DecisionAction.REJECT,
            actor_id="reviewer", reason="bad extraction", decided_at=NOW + timedelta(seconds=1), input_claim_ids=("c1",),
        )
    )
    assert next(value for value in rejected.fields if value.field_key == "length").state is FieldState.REJECTED


def test_resolved_conflict_is_retained_and_candidates_cannot_freeze() -> None:
    svc, _ = service(claim("a", "length", "325 mm"), claim("b", "length", "330 mm"))
    first = make_spec(svc)
    resolved = svc.record_decision(
        SpecFieldDecision(
            decision_id="select", spec_id="spec-1", field_key="length", action=DecisionAction.SELECT_CLAIM,
            actor_id="reviewer", reason="drawing", decided_at=NOW + timedelta(seconds=1), input_claim_ids=("a", "b"), selected_claim_id="a",
        )
    )
    assert first.conflicts[0].state == "open"
    assert resolved.conflicts[0].state == "resolved"
    assert not resolved.unresolved_conflict_ids
    with pytest.raises(ValueError, match="ready"):
        svc.freeze_for_supplier_rfq("spec-1", actor_id="approver", frozen_at=NOW + timedelta(seconds=2))


def test_knowledge_adapter_keeps_locator_lineage_and_refuses_rejected_schema() -> None:
    candidate = RfqFieldCandidate(
        candidate_id="candidate-1", rfq_id="rfq-1", field_name="length", value="25.4 mm",
        artifact_id="artifact-1", block_id="block-1", locator=EvidenceLocator(artifact_id="artifact-1", block_ids=("block-1",)),
        extraction_run_id="run-1", extractor_type="rule", extractor_version="1.0.0", extraction_fingerprint="sha256:x", confidence=0.9,
    )
    store = KnowledgeStore()
    mapped = KnowledgeClaimAdapter(store).add_candidate("spec-1", candidate, observed_at=NOW)
    assert mapped.status is ReviewStatus.PENDING and mapped.evidence_block_ids == ("block-1",)
    with pytest.raises(ValueError, match="rejected_schema"):
        KnowledgeClaimAdapter(store).add_candidate("spec-1", candidate.model_copy(update={"status": "rejected_schema"}), observed_at=NOW)


def test_loaded_yaml_identity_and_quantity_alternatives() -> None:
    config = Path(__file__).parents[2] / "configs/execution/product-families/FE-DRY-01-v1.yaml"
    loaded = load_profile(config)
    svc, _ = service(profile_value=loaded)
    def readiness(*confirmed: str):
        fields = tuple(
            SpecFieldProjection(
                field_key=definition.key,
                state=FieldState.CONFIRMED if definition.key in confirmed else FieldState.CANDIDATE,
                normalizer_version=definition.normalizer,
            )
            for definition in loaded.field_definitions
        )
        return svc._readiness(loaded, fields, ())
    base = ("dust_toxicity", "dust_combustibility", "atex_required", "filter_material", "performance_requirement", "packaging_requirement")
    assert "product_identity" not in readiness(*base, "oem_model_reference", "tier_quantities").missing_fields
    assert "product_identity" not in readiness(*base, "outer_diameter", "inner_diameter", "length", "tier_quantities").missing_fields
    assert "order_quantity" not in readiness(*base, "oem_model_reference", "tier_quantities").missing_fields


def test_decisions_are_replayed_in_chronological_order_not_insertion_order() -> None:
    repo = InMemoryProductSpecRepository()
    later = SpecFieldDecision(decision_id="z", spec_id="spec-1", field_key="material", action=DecisionAction.OPEN_TO_SUPPLIER_PROPOSAL, actor_id="reviewer", reason="later", decided_at=NOW + timedelta(seconds=2))
    earlier = later.model_copy(update={"decision_id": "a", "decided_at": NOW + timedelta(seconds=1), "reason": "earlier"})
    repo.save_decision(later)
    repo.save_decision(earlier)
    assert [item.decision_id for item in repo.decisions_for("spec-1", NOW + timedelta(seconds=3))] == ["a", "z"]


def test_loaded_scope_gate_requires_confirmed_allowed_scope_and_blocks_exclusions() -> None:
    config = Path(__file__).parents[2] / "configs/execution/product-families/FE-DRY-01-v1.yaml"
    loaded = load_profile(config)
    svc, _ = service(profile_value=loaded)

    def evaluate(scope_state: FieldState, scope_value: str | None) -> object:
        ready_keys = {
            "oem_model_reference",
            "tier_quantities",
            "dust_toxicity",
            "dust_combustibility",
            "atex_required",
        }
        fields = []
        for definition in loaded.field_definitions:
            state = FieldState.CANDIDATE
            canonical = None
            if definition.key == "application_scope":
                state, canonical = scope_state, scope_value
            elif definition.key in ready_keys:
                state = FieldState.CONFIRMED
                canonical = False if definition.key.startswith("dust_") or definition.key == "atex_required" else "confirmed"
            elif definition.requirement_mode == "supplier_proposable":
                state = FieldState.OPEN_TO_SUPPLIER_PROPOSAL
            fields.append(SpecFieldProjection(field_key=definition.key, state=state, canonical_value=canonical, normalizer_version=definition.normalizer))
        return svc._readiness(loaded, tuple(fields), ())

    assert evaluate(FieldState.UNKNOWN, None).status is GateStatus.NEEDS_CLARIFICATION
    assert evaluate(FieldState.CONFIRMED, "general_industrial_dry_dust").status is GateStatus.READY
    for excluded in ("medical", "pressure"):
        assert evaluate(FieldState.CONFIRMED, excluded).status is GateStatus.BLOCKED
    assert evaluate(FieldState.CONFIRMED, "unrelated automotive dust").status is GateStatus.BLOCKED
    assert evaluate(FieldState.CONFIRMED, "not medical").status is GateStatus.BLOCKED
    assert "hard_exclusion:application_scope" not in evaluate(FieldState.CONFIRMED, "not medical").risk_flags


def test_profile_upgrade_is_sticky_for_later_decisions_and_freeze() -> None:
    base = ProductFamilyProfile(
        profile_id="FE-DRY-01",
        profile_version="1.0.0",
        field_definitions=(FieldDefinition(key="length", value_kind=ValueKind.QUANTITY, canonical_unit="mm"),),
    )
    svc, _ = service(claim("length", "length", "25.4 mm"), profile_value=base)
    first = make_spec(svc)
    upgraded_profile = base.model_copy(update={"profile_version": "1.1.0"})
    svc.register_profile(upgraded_profile)
    upgraded = svc.reproject("spec-1", as_of=NOW + timedelta(seconds=1), profile_ref=upgraded_profile.ref)
    decided = svc.record_decision(
        SpecFieldDecision(
            decision_id="select-length", spec_id="spec-1", field_key="length", action=DecisionAction.SELECT_CLAIM,
            actor_id="reviewer", reason="confirmed", decided_at=NOW + timedelta(seconds=2), input_claim_ids=("length",), selected_claim_id="length",
        )
    )
    frozen = svc.freeze_for_supplier_rfq("spec-1", actor_id="approver", frozen_at=NOW + timedelta(seconds=3))
    assert first.profile_ref.version == "1.0.0"
    assert upgraded.profile_ref.version == decided.profile_ref.version == frozen.profile_ref.version == "1.1.0"
    assert svc.get_as_of("spec-1", NOW).profile_ref.version == "1.0.0"
