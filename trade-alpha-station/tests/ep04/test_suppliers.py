from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from trade_alpha.contracts.common import EvidenceLocator, VersionRef
from trade_alpha.execution.adapters.format_identifiers import FormatOnlyIdentifierAdapter
from trade_alpha.execution.contracts import ProvenanceRef
from trade_alpha.execution.specifications.models import (
    GateStatus,
    ProductSpecVersion,
    ReadinessResult,
    SpecState,
)
from trade_alpha.execution.suppliers import (
    AuthenticityStatus,
    BusinessIdentifierClaim,
    CapabilityPredicate,
    CapabilitySourceType,
    CapabilityType,
    ClaimReviewStatus,
    CounterpartyScreeningRun,
    DocumentReviewStatus,
    ExpiryStatus,
    IdentifierFormatStatus,
    IdentityDecisionAction,
    InMemorySupplierQualificationRepository,
    InMemorySupplierRepository,
    MachineDisposition,
    QualificationDecisionAction,
    QualificationDisposition,
    QualificationPolicyVersion,
    RegistryStatus,
    RequirementStatus,
    ScreeningCandidateMatch,
    ScreeningDisposition,
    ScreeningRunStatus,
    SiteType,
    SupplierCapabilityClaim,
    SupplierCapabilityService,
    SupplierDocumentClaim,
    SupplierIdentityDecision,
    SupplierIdentityService,
    SupplierLifecycleStatus,
    SupplierProfile,
    SupplierProfileVersion,
    SupplierQualificationDecision,
    SupplierQualificationService,
    SupplierSiteVersion,
)

NOW = datetime(2026, 8, 27, tzinfo=UTC)


def provenance() -> ProvenanceRef:
    return ProvenanceRef(
        source="fixture",
        evidence=EvidenceLocator(artifact_id="supplier-source", block_ids=("block-1",)),
        observed_at=NOW,
        source_version=VersionRef(name="fixture", version="1"),
    )


def profile(
    *, supplier_id: str = "supplier-a", site_type: SiteType = SiteType.FACTORY
) -> SupplierProfileVersion:
    site = SupplierSiteVersion(
        site_id=f"{supplier_id}-site",
        site_version_id=f"{supplier_id}-site-v1",
        supplier_id=supplier_id,
        site_type=site_type,
        address_raw="No. 1 Industrial Road",
        country_or_region="CN",
        provenance_refs=(provenance(),),
        valid_from=NOW,
        content_fingerprint="sha256:site",
    )
    return SupplierProfileVersion(
        supplier_profile_version_id=f"{supplier_id}-v1",
        supplier_id=supplier_id,
        version=1,
        legal_name_raw="Alpha Filter Factory",
        legal_name_normalized="alpha filter factory",
        country_or_region="CN",
        sites=(site,),
        provenance_refs=(provenance(),),
        valid_from=NOW,
        system_time=NOW,
        content_fingerprint=f"sha256:{supplier_id}",
    )


def frozen_spec() -> ProductSpecVersion:
    return ProductSpecVersion(
        spec_version_id="spec-v1",
        spec_id="spec-1",
        sequence=1,
        profile_ref=VersionRef(name="FE-DRY-01", version="1.0.0"),
        fields=(),
        readiness=ReadinessResult(status=GateStatus.READY, profile_version="1.0.0"),
        state=SpecState.FROZEN_FOR_SUPPLIER_RFQ,
        fingerprint="sha256:spec",
        created_at=NOW,
        created_by="approver",
    )


def policy() -> QualificationPolicyVersion:
    return QualificationPolicyVersion(
        policy_id="policy",
        policy_version="1.0.0",
        product_family_id="FE-DRY-01",
        required_capability_predicates=(
            CapabilityPredicate(
                requirement_id="family-manufacturing",
                product_family_id="FE-DRY-01",
                capability_types=(CapabilityType.MANUFACTURING,),
            ),
        ),
        hard_blocks=("atex", "medical", "combustible_dust", "toxic_dust"),
        allowed_human_roles=("approver",),
        config_fingerprint="sha256:policy",
        effective_from=NOW - timedelta(days=1),
    )


def populated_services() -> tuple[
    InMemorySupplierRepository,
    InMemorySupplierQualificationRepository,
    SupplierQualificationService,
]:
    suppliers = InMemorySupplierRepository()
    qualifications = InMemorySupplierQualificationRepository()
    identity = SupplierIdentityService(suppliers)
    version = profile()
    identity.capture_profile(
        SupplierProfile(supplier_id="supplier-a", created_at=NOW),
        version,
        idempotency_key="retry-key",
    )
    suppliers.save_capability(
        SupplierCapabilityClaim(
            capability_claim_id="manufacturing",
            supplier_id="supplier-a",
            site_id="supplier-a-site",
            product_family_id="FE-DRY-01",
            capability_type=CapabilityType.MANUFACTURING,
            claim_value="general industrial dry dust filter cartridges",
            source_type=CapabilitySourceType.SUPPLIER_ASSERTED,
            provenance_refs=(provenance(),),
            confidence=0.9,
            valid_from=NOW,
            recorded_at=NOW,
            review_status=ClaimReviewStatus.REVIEWED,
        )
    )
    return suppliers, qualifications, SupplierQualificationService(suppliers, qualifications)


def test_ep04_ac_001_capture_is_idempotent_and_identifier_states_are_separate() -> None:
    suppliers = InMemorySupplierRepository()
    service = SupplierIdentityService(suppliers)
    version = profile()
    root = SupplierProfile(supplier_id="supplier-a", created_at=NOW)
    captured = service.capture_profile(root, version, idempotency_key="same")
    assert captured.supplier_profile_version_id == version.supplier_profile_version_id
    assert service.capture_profile(root, version, idempotency_key="same") == captured
    assert len(suppliers.profile_versions_for("supplier-a")) == 1
    with pytest.raises(ValueError, match="different supplier input"):
        service.capture_profile(
            root,
            version.model_copy(update={"legal_name_raw": "Different Factory"}),
            idempotency_key="same",
        )
    fake_fingerprint = "sha256:caller-controlled"
    first_normalized = service.capture_profile(
        root,
        version.model_copy(update={"content_fingerprint": fake_fingerprint}),
    )
    second_root = SupplierProfile(supplier_id="supplier-b", created_at=NOW)
    second_normalized = service.capture_profile(
        second_root,
        profile(supplier_id="supplier-b").model_copy(
            update={
                "legal_name_raw": "Different Factory",
                "legal_name_normalized": "different factory",
                "content_fingerprint": fake_fingerprint,
            }
        ),
    )
    assert first_normalized.content_fingerprint != second_normalized.content_fingerprint
    identifier = BusinessIdentifierClaim(
        identifier_claim_id="identifier",
        supplier_id="supplier-a",
        identifier_type="fixture-id",
        country_or_region="CN",
        raw_value="AB-123",
        compact_value="AB123",
        provenance_ref=provenance(),
    )
    checked = FormatOnlyIdentifierAdapter(
        {("CN", "fixture-id"): lambda value: value == "AB123"}
    ).validate(identifier)
    assert checked.format_status is IdentifierFormatStatus.VALID_FORMAT
    assert checked.registry_status is RegistryStatus.NOT_CHECKED


def test_ep04_pit_future_recorded_capability_and_document_do_not_leak_into_past() -> None:
    suppliers, _, service = populated_services()
    future = NOW + timedelta(days=1)
    suppliers.save_capability(
        SupplierCapabilityClaim(
            capability_claim_id="future-capability",
            supplier_id="supplier-a",
            site_id="supplier-a-site",
            product_family_id="FE-DRY-01",
            capability_type=CapabilityType.MANUFACTURING,
            claim_value="additional documented manufacturing capability",
            source_type=CapabilitySourceType.DOCUMENTED,
            provenance_refs=(provenance(),),
            confidence=0.9,
            valid_from=NOW,
            recorded_at=future,
            review_status=ClaimReviewStatus.REVIEWED,
        )
    )
    suppliers.save_document(
        SupplierDocumentClaim(
            document_claim_id="future-quality-document",
            supplier_id="supplier-a",
            document_type="quality_system",
            subject_supplier_id="supplier-a",
            artifact_id="future-artifact",
            issued_at=NOW,
            recorded_at=future,
            human_review_status=DocumentReviewStatus.REVIEWED,
            authenticity_status=AuthenticityStatus.VERIFIED_BY_HUMAN,
        )
    )
    quality_policy = policy().model_copy(update={"required_evidence_types": ("quality_system",)})
    assert not any(
        item.capability_claim_id == "future-capability"
        for item in suppliers.capabilities_for("supplier-a", NOW)
    )
    assert not any(
        item.document_claim_id == "future-quality-document"
        for item in suppliers.documents_for("supplier-a", NOW)
    )
    past = service.assess("supplier-a-v1", frozen_spec(), quality_policy, as_of=NOW)
    assert (
        next(
            item
            for item in past.requirement_results
            if item.requirement_id == "evidence:quality_system"
        ).status
        is RequirementStatus.WARNING
    )
    future_assessment = service.assess(
        "supplier-a-v1", frozen_spec(), quality_policy, as_of=NOW + timedelta(days=2)
    )
    assert (
        next(
            item
            for item in future_assessment.requirement_results
            if item.requirement_id == "evidence:quality_system"
        ).status
        is RequirementStatus.SATISFIED
    )


def test_ep04_profile_and_unverified_documents_are_fail_closed_at_as_of() -> None:
    suppliers, _, service = populated_services()
    future_profile = profile().model_copy(
        update={
            "supplier_profile_version_id": "supplier-a-future",
            "version": 2,
            "valid_from": NOW + timedelta(days=1),
            "system_time": NOW + timedelta(days=1),
        }
    )
    suppliers.save_profile_version(future_profile)
    with pytest.raises(ValueError, match="not valid and known"):
        service.assess(
            future_profile.supplier_profile_version_id,
            frozen_spec(),
            policy(),
            as_of=NOW,
        )

    suppliers.save_document(
        SupplierDocumentClaim(
            document_claim_id="pending-quality-document",
            supplier_id="supplier-a",
            document_type="quality_system",
            subject_supplier_id="supplier-a",
            artifact_id="pending-artifact",
            issued_at=NOW,
            recorded_at=NOW,
        )
    )
    assessment = service.assess(
        "supplier-a-v1",
        frozen_spec(),
        policy().model_copy(update={"required_evidence_types": ("quality_system",)}),
        as_of=NOW,
    )
    evidence = next(
        item
        for item in assessment.requirement_results
        if item.requirement_id == "evidence:quality_system"
    )
    assert evidence.status is RequirementStatus.WARNING


def test_ep04_ac_002_duplicate_candidates_do_not_merge_without_human_decision() -> None:
    suppliers = InMemorySupplierRepository()
    service = SupplierIdentityService(suppliers)
    first = profile()
    second = profile(supplier_id="supplier-b").model_copy(
        update={"legal_name_normalized": "alpha factory filter"}
    )
    service.capture_profile(SupplierProfile(supplier_id="supplier-a", created_at=NOW), first)
    service.capture_profile(SupplierProfile(supplier_id="supplier-b", created_at=NOW), second)
    candidates = service.propose_duplicate_links("supplier-a", as_of=NOW, minimum_score=50)
    assert (
        candidates
        and suppliers.get_profile("supplier-a").lifecycle_status
        is SupplierLifecycleStatus.CANDIDATE
    )


def test_ep04_ac_007_failed_screening_can_never_be_no_candidate() -> None:
    with pytest.raises(ValueError, match="cannot be no_candidate"):
        CounterpartyScreeningRun(
            screening_run_id="failed",
            supplier_profile_version_id="supplier-a-v1",
            provider="fixture",
            dataset_scope="fixture",
            dataset_version="1",
            published_at=NOW,
            algorithm="fixture",
            algorithm_version="1",
            request_fingerprint="sha256:request",
            run_status=ScreeningRunStatus.PROVIDER_ERROR,
            disposition=ScreeningDisposition.NO_CANDIDATE,
            completed_at=NOW,
        )


def test_ep04_ac_004_to_006_office_is_not_factory_and_expired_document_is_not_current() -> None:
    suppliers = InMemorySupplierRepository()
    identity = SupplierIdentityService(suppliers)
    office_version = profile(site_type=SiteType.OFFICE)
    identity.capture_profile(
        SupplierProfile(supplier_id="supplier-a", created_at=NOW), office_version
    )
    suppliers.save_capability(
        SupplierCapabilityClaim(
            capability_claim_id="manufacturing-office",
            supplier_id="supplier-a",
            site_id="supplier-a-site",
            product_family_id="FE-DRY-01",
            capability_type=CapabilityType.MANUFACTURING,
            claim_value="filter cartridges",
            source_type=CapabilitySourceType.DOCUMENTED,
            provenance_refs=(provenance(),),
            confidence=0.9,
            valid_from=NOW,
            recorded_at=NOW,
            review_status=ClaimReviewStatus.REVIEWED,
        )
    )
    suppliers.save_document(
        SupplierDocumentClaim(
            document_claim_id="expired-quality-document",
            supplier_id="supplier-a",
            document_type="quality_system",
            subject_supplier_id="supplier-a",
            artifact_id="expired-artifact",
            issued_at=NOW - timedelta(days=365),
            valid_until=NOW - timedelta(days=1),
            recorded_at=NOW,
            expiry_status=ExpiryStatus.EXPIRED,
        )
    )
    quality_policy = policy().model_copy(update={"required_evidence_types": ("quality_system",)})
    assessment = SupplierQualificationService(suppliers).assess(
        "supplier-a-v1", frozen_spec(), quality_policy, as_of=NOW
    )
    by_id = {item.requirement_id: item for item in assessment.requirement_results}
    assert by_id["production_site"].status is RequirementStatus.MISSING
    assert by_id["evidence:quality_system"].status is RequirementStatus.WARNING


def test_ep04_ac_008_candidate_screening_requires_review_not_clearance() -> None:
    suppliers, _, service = populated_services()
    suppliers.save_screening(
        CounterpartyScreeningRun(
            screening_run_id="candidate-screening",
            supplier_profile_version_id="supplier-a-v1",
            provider="fixture",
            dataset_scope="fixture",
            dataset_version="1",
            published_at=NOW,
            algorithm="fixture",
            algorithm_version="1",
            request_fingerprint="sha256:request",
            candidate_matches=(
                ScreeningCandidateMatch(
                    source_entity_id="listed-entity", score=95, matched_fields=("legal_name",)
                ),
            ),
            run_status=ScreeningRunStatus.COMPLETED,
            disposition=ScreeningDisposition.CANDIDATE_FOUND,
            completed_at=NOW,
        )
    )
    assessment = service.assess("supplier-a-v1", frozen_spec(), policy(), as_of=NOW)
    screening = next(
        item for item in assessment.requirement_results if item.requirement_id == "screening"
    )
    assert screening.status is RequirementStatus.WARNING
    assert screening.reason_code == "SCREENING_CANDIDATE_REVIEW_REQUIRED"


def test_ep04_ac_008_to_011_assessment_decision_snapshot_and_as_of() -> None:
    suppliers, _, service = populated_services()
    suppliers.save_screening(
        CounterpartyScreeningRun(
            screening_run_id="screening-1",
            supplier_profile_version_id="supplier-a-v1",
            provider="fixture",
            dataset_scope="fixture",
            dataset_version="1",
            published_at=NOW,
            algorithm="fixture",
            algorithm_version="1",
            request_fingerprint="sha256:request",
            run_status=ScreeningRunStatus.COMPLETED,
            disposition=ScreeningDisposition.NO_CANDIDATE,
            completed_at=NOW,
        )
    )
    assessment = service.assess("supplier-a-v1", frozen_spec(), policy(), as_of=NOW)
    assert assessment.machine_disposition is MachineDisposition.REVIEW_REQUIRED
    attacker = SupplierQualificationDecision(
        decision_id="attacker",
        assessment_id=assessment.assessment_id,
        actor_id="attacker",
        actor_role="attacker",
        decision=QualificationDecisionAction.APPROVE_FOR_DRY_RUN,
        rationale="attempt privilege escalation",
        decided_at=NOW,
    )
    with pytest.raises(PermissionError, match="not allowed"):
        service.decide(attacker, policy=policy())
    swapped_policy = policy().model_copy(update={"allowed_human_roles": ("attacker",)})
    with pytest.raises(ValueError, match="configuration"):
        service.decide(attacker, policy=swapped_policy)
    decision = service.decide(
        SupplierQualificationDecision(
            decision_id="approve",
            assessment_id=assessment.assessment_id,
            actor_id="person",
            actor_role="approver",
            decision=QualificationDecisionAction.APPROVE_FOR_DRY_RUN,
            rationale="fixture reviewed",
            decided_at=NOW,
            expires_at=NOW + timedelta(days=7),
        ),
        policy=policy(),
    )
    snapshot = service.freeze_snapshot(assessment.assessment_id, policy(), as_of=NOW)
    assert decision.decision_id == snapshot.effective_decision_id
    assert snapshot.disposition is QualificationDisposition.DRY_RUN_ELIGIBLE
    assert service.get_as_of("supplier-a", "spec-v1", as_of=NOW) == snapshot
    with pytest.raises(ValueError, match="frozen"):
        service.assess(
            "supplier-a-v1",
            frozen_spec().model_copy(update={"state": SpecState.CONFIRMED}),
            policy(),
            as_of=NOW,
        )


def test_ep04_ac_010_hard_scope_conflict_cannot_be_scored_away() -> None:
    suppliers, _, service = populated_services()
    suppliers.save_capability(
        SupplierCapabilityClaim(
            capability_claim_id="excluded",
            supplier_id="supplier-a",
            site_id="supplier-a-site",
            product_family_id="FE-DRY-01",
            capability_type=CapabilityType.MANUFACTURING,
            claim_value="ATEX certified application only",
            source_type=CapabilitySourceType.SUPPLIER_ASSERTED,
            provenance_refs=(provenance(),),
            confidence=0.8,
            valid_from=NOW,
            recorded_at=NOW,
            review_status=ClaimReviewStatus.REVIEWED,
        )
    )
    assessment = service.assess("supplier-a-v1", frozen_spec(), policy(), as_of=NOW)
    assert any(item.status is RequirementStatus.BLOCKED for item in assessment.requirement_results)
    assert assessment.machine_disposition is MachineDisposition.BLOCKED


@pytest.mark.parametrize(
    ("valid_from", "valid_until"),
    ((NOW + timedelta(days=1), None), (NOW - timedelta(days=2), NOW - timedelta(days=1))),
)
def test_ep04_pit_future_or_expired_factory_does_not_satisfy_production_site(
    valid_from: datetime, valid_until: datetime | None
) -> None:
    suppliers = InMemorySupplierRepository()
    identity = SupplierIdentityService(suppliers)
    base = profile()
    site = base.sites[0].model_copy(update={"valid_from": valid_from, "valid_until": valid_until})
    version = base.model_copy(update={"sites": (site,)})
    identity.capture_profile(SupplierProfile(supplier_id="supplier-a", created_at=NOW), version)
    assessment = SupplierQualificationService(suppliers).assess(
        "supplier-a-v1", frozen_spec(), policy(), as_of=NOW
    )
    production = next(
        item for item in assessment.requirement_results if item.requirement_id == "production_site"
    )
    assert production.status is RequirementStatus.MISSING


def test_ep04_assessment_expiry_prevents_late_eligibility() -> None:
    suppliers, _, service = populated_services()
    suppliers.save_screening(
        CounterpartyScreeningRun(
            screening_run_id="screening-expiry",
            supplier_profile_version_id="supplier-a-v1",
            provider="fixture",
            dataset_scope="fixture",
            dataset_version="1",
            published_at=NOW,
            algorithm="fixture",
            algorithm_version="1",
            request_fingerprint="sha256:request",
            run_status=ScreeningRunStatus.COMPLETED,
            disposition=ScreeningDisposition.NO_CANDIDATE,
            completed_at=NOW,
        )
    )
    assessment = service.assess("supplier-a-v1", frozen_spec(), policy(), as_of=NOW)
    service.decide(
        SupplierQualificationDecision(
            decision_id="late-approval",
            assessment_id=assessment.assessment_id,
            actor_id="person",
            actor_role="approver",
            decision=QualificationDecisionAction.APPROVE_FOR_DRY_RUN,
            rationale="reviewed",
            decided_at=NOW,
        ),
        policy=policy(),
    )
    frozen = service.freeze_snapshot(
        assessment.assessment_id, policy(), as_of=NOW + timedelta(days=30)
    )
    assert frozen.disposition is QualificationDisposition.DRY_RUN_ELIGIBLE
    frozen = service.freeze_snapshot(
        assessment.assessment_id, policy(), as_of=NOW + timedelta(days=31)
    )
    assert frozen.disposition is QualificationDisposition.EXPIRED
    assert frozen.valid_until == NOW + timedelta(days=30)


def test_ep04_unassessed_screening_cannot_become_eligible_at_exact_as_of() -> None:
    _, _, service = populated_services()
    assessment = service.assess("supplier-a-v1", frozen_spec(), policy(), as_of=NOW)
    assert assessment.machine_disposition is MachineDisposition.EVIDENCE_INCOMPLETE
    service.decide(
        SupplierQualificationDecision(
            decision_id="unassessed-approval",
            assessment_id=assessment.assessment_id,
            actor_id="person",
            actor_role="approver",
            decision=QualificationDecisionAction.APPROVE_FOR_DRY_RUN,
            rationale="cannot overcome missing screening",
            decided_at=NOW,
        ),
        policy=policy(),
    )
    snapshot = service.freeze_snapshot(assessment.assessment_id, policy(), as_of=NOW)
    assert snapshot.disposition is QualificationDisposition.EVIDENCE_INCOMPLETE


def test_ep04_capability_requires_owned_site_and_human_review() -> None:
    suppliers, _, service = populated_services()
    capability = SupplierCapabilityClaim(
        capability_claim_id="wrong-site",
        supplier_id="supplier-a",
        site_id="not-a-profile-site",
        product_family_id="FE-DRY-01",
        capability_type=CapabilityType.MANUFACTURING,
        claim_value="filter cartridges",
        source_type=CapabilitySourceType.SUPPLIER_ASSERTED,
        provenance_refs=(provenance(),),
        confidence=0.9,
        valid_from=NOW,
        recorded_at=NOW,
    )
    with pytest.raises(ValueError, match="site_id"):
        SupplierCapabilityService(suppliers).record_claims((capability,))
    pending_only = InMemorySupplierRepository()
    identity = SupplierIdentityService(pending_only)
    identity.capture_profile(SupplierProfile(supplier_id="supplier-a", created_at=NOW), profile())
    pending_only.save_capability(
        capability.model_copy(
            update={"site_id": "supplier-a-site", "capability_claim_id": "pending"}
        )
    )
    assessment = SupplierQualificationService(pending_only).assess(
        "supplier-a-v1", frozen_spec(), policy(), as_of=NOW
    )
    assert (
        next(
            item
            for item in assessment.requirement_results
            if item.requirement_id == "family-manufacturing"
        ).status
        is RequirementStatus.MISSING
    )


def test_ep04_merge_requires_persisted_candidate_and_is_conflict_safe() -> None:
    suppliers = InMemorySupplierRepository()
    service = SupplierIdentityService(suppliers)
    first = profile()
    second = profile(supplier_id="supplier-b")
    service.capture_profile(SupplierProfile(supplier_id="supplier-a", created_at=NOW), first)
    service.capture_profile(SupplierProfile(supplier_id="supplier-b", created_at=NOW), second)
    invalid = SupplierIdentityDecision(
        decision_id="merge",
        action=IdentityDecisionAction.MERGE,
        actor_id="reviewer",
        source_supplier_id="supplier-a",
        target_supplier_id="supplier-b",
        candidate_id="missing",
        rationale="same factory",
        decided_at=NOW,
    )
    with pytest.raises(ValueError, match="matching generated"):
        service.record_identity_decision(invalid)
    assert suppliers.get_profile("supplier-a").lifecycle_status is SupplierLifecycleStatus.CANDIDATE
    candidate = service.propose_duplicate_links("supplier-a", as_of=NOW, minimum_score=50)[0]
    merge = invalid.model_copy(update={"candidate_id": candidate.candidate_id})
    assert service.record_identity_decision(merge) == merge
    with pytest.raises(ValueError, match="conflicts"):
        service.record_identity_decision(merge.model_copy(update={"rationale": "changed"}))


def test_ep04_ac_011_to_012_snapshot_is_immutable_and_never_authorizes_outbound() -> None:
    suppliers, _, service = populated_services()
    suppliers.save_screening(
        CounterpartyScreeningRun(
            screening_run_id="screening-immutable",
            supplier_profile_version_id="supplier-a-v1",
            provider="fixture",
            dataset_scope="fixture",
            dataset_version="1",
            published_at=NOW,
            algorithm="fixture",
            algorithm_version="1",
            request_fingerprint="sha256:request",
            run_status=ScreeningRunStatus.COMPLETED,
            disposition=ScreeningDisposition.NO_CANDIDATE,
            completed_at=NOW,
        )
    )
    assessment = service.assess("supplier-a-v1", frozen_spec(), policy(), as_of=NOW)
    service.decide(
        SupplierQualificationDecision(
            decision_id="approve-immutable",
            assessment_id=assessment.assessment_id,
            actor_id="person",
            actor_role="approver",
            decision=QualificationDecisionAction.APPROVE_FOR_DRY_RUN,
            rationale="reviewed",
            decided_at=NOW,
        ),
        policy=policy(),
    )
    snapshot = service.freeze_snapshot(assessment.assessment_id, policy(), as_of=NOW)
    with pytest.raises(ValidationError):
        snapshot.disposition = QualificationDisposition.BLOCKED  # type: ignore[misc]
    assert "outbound_authorized" not in snapshot.model_dump(mode="json")
