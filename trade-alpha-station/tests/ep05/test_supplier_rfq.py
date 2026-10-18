from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from jinja2 import UndefinedError
from jinja2.exceptions import SecurityError

from trade_alpha.contracts.common import VersionRef
from trade_alpha.execution.adapters.jinja_rfq_renderer import JinjaRfqRenderer
from trade_alpha.execution.adapters.null_outbound import NullOutboundAdapter
from trade_alpha.execution.contracts.models import Incoterm, IncotermCode
from trade_alpha.execution.specifications.models import (
    FieldState,
    GateStatus,
    ProductSpecVersion,
    ReadinessResult,
    SpecFieldProjection,
    SpecState,
)
from trade_alpha.execution.supplier_rfq.models import (
    AttachmentManifestEntry,
    DisclosurePolicyVersion,
    DryRunDecision,
    QuantityTier,
    RequestedIncoterm,
)
from trade_alpha.execution.supplier_rfq.repository import InMemorySupplierRfqRepository
from trade_alpha.execution.supplier_rfq.service import (
    SupplierRfqCampaignService,
    SupplierRfqPackageService,
)
from trade_alpha.execution.suppliers.models import (
    QualificationDisposition,
    SupplierQualificationSnapshot,
    qualification_snapshot_content_fingerprint,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _spec(state: SpecState = SpecState.FROZEN_FOR_SUPPLIER_RFQ) -> ProductSpecVersion:
    return ProductSpecVersion(
        spec_version_id="spec-v1",
        spec_id="spec",
        sequence=1,
        profile_ref=VersionRef(name="family", version="1"),
        fields=(
            SpecFieldProjection(
                field_key="length",
                state=FieldState.CONFIRMED,
                canonical_value="100",
                normalizer_version=VersionRef(name="normalizer", version="1"),
            ),
            SpecFieldProjection(
                field_key="material",
                state=FieldState.OPEN_TO_SUPPLIER_PROPOSAL,
                normalizer_version=VersionRef(name="normalizer", version="1"),
            ),
        ),
        readiness=ReadinessResult(status=GateStatus.READY, profile_version="1"),
        state=state,
        fingerprint="sha256:spec",
        created_at=NOW,
        created_by="fixture",
    )


class SpecReader:
    def __init__(self, spec: ProductSpecVersion) -> None:
        self.spec = spec

    def get_version(self, _spec_version_id: str) -> ProductSpecVersion:
        return self.spec


class QualificationReader:
    def __init__(self, snapshot: SupplierQualificationSnapshot) -> None:
        self.snapshot = snapshot

    def get_snapshot(self, _snapshot_id: str) -> SupplierQualificationSnapshot:
        return self.snapshot


def _services(spec: ProductSpecVersion | None = None):
    spec = spec or _spec()
    pending_snapshot = SupplierQualificationSnapshot(
        qualification_snapshot_id="qualification-v1",
        supplier_id="supplier-a",
        supplier_profile_version_id="profile-v1",
        product_family_id="family",
        product_spec_version_id="spec-v1",
        assessment_id="assessment",
        effective_decision_id="decision",
        disposition=QualificationDisposition.DRY_RUN_ELIGIBLE,
        as_of=NOW,
        policy_version="policy-1",
        content_fingerprint="pending",
    )
    snapshot = pending_snapshot.model_copy(
        update={
            "content_fingerprint": qualification_snapshot_content_fingerprint(
                pending_snapshot
            )
        }
    )
    repo = InMemorySupplierRfqRepository()
    campaign = SupplierRfqCampaignService(repo).create(
        source_rfq_id="rfq",
        rfq_line_item_id="line",
        product_spec_version_id="spec-v1",
        requested_quantity_tiers=(QuantityTier(quantity="10", unit="piece"),),
        response_deadline=NOW + timedelta(days=10),
        target_delivery_place="Shanghai",
        requested_incoterm_options=(
            RequestedIncoterm(
                incoterm=Incoterm(
                    code=IncotermCode.FOB, rules_version="2020", named_place="Shanghai"
                )
            ),
        ),
        requested_quote_currencies=("USD",),
        created_at=NOW,
    )
    campaign = SupplierRfqCampaignService(repo).add_supplier(
        campaign.campaign_id, "supplier-a", changed_at=NOW
    )
    service = SupplierRfqPackageService(
        repo,
        product_specs=SpecReader(spec),
        qualifications=QualificationReader(snapshot),
        renderer=JinjaRfqRenderer(),
        outbound=NullOutboundAdapter(),
    )
    return service, campaign


def test_open_proposal_is_explicit_and_null_delivery_has_no_side_effect() -> None:
    service, campaign = _services()
    package = service.build_draft(
        campaign.campaign_id,
        "supplier-a",
        qualification_snapshot_id="qualification-v1",
        disclosure_policy=DisclosurePolicyVersion(
            policy_id="disclosure", policy_version="1", buyer_alias="Buyer Alias"
        ),
        as_of=NOW,
    )
    assert package.package_readiness_result.machine_ready
    assert package.open_questions[0].source_spec_field == "material"
    package = service.record_internal_decision(
        package.package_id,
        decision=DryRunDecision.APPROVE,
        actor_id="reviewer",
        actor_role="procurement_approver",
        rationale="internal rehearsal",
        decided_at=NOW + timedelta(hours=1),
    )
    package = service.render(package.package_id)
    delivery = service.attempt_dry_run_delivery(package.package_id)
    assert delivery.side_effect_count == 0
    assert delivery.recipient_contact_used is None
    assert (
        service.repository.get_package(package.package_id).outbound_disposition.value
        == "blocked_by_null_adapter"
    )
    same = service.build_draft(
        campaign.campaign_id,
        "supplier-a",
        qualification_snapshot_id="qualification-v1",
        disclosure_policy=DisclosurePolicyVersion(
            policy_id="disclosure", policy_version="1", buyer_alias="Buyer Alias"
        ),
        as_of=NOW,
    )
    assert same.version == package.version + 1
    with pytest.raises(ValueError, match="terminal package inputs changed"):
        service.build_draft(
            campaign.campaign_id,
            "supplier-a",
            qualification_snapshot_id="qualification-v1",
            disclosure_policy=DisclosurePolicyVersion(
                policy_id="disclosure", policy_version="changed", buyer_alias="Buyer Alias"
            ),
            as_of=NOW,
        )


def test_unfrozen_spec_is_blocked() -> None:
    service, campaign = _services(_spec(SpecState.CONFIRMED))
    package = service.build_draft(
        campaign.campaign_id,
        "supplier-a",
        qualification_snapshot_id="qualification-v1",
        disclosure_policy=DisclosurePolicyVersion(
            policy_id="disclosure", policy_version="1", buyer_alias="Buyer Alias"
        ),
        as_of=NOW,
    )
    assert not package.package_readiness_result.machine_ready
    with pytest.raises(ValueError):
        service.record_internal_decision(
            package.package_id,
            decision=DryRunDecision.APPROVE,
            actor_id="reviewer",
            actor_role="procurement_approver",
            rationale="must not approve",
            decided_at=NOW + timedelta(hours=1),
        )


def test_default_authorization_is_fail_closed_and_non_null_adapter_is_rejected() -> None:
    service, campaign = _services()
    from trade_alpha.execution.supplier_rfq.service import MinimumDryRunAuthorization

    assert not MinimumDryRunAuthorization().may_approve_dry_run("reviewer", "viewer")
    assert not MinimumDryRunAuthorization().may_approve_dry_run("reviewer", "admin")

    class FakeOutbound:
        def deliver(self, _intent):  # pragma: no cover - must not be called
            raise AssertionError("non-null adapter must be rejected at construction")

    with pytest.raises(ValueError):
        SupplierRfqPackageService(
            InMemorySupplierRfqRepository(),
            product_specs=SpecReader(_spec()),
            qualifications=QualificationReader(
                _services()[0].qualifications.snapshot  # type: ignore[attr-defined]
            ),
            renderer=JinjaRfqRenderer(),
            outbound=FakeOutbound(),  # type: ignore[arg-type]
        )


def test_expired_approval_and_request_changes_are_not_renderable() -> None:
    service, campaign = _services()
    package = service.build_draft(
        campaign.campaign_id,
        "supplier-a",
        qualification_snapshot_id="qualification-v1",
        disclosure_policy=DisclosurePolicyVersion(
            policy_id="disclosure", policy_version="1", buyer_alias="Buyer Alias"
        ),
        as_of=NOW,
    )
    package = service.record_internal_decision(
        package.package_id,
        decision=DryRunDecision.REQUEST_CHANGES,
        actor_id="reviewer",
        actor_role="procurement_approver",
        rationale="clarify",
        decided_at=NOW + timedelta(hours=1),
    )
    assert package.status.value == "draft"
    with pytest.raises(ValueError):
        service.render(package.package_id)
    package = service.build_draft(
        campaign.campaign_id,
        "supplier-a",
        qualification_snapshot_id="qualification-v1",
        disclosure_policy=DisclosurePolicyVersion(
            policy_id="disclosure", policy_version="2", buyer_alias="Buyer Alias"
        ),
        as_of=NOW,
    )
    package = service.record_internal_decision(
        package.package_id,
        decision=DryRunDecision.APPROVE,
        actor_id="reviewer",
        actor_role="procurement_approver",
        rationale="expired fixture",
        decided_at=NOW + timedelta(hours=3),
        expires_at=NOW + timedelta(hours=4),
    )
    with pytest.raises(ValueError, match="expired"):
        service.render(package.package_id)


def test_strict_undefined_and_required_question_template_checks() -> None:
    service, campaign = _services()
    package = service.build_draft(
        campaign.campaign_id,
        "supplier-a",
        qualification_snapshot_id="qualification-v1",
        disclosure_policy=DisclosurePolicyVersion(
            policy_id="disclosure", policy_version="1", buyer_alias="Buyer Alias"
        ),
        as_of=NOW,
    )
    approved = service.record_internal_decision(
        package.package_id,
        decision=DryRunDecision.APPROVE,
        actor_id="reviewer",
        actor_role="procurement_approver",
        rationale="internal rehearsal",
        decided_at=NOW + timedelta(hours=1),
    )
    with pytest.raises(ValueError, match="omitted required questions"):
        SupplierRfqPackageService(
            service.repository,
            product_specs=SpecReader(_spec()),
            qualifications=QualificationReader(_services()[0].qualifications.snapshot),  # type: ignore[attr-defined]
            renderer=JinjaRfqRenderer(template="{{ structured_request.buyer_alias }}"),
            outbound=NullOutboundAdapter(),
        ).render(approved.package_id)
    with pytest.raises(UndefinedError):
        JinjaRfqRenderer(
            template="{{ structured_request.does_not_exist }}"
        ).render_markdown(approved)


def test_attachment_hash_and_sanitizer_fail_closed() -> None:
    with pytest.raises(ValueError):
        AttachmentManifestEntry(
            artifact_id="a",
            filename="drawing.pdf",
            sha256="sha256:bad",
            metadata_sanitized=True,
        )
    service, campaign = _services()
    unsafe = AttachmentManifestEntry(
        artifact_id="a",
        filename="drawing.pdf",
        sha256="sha256:" + "a" * 64,
        metadata_sanitized=False,
    )
    package = service.build_draft(
        campaign.campaign_id,
        "supplier-a",
        qualification_snapshot_id="qualification-v1",
        disclosure_policy=DisclosurePolicyVersion(
            policy_id="disclosure", policy_version="1", buyer_alias="Buyer Alias"
        ),
        attachment_manifest=(unsafe,),
        as_of=NOW,
    )
    assert package.attachment_manifest == ()
    assert package.status.value == "blocked_disclosure"


def test_campaign_membership_and_qualification_as_of_boundaries() -> None:
    service, campaign = _services()
    assert campaign.supplier_ids == ("supplier-a",)
    assert campaign.supplier_package_refs == ()
    with pytest.raises(ValueError, match="member"):
        service.build_draft(
            campaign.campaign_id,
            "supplier-b",
            qualification_snapshot_id="qualification-v1",
            disclosure_policy=DisclosurePolicyVersion(
                policy_id="disclosure", policy_version="1", buyer_alias="Buyer Alias"
            ),
            as_of=NOW,
        )
    original = service.qualifications.snapshot  # type: ignore[attr-defined]
    future_snapshot = original.model_copy(
        update={"as_of": NOW + timedelta(days=1)}
    )
    service.qualifications.snapshot = future_snapshot.model_copy(  # type: ignore[attr-defined]
        update={
            "content_fingerprint": qualification_snapshot_content_fingerprint(
                future_snapshot
            )
        }
    )
    with pytest.raises(ValueError, match="newer"):
        service.build_draft(
            campaign.campaign_id,
            "supplier-a",
            qualification_snapshot_id="qualification-v1",
            disclosure_policy=DisclosurePolicyVersion(
                policy_id="disclosure", policy_version="1", buyer_alias="Buyer Alias"
            ),
            as_of=NOW,
        )


def test_renderer_sandbox_blocks_dunder_attribute_traversal() -> None:
    service, campaign = _services()
    package = service.build_draft(
        campaign.campaign_id,
        "supplier-a",
        qualification_snapshot_id="qualification-v1",
        disclosure_policy=DisclosurePolicyVersion(
            policy_id="disclosure", policy_version="1", buyer_alias="Buyer Alias"
        ),
        as_of=NOW,
    )
    with pytest.raises(SecurityError):
        JinjaRfqRenderer(
            template=(
                "{{ open_questions[0].question_id }} "
                "{{ structured_request.__class__.__mro__ }}"
            )
        ).render_markdown(package)


def test_qualification_reader_rejects_duck_types_and_forged_fingerprints() -> None:
    service, campaign = _services()
    real_snapshot = service.qualifications.snapshot  # type: ignore[attr-defined]
    service.qualifications.snapshot = SimpleNamespace(  # type: ignore[attr-defined]
        dry_run_eligible=True,
        supplier_id="supplier-a",
        qualification_snapshot_id="qualification-v1",
        product_spec_version_id="spec-v1",
        as_of=NOW,
        content_fingerprint="fake",
    )
    with pytest.raises(TypeError, match="invalid snapshot type"):
        service.build_draft(
            campaign.campaign_id,
            "supplier-a",
            qualification_snapshot_id="qualification-v1",
            disclosure_policy=DisclosurePolicyVersion(
                policy_id="disclosure", policy_version="1", buyer_alias="Buyer Alias"
            ),
            as_of=NOW,
        )

    service.qualifications.snapshot = real_snapshot.model_copy(  # type: ignore[attr-defined]
        update={"content_fingerprint": "sha256:forged"}
    )
    with pytest.raises(ValueError, match="content fingerprint mismatch"):
        service.build_draft(
            campaign.campaign_id,
            "supplier-a",
            qualification_snapshot_id="qualification-v1",
            disclosure_policy=DisclosurePolicyVersion(
                policy_id="disclosure", policy_version="1", buyer_alias="Buyer Alias"
            ),
            as_of=NOW,
        )
