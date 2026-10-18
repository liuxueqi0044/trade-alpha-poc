"""Application services for deterministic RFQ package dry runs."""

from __future__ import annotations

# ruff: noqa: E501
from datetime import datetime
from typing import Any

from trade_alpha.contracts.common import VersionRef, require_utc, utc_now
from trade_alpha.execution.adapters.null_outbound import NullOutboundAdapter
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.suppliers.models import (
    SupplierQualificationSnapshot,
    qualification_snapshot_content_fingerprint,
)

from .disclosure import DEFAULT_COMMERCIAL_TERMS, build_supplier_view
from .models import (
    AttachmentManifestEntry,
    CampaignPurpose,
    DisclosurePolicyVersion,
    DryRunDecision,
    InternalDryRunDecision,
    OutboundDisposition,
    OutboundIntent,
    PackageStatus,
    SupplierRfqCampaign,
    SupplierRfqPackageSnapshot,
    deterministic_id,
)
from .ports import (
    AttachmentSanitizerPort,
    AuthorizationPort,
    ClockPort,
    NullAttachmentSanitizer,
    OutboundPort,
    ProductSpecReader,
    SupplierQualificationReader,
    SupplierRfqRendererPort,
    SupplierRfqRepository,
)
from .readiness import evaluate_readiness


class MinimumDryRunAuthorization:
    _APPROVER_ROLES = frozenset(
        {"dry_run_approver", "procurement_approver", "execution_approver"}
    )

    def may_approve_dry_run(self, actor_id: str, actor_role: str) -> bool:
        return bool(actor_id.strip() and actor_role in self._APPROVER_ROLES)


class UtcClock:
    def now(self) -> datetime:
        return utc_now()


class SupplierRfqCampaignService:
    def __init__(self, repository: SupplierRfqRepository, *, clock: ClockPort | None = None) -> None:
        self.repository = repository
        self.clock = clock or UtcClock()

    def create(
        self,
        *,
        source_rfq_id: str,
        rfq_line_item_id: str,
        product_spec_version_id: str,
        requested_quantity_tiers: tuple[Any, ...],
        response_deadline: datetime,
        target_delivery_place: str,
        requested_incoterm_options: tuple[Any, ...],
        requested_quote_currencies: tuple[str, ...],
        campaign_id: str | None = None,
        created_at: datetime | None = None,
    ) -> SupplierRfqCampaign:
        when = require_utc(created_at or self.clock.now())
        deadline = require_utc(response_deadline)
        fingerprint = stable_fingerprint(
            {
                "source_rfq_id": source_rfq_id,
                "rfq_line_item_id": rfq_line_item_id,
                "product_spec_version_id": product_spec_version_id,
                "requested_quantity_tiers": requested_quantity_tiers,
                "response_deadline": deadline,
                "target_delivery_place": target_delivery_place,
                "requested_incoterm_options": requested_incoterm_options,
                "requested_quote_currencies": tuple(sorted(set(requested_quote_currencies))),
            }
        )
        campaign = SupplierRfqCampaign(
            campaign_id=campaign_id or deterministic_id("campaign", fingerprint),
            campaign_version=1,
            source_rfq_id=source_rfq_id,
            rfq_line_item_id=rfq_line_item_id,
            product_spec_version_id=product_spec_version_id,
            purpose=CampaignPurpose.DRY_RUN,
            requested_quantity_tiers=tuple(requested_quantity_tiers),
            response_deadline=deadline,
            target_delivery_place=target_delivery_place,
            requested_incoterm_options=tuple(requested_incoterm_options),
            requested_quote_currencies=tuple(sorted(set(requested_quote_currencies))),
            campaign_fingerprint=fingerprint,
            created_at=when,
        )
        return self.repository.save_campaign(campaign)

    def add_supplier(
        self,
        campaign_id: str,
        supplier_id: str,
        *,
        as_of: datetime | None = None,
        changed_at: datetime | None = None,
    ) -> SupplierRfqCampaign:
        campaign = self.repository.get_campaign(campaign_id, as_of=as_of)
        if campaign is None:
            raise ValueError("campaign not found")
        if supplier_id in campaign.supplier_ids:
            return campaign
        effective_change = require_utc(changed_at or self.clock.now())
        if effective_change < campaign.created_at:
            raise ValueError("campaign change time must not precede campaign creation")
        if effective_change > campaign.response_deadline:
            raise ValueError("campaign cannot change after response deadline")
        suppliers = tuple(sorted((*campaign.supplier_ids, supplier_id)))
        version = campaign.campaign_version + 1
        new = campaign.model_copy(
            update={
                "campaign_version": version,
                "supplier_ids": suppliers,
                "campaign_fingerprint": stable_fingerprint(
                    {"base": campaign.campaign_fingerprint, "suppliers": suppliers}
                ),
                "created_at": effective_change,
            }
        )
        return self.repository.save_campaign(new)

    def get_as_of(self, campaign_id: str, as_of: datetime) -> SupplierRfqCampaign | None:
        return self.repository.get_campaign(campaign_id, as_of=require_utc(as_of))


class SupplierRfqPackageService:
    def __init__(
        self,
        repository: SupplierRfqRepository,
        *,
        product_specs: ProductSpecReader,
        qualifications: SupplierQualificationReader,
        renderer: SupplierRfqRendererPort,
        outbound: OutboundPort,
        sanitizer: AttachmentSanitizerPort | None = None,
        authorization: AuthorizationPort | None = None,
        clock: ClockPort | None = None,
        template_version: VersionRef | None = None,
    ) -> None:
        self.repository = repository
        self.product_specs = product_specs
        self.qualifications = qualifications
        self.renderer = renderer
        self.outbound = outbound
        if type(outbound) is not NullOutboundAdapter:
            raise ValueError("EP-05 requires an outbound adapter with is_null_adapter=True")
        self.sanitizer = sanitizer or NullAttachmentSanitizer()
        self.authorization = authorization or MinimumDryRunAuthorization()
        self.clock = clock or UtcClock()
        renderer_version = getattr(renderer, "template_version", None)
        self.template_version = template_version or VersionRef(
            name="supplier-rfq-template",
            version=str(renderer_version or "1.0.0"),
        )
        self.rendered_artifacts: dict[str, str] = {}

    def _qualification(
        self,
        qualification_snapshot_id: str,
        supplier_id: str,
        product_spec_version_id: str,
        as_of: datetime,
    ) -> SupplierQualificationSnapshot:
        getter = getattr(self.qualifications, "get_snapshot", None)
        snapshot = getter(qualification_snapshot_id) if getter is not None else None
        if snapshot is None:
            # Native EP-04 repository readers use snapshot_as_of and scope by
            # product specification; the small reader protocol remains optional.
            native_getter = getattr(self.qualifications, "snapshot_as_of", None)
            if native_getter is not None:
                snapshot = native_getter(supplier_id, product_spec_version_id, as_of)
        if snapshot is None:
            as_of_getter = getattr(self.qualifications, "get_as_of", None)
            if as_of_getter is not None:
                snapshot = as_of_getter(supplier_id, as_of)
        if snapshot is None:
            raise ValueError("qualification snapshot not found")
        if type(snapshot) is not SupplierQualificationSnapshot:
            raise TypeError("qualification reader returned an invalid snapshot type")
        if snapshot.content_fingerprint != qualification_snapshot_content_fingerprint(snapshot):
            raise ValueError("qualification snapshot content fingerprint mismatch")
        if snapshot.supplier_id != supplier_id:
            raise ValueError("qualification snapshot supplier mismatch")
        if snapshot.qualification_snapshot_id != qualification_snapshot_id:
            raise ValueError("qualification snapshot id mismatch")
        if snapshot.product_spec_version_id != product_spec_version_id:
            raise ValueError("qualification snapshot product specification mismatch")
        if require_utc(snapshot.as_of) > as_of:
            raise ValueError("qualification snapshot is newer than requested as_of")
        return snapshot

    def build_draft(
        self,
        campaign_id: str,
        supplier_id: str,
        *,
        qualification_snapshot_id: str,
        disclosure_policy: DisclosurePolicyVersion,
        attachment_manifest: tuple[Any, ...] = (),
        as_of: datetime | None = None,
    ) -> SupplierRfqPackageSnapshot:
        campaign = self.repository.get_campaign(campaign_id, as_of=as_of)
        if campaign is None:
            raise ValueError("campaign not found")
        if supplier_id not in campaign.supplier_ids:
            raise ValueError("supplier is not a member of the campaign")
        moment = require_utc(as_of or self.clock.now())
        spec = self.product_specs.get_version(campaign.product_spec_version_id)
        qualification = self._qualification(
            qualification_snapshot_id,
            supplier_id,
            campaign.product_spec_version_id,
            moment,
        )
        try:
            sanitized = self.sanitizer.sanitize(tuple(attachment_manifest))
            attachments = tuple(sanitized)
            for item in attachments:
                if not isinstance(item, AttachmentManifestEntry):
                    raise ValueError("sanitizer returned an invalid attachment")
                digest = item.sha256.removeprefix("sha256:")
                if not item.metadata_sanitized or len(digest) != 64 or any(
                    char not in "0123456789abcdefABCDEF" for char in digest
                ):
                    raise ValueError("sanitizer returned an unsafe attachment")
            attachment_errors: tuple[str, ...] = ()
        except Exception as error:
            # Never persist the unsanitized input when the sanitizer fails.
            attachments = ()
            attachment_errors = (f"attachment_sanitization_failed:{type(error).__name__}",)
        if spec is None:
            request = None
            questions: tuple[Any, ...] = ()
        else:
            try:
                request, attachments, questions = build_supplier_view(
                    campaign,
                    supplier_id=supplier_id,
                    spec=spec,
                    policy=disclosure_policy,
                    commercial_terms=DEFAULT_COMMERCIAL_TERMS,
                    attachments=attachments,
                )
            except ValueError as error:
                request = None
                questions = ()
                attachments = ()
                attachment_errors = (*attachment_errors, f"disclosure_failed:{str(error)}")
        readiness = evaluate_readiness(
            spec=spec,
            qualification_snapshot=qualification,
            structured_request=request,
            policy=disclosure_policy,
            as_of=moment,
            attachment_error_codes=attachment_errors,
        )
        status = PackageStatus.READY_FOR_INTERNAL_REVIEW if readiness.machine_ready else PackageStatus.BLOCKED_SPEC
        if any(item.startswith("qualification") for item in readiness.hard_blocks):
            status = PackageStatus.BLOCKED_SUPPLIER
        if any(item.startswith("disclosure") or item.startswith("attachment") for item in readiness.hard_blocks):
            status = PackageStatus.BLOCKED_DISCLOSURE
        supplier_profile_version_id = str(getattr(qualification, "supplier_profile_version_id", "unknown"))
        package_id = deterministic_id("package", campaign.campaign_id, supplier_id)
        prior_package = self.repository.get_package(package_id)
        version = 1 if prior_package is None else prior_package.version + 1
        if request is None:
            # Keep a validated schema object even for a blocked draft.
            raise_if = readiness.hard_blocks
            if not raise_if:
                raise ValueError("unable to build structured request")
            from .models import StructuredRequest

            request = StructuredRequest(
                campaign_id=campaign.campaign_id,
                supplier_id=supplier_id,
                buyer_alias=disclosure_policy.buyer_alias,
                product_spec_version_id=campaign.product_spec_version_id,
                lines=(),
                commercial_terms=DEFAULT_COMMERCIAL_TERMS,
                response_deadline=campaign.response_deadline,
                target_delivery_place=campaign.target_delivery_place,
                requested_incoterms=campaign.requested_incoterm_options,
                requested_currencies=campaign.requested_quote_currencies,
            )
        package_payload = {
            "campaign": campaign.campaign_id,
            "supplier": supplier_id,
            "supplier_profile_version": supplier_profile_version_id,
            "qualification": qualification_snapshot_id,
            "qualification_content": qualification.content_fingerprint,
            "spec": campaign.product_spec_version_id,
            "template": self.template_version,
            "policy": disclosure_policy.content_fingerprint(),
            "request": request,
            "questions": questions,
            "attachments": attachments,
            "readiness": readiness,
        }
        candidate_fingerprint = stable_fingerprint(package_payload)
        prior_package = self.repository.get_package(package_id)
        if prior_package is not None and prior_package.status is PackageStatus.RENDERED_NOT_SENT:
            if prior_package.input_fingerprint == candidate_fingerprint:
                return prior_package
            raise ValueError("terminal package inputs changed; create a new campaign/package")
        package = SupplierRfqPackageSnapshot(
            supplier_rfq_package_id=package_id,
            version=version,
            campaign_id=campaign.campaign_id,
            supplier_id=supplier_id,
            supplier_profile_version_id=supplier_profile_version_id,
            qualification_snapshot_id=qualification_snapshot_id,
            product_spec_version_id=campaign.product_spec_version_id,
            template_version=self.template_version,
            disclosure_policy_version=VersionRef(name=disclosure_policy.policy_id, version=disclosure_policy.policy_version),
            structured_request=request,
            open_questions=questions,
            attachment_manifest=attachments,
            package_readiness_result=readiness,
            status=status,
            created_at=moment,
            content_fingerprint=candidate_fingerprint,
            input_fingerprint=candidate_fingerprint,
        )
        saved = self.repository.save_package(package)
        # ``supplier_package_refs`` contains package IDs, never supplier IDs.
        # Supplier membership is kept separately on the campaign.
        if package.supplier_rfq_package_id not in campaign.supplier_package_refs:
            package_refs = tuple(
                sorted((*campaign.supplier_package_refs, package.supplier_rfq_package_id))
            )
            self.repository.save_campaign(
                campaign.model_copy(
                    update={
                        "campaign_version": campaign.campaign_version + 1,
                        "supplier_package_refs": package_refs,
                        "campaign_fingerprint": stable_fingerprint(
                            {"base": campaign.campaign_fingerprint, "packages": package_refs}
                        ),
                        "created_at": moment,
                    }
                )
            )
        return saved

    def evaluate_readiness(
        self, package_id: str, *, as_of: datetime | None = None
    ) -> Any:
        package = self.repository.get_package(package_id, as_of=as_of)
        if package is None:
            raise ValueError("package not found")
        return package.package_readiness_result

    def record_internal_decision(
        self,
        package_id: str,
        *,
        decision: DryRunDecision,
        actor_id: str,
        actor_role: str,
        rationale: str,
        condition_codes: tuple[str, ...] = (),
        decided_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> SupplierRfqPackageSnapshot:
        package = self.repository.get_package(package_id)
        if package is None:
            raise ValueError("package not found")
        if not self.authorization.may_approve_dry_run(actor_id, actor_role):
            raise PermissionError("actor is not authorized for dry-run approval")
        when = require_utc(decided_at or self.clock.now())
        decision_id = deterministic_id(
            "decision",
            package_id,
            when.isoformat(),
            actor_id,
            decision.value,
            condition_codes,
            rationale,
        )
        existing = self.repository.get_decision(decision_id)
        if existing is not None:
            # Idempotent retry returns the already persisted package version.
            latest = self.repository.get_package(package_id)
            if latest is None:
                raise ValueError("decision exists but package is missing")
            return latest
        if package.status is PackageStatus.RENDERED_NOT_SENT:
            raise ValueError("rendered package is terminal")
        if decision in {DryRunDecision.APPROVE, DryRunDecision.APPROVE_WITH_CONDITIONS} and not package.package_readiness_result.machine_ready:
            raise ValueError("blocked package cannot be approved")
        if (
            decision in {DryRunDecision.APPROVE, DryRunDecision.APPROVE_WITH_CONDITIONS}
            and package.status is not PackageStatus.READY_FOR_INTERNAL_REVIEW
        ):
            raise ValueError("only a package ready for internal review can be approved")
        record = InternalDryRunDecision(
            decision_id=decision_id,
            package_id=package_id,
            decision=decision,
            actor_id=actor_id,
            actor_role=actor_role,
            rationale=rationale,
            condition_codes=condition_codes,
            decided_at=when,
            expires_at=expires_at,
        )
        self.repository.save_decision(record)
        if decision in {DryRunDecision.APPROVE, DryRunDecision.APPROVE_WITH_CONDITIONS}:
            status = PackageStatus.APPROVED_FOR_DRY_RUN
        elif decision is DryRunDecision.REQUEST_CHANGES:
            status = PackageStatus.DRAFT
        else:
            status = PackageStatus.BLOCKED_APPROVAL
        updated = package.model_copy(
            update={
                "version": package.version + 1,
                "status": status,
                "internal_approval_decision_id": record.decision_id,
                "created_at": when,
                "content_fingerprint": stable_fingerprint({"prior": package.content_fingerprint, "decision": record}),
            }
        )
        return self.repository.save_package(updated)

    def render(self, package_id: str) -> SupplierRfqPackageSnapshot:
        package = self.repository.get_package(package_id)
        if package is None:
            raise ValueError("package not found")
        if package.status is not PackageStatus.APPROVED_FOR_DRY_RUN:
            raise ValueError("package requires internal dry-run approval before rendering")
        approval = self.repository.decision_for(package_id)
        if approval is None or approval.decision not in {
            DryRunDecision.APPROVE,
            DryRunDecision.APPROVE_WITH_CONDITIONS,
        }:
            raise ValueError("package has no effective dry-run approval")
        now = require_utc(self.clock.now())
        if approval.expires_at is not None and approval.expires_at <= now:
            raise ValueError("internal dry-run approval has expired")
        markdown = self.renderer.render_markdown(package)
        json_payload = self.renderer.render_json(package)
        md_ref = "artifact:" + stable_fingerprint({"format": "markdown", "content": markdown})
        json_ref = "artifact:" + stable_fingerprint({"format": "json", "content": json_payload})
        self.rendered_artifacts[md_ref] = markdown
        self.rendered_artifacts[json_ref] = json_payload
        updated = package.model_copy(
            update={
                "version": package.version + 1,
                "status": PackageStatus.RENDERED_NOT_SENT,
                "rendered_artifact_refs": (json_ref, md_ref),
                "created_at": now,
                "content_fingerprint": stable_fingerprint({"prior": package.content_fingerprint, "json": json_payload, "markdown": markdown}),
            }
        )
        return self.repository.save_package(updated)

    def attempt_dry_run_delivery(self, package_id: str) -> Any:
        package = self.repository.get_package(package_id)
        if package is None:
            raise ValueError("package not found")
        if package.status is not PackageStatus.RENDERED_NOT_SENT:
            raise ValueError("package must be rendered before dry-run delivery attempt")
        if package.outbound_disposition is OutboundDisposition.BLOCKED_BY_NULL_ADAPTER:
            raise ValueError("null outbound attempt is terminal")
        if type(self.outbound) is not NullOutboundAdapter:
            raise RuntimeError("EP-05 refuses a non-null outbound adapter")
        intent = OutboundIntent(
            package_id=package.supplier_rfq_package_id,
            supplier_id=package.supplier_id,
            payload_fingerprint=package.content_fingerprint,
        )
        result = self.outbound.deliver(intent)
        if result.status is not OutboundDisposition.BLOCKED_BY_NULL_ADAPTER or result.side_effect_count != 0:
            raise RuntimeError("outbound adapter violated EP-05 zero-side-effect contract")
        updated = package.model_copy(
            update={
                "version": package.version + 1,
                "outbound_disposition": OutboundDisposition.BLOCKED_BY_NULL_ADAPTER,
                "created_at": require_utc(self.clock.now()),
                "content_fingerprint": stable_fingerprint(
                    {"prior": package.content_fingerprint, "delivery": result}
                ),
            }
        )
        self.repository.save_package(updated)
        return result

    def get_as_of(self, package_id: str, as_of: datetime) -> SupplierRfqPackageSnapshot | None:
        return self.repository.get_package(package_id, as_of=require_utc(as_of))
