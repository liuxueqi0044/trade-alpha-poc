from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_alpha.contracts.common import EvidenceLocator, VersionRef
from trade_alpha.execution.adapters.commercial_evidence import CommercialEvidenceAdapter
from trade_alpha.execution.adapters.execution_dependencies import (
    CostScenarioDependencyReader,
    RiskAssessmentDependencyReader,
)
from trade_alpha.execution.adapters.fixture_screening import FixtureScreeningAdapter
from trade_alpha.execution.adapters.governance_execution import (
    GovernanceApprovalAuditAdapter,
    GovernanceAuditChainVerifierAdapter,
    GovernanceExecutionAuthorizationAdapter,
    GovernanceRiskAuditAdapter,
)
from trade_alpha.execution.adapters.jinja_customer_quote_renderer import (
    JinjaCustomerQuoteRenderer,
)
from trade_alpha.execution.adapters.jinja_rfq_renderer import JinjaRfqRenderer
from trade_alpha.execution.adapters.null_outbound import NullOutboundAdapter
from trade_alpha.execution.adapters.pint_units import PintUnitConverter
from trade_alpha.execution.commercial_risk import (
    InMemoryTradeRiskRepository,
    RepositoryTradeRiskSourceReader,
    RiskAssessmentInput,
    RiskPolicyVersion,
    TradeRiskService,
)
from trade_alpha.execution.commercial_risk import (
    policy_content_fingerprint as risk_policy_fingerprint,
)
from trade_alpha.execution.contracts import (
    FxSnapshot,
    Incoterm,
    IncotermCode,
    MonetaryAmount,
    ProvenanceRef,
)
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.costing import (
    CostComponentKind,
    CostScenarioPolicyVersion,
    CostScenarioService,
    InMemoryCostingSourceReader,
    InMemoryCostScenarioRepository,
)
from trade_alpha.execution.costing import (
    policy_content_fingerprint as cost_policy_fingerprint,
)
from trade_alpha.execution.customer_quotes import (
    CustomerCommercialDecision,
    CustomerCommercialDecisionAction,
    CustomerCostScenarioSelection,
    CustomerQuoteLine,
    CustomerQuotePolicyVersion,
    CustomerQuoteService,
    CustomerQuoteStatus,
    CustomerQuoteTerms,
    ExecutionChainNode,
    ExecutionChainVerifier,
    InMemoryCustomerInputReferenceReader,
    InMemoryCustomerQuoteRepository,
    InMemoryExecutionChainNodeReader,
    InputReference,
)
from trade_alpha.execution.operational_readiness import (
    ApprovalOutcome,
    ApprovalStage,
    ApprovalWorkflowService,
    InMemoryOperationalReadinessRepository,
    OperationalCapability,
    OperationalReadinessPolicyVersion,
    OperationalReadinessService,
    OperationalReadinessSnapshot,
    ReadinessItem,
    ReadinessItemState,
    readiness_snapshot_fingerprint,
)
from trade_alpha.execution.specifications import (
    FieldState,
    GateStatus,
    ProductSpecVersion,
    ReadinessResult,
    SpecFieldProjection,
    SpecState,
)
from trade_alpha.execution.specifications.repository import InMemoryProductSpecRepository
from trade_alpha.execution.supplier_quotes import (
    ComponentType,
    ConfirmedQuoteLine,
    FixtureProductSpecReader,
    FixtureSupplierRfqPackageReader,
    FxSnapshotReaderRecord,
    InclusionStatus,
    InMemoryFxSnapshotReader,
    InMemorySupplierQuoteRepository,
    PriceComponent,
    QuantityBasis,
    QuoteComparisonService,
    QuoteDecisionAction,
    QuoteNormalizationPolicyVersion,
    QuoteNormalizationService,
    QuoteTerm,
    SupplierQuoteDecision,
    SupplierQuoteExtractionService,
    SupplierQuoteIntakeService,
    SupplierQuoteLineCandidate,
    SupplierQuoteReviewService,
    SupplierQuoteService,
    policy_content_fingerprint,
)
from trade_alpha.execution.supplier_rfq import (
    DisclosurePolicyVersion,
    DryRunDecision,
    InMemorySupplierRfqRepository,
    OutboundDisposition,
    QuantityTier,
    RequestedIncoterm,
    SupplierRfqCampaignService,
    SupplierRfqPackageService,
)
from trade_alpha.execution.supplier_rfq.ports import FixedClock
from trade_alpha.execution.suppliers import (
    CapabilityPredicate,
    CapabilitySourceType,
    CapabilityType,
    ClaimReviewStatus,
    InMemorySupplierQualificationRepository,
    InMemorySupplierRepository,
    QualificationDecisionAction,
    QualificationDisposition,
    QualificationPolicyVersion,
    SupplierCapabilityClaim,
    SupplierIdentityService,
    SupplierProfile,
    SupplierProfileVersion,
    SupplierQualificationDecision,
    SupplierQualificationService,
    SupplierScreeningService,
    SupplierSiteVersion,
)
from trade_alpha.governance import AppendOnlyAuditLog, Identity, PolicyEngine, Role
from trade_alpha.ingestion import EvidenceStore

NOW = datetime(2026, 8, 27, 2, tzinfo=UTC)


class _SpecReader:
    def __init__(self, spec: ProductSpecVersion) -> None:
        self.spec = spec

    def get_version(self, spec_version_id: str) -> ProductSpecVersion | None:
        return self.spec if self.spec.spec_version_id == spec_version_id else None


def _provenance(artifact_id: str) -> ProvenanceRef:
    return ProvenanceRef(
        source="integration fixture",
        evidence=EvidenceLocator(artifact_id=artifact_id, block_ids=("block-1",)),
        observed_at=NOW,
        source_version=VersionRef(name="integration-fixture", version="1.0.0"),
    )


def _frozen_spec() -> ProductSpecVersion:
    normalizer = VersionRef(name="spec-normalizer", version="1.0.0")
    return ProductSpecVersion(
        spec_version_id="spec-fe-dry-01-v1",
        spec_id="spec-fe-dry-01",
        sequence=1,
        profile_ref=VersionRef(name="FE-DRY-01", version="1.0.0"),
        fields=(
            SpecFieldProjection(
                field_key="length",
                state=FieldState.CONFIRMED,
                canonical_value={"amount": "1000", "unit": "mm"},
                display_value="1000 mm",
                normalizer_version=normalizer,
            ),
            SpecFieldProjection(
                field_key="filter_material",
                state=FieldState.OPEN_TO_SUPPLIER_PROPOSAL,
                normalizer_version=normalizer,
            ),
            SpecFieldProjection(
                field_key="application_scope",
                state=FieldState.CONFIRMED,
                canonical_value="general industrial dry dust",
                display_value="general industrial dry dust",
                normalizer_version=normalizer,
            ),
        ),
        open_proposal_fields=("filter_material",),
        readiness=ReadinessResult(
            status=GateStatus.READY,
            open_proposal_fields=("filter_material",),
            profile_version="1.0.0",
        ),
        state=SpecState.FROZEN_FOR_SUPPLIER_RFQ,
        fingerprint="sha256:frozen-spec-fixture",
        created_at=NOW,
        created_by="spec-approver",
    )


def _qualify_suppliers(
    spec: ProductSpecVersion,
) -> tuple[
    tuple[str, ...],
    InMemorySupplierQualificationRepository,
]:
    supplier_ids = ("supplier-a", "supplier-b", "supplier-c")
    suppliers = InMemorySupplierRepository()
    qualifications = InMemorySupplierQualificationRepository()
    identity = SupplierIdentityService(suppliers)
    policy = QualificationPolicyVersion(
        policy_id="fe-dry-01-dry-run",
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
        allowed_human_roles=("supplier-approver",),
        config_fingerprint="sha256:qualification-policy",
        effective_from=NOW - timedelta(days=1),
    )
    screening = SupplierScreeningService(
        suppliers,
        FixtureScreeningAdapter(
            dataset_version="fixture-2026-08-27",
            published_at=NOW - timedelta(hours=1),
        ),
    )
    qualification = SupplierQualificationService(suppliers, qualifications)
    snapshot_ids: list[str] = []
    for supplier_id in supplier_ids:
        source = _provenance(f"{supplier_id}-profile-artifact")
        site = SupplierSiteVersion(
            site_id=f"{supplier_id}-factory",
            site_version_id=f"{supplier_id}-factory-v1",
            supplier_id=supplier_id,
            site_type="factory",
            address_raw=f"{supplier_id} industrial park",
            country_or_region="CN",
            provenance_refs=(source,),
            valid_from=NOW,
            content_fingerprint=f"sha256:{supplier_id}-site",
        )
        profile_version = SupplierProfileVersion(
            supplier_profile_version_id=f"{supplier_id}-profile-v1",
            supplier_id=supplier_id,
            version=1,
            legal_name_raw=f"{supplier_id} factory",
            legal_name_normalized=f"{supplier_id} factory",
            country_or_region="CN",
            sites=(site,),
            provenance_refs=(source,),
            valid_from=NOW,
            system_time=NOW,
            content_fingerprint=f"sha256:{supplier_id}-profile",
        )
        identity.capture_profile(
            SupplierProfile(supplier_id=supplier_id, created_at=NOW),
            profile_version,
            idempotency_key=f"identity-{supplier_id}",
        )
        suppliers.save_capability(
            SupplierCapabilityClaim(
                capability_claim_id=f"{supplier_id}-manufacturing",
                supplier_id=supplier_id,
                site_id=site.site_id,
                product_family_id="FE-DRY-01",
                capability_type=CapabilityType.MANUFACTURING,
                claim_value="general industrial dry dust filter cartridges",
                source_type=CapabilitySourceType.SUPPLIER_ASSERTED,
                provenance_refs=(source,),
                confidence=Decimal("0.8"),
                valid_from=NOW,
                recorded_at=NOW,
                review_status=ClaimReviewStatus.REVIEWED,
            )
        )
        screening.run(profile_version.supplier_profile_version_id, as_of=NOW)
        assessment = qualification.assess(
            profile_version.supplier_profile_version_id,
            spec,
            policy,
            as_of=NOW,
        )
        qualification.decide(
            SupplierQualificationDecision(
                decision_id=f"{supplier_id}-dry-run-approval",
                assessment_id=assessment.assessment_id,
                actor_id="supplier-reviewer",
                actor_role="supplier-approver",
                decision=QualificationDecisionAction.APPROVE_FOR_DRY_RUN,
                rationale="identity, scope, and fixture screening reviewed",
                decided_at=NOW + timedelta(minutes=10),
                expires_at=NOW + timedelta(days=30),
            ),
            policy=policy,
        )
        snapshot = qualification.freeze_snapshot(
            assessment.assessment_id,
            policy,
            as_of=NOW + timedelta(minutes=10),
        )
        assert snapshot.disposition is QualificationDisposition.DRY_RUN_ELIGIBLE
        snapshot_ids.append(snapshot.qualification_snapshot_id)
    return tuple(snapshot_ids), qualifications


def _build_supplier_packages(
    spec: ProductSpecVersion,
    supplier_ids: tuple[str, ...],
    snapshot_ids: tuple[str, ...],
    qualifications: InMemorySupplierQualificationRepository,
):
    repository = InMemorySupplierRfqRepository()
    campaign_service = SupplierRfqCampaignService(
        repository, clock=FixedClock(NOW + timedelta(hours=1))
    )
    campaign = campaign_service.create(
        source_rfq_id="buyer-rfq-1",
        rfq_line_item_id="buyer-rfq-line-1",
        product_spec_version_id=spec.spec_version_id,
        requested_quantity_tiers=(QuantityTier(quantity="100", unit="piece"),),
        response_deadline=NOW + timedelta(days=7),
        target_delivery_place="Shanghai, China",
        requested_incoterm_options=(
            RequestedIncoterm(
                incoterm=Incoterm(
                    code=IncotermCode.EXW,
                    rules_version="Incoterms 2020",
                    named_place="Supplier factory",
                )
            ),
            RequestedIncoterm(
                incoterm=Incoterm(
                    code=IncotermCode.FOB,
                    rules_version="Incoterms 2020",
                    named_place="Shanghai",
                )
            ),
        ),
        requested_quote_currencies=("CNY", "USD"),
        created_at=NOW + timedelta(hours=1),
    )
    for supplier_id in supplier_ids:
        campaign = campaign_service.add_supplier(campaign.campaign_id, supplier_id)
    outbound = NullOutboundAdapter()
    package_service = SupplierRfqPackageService(
        repository,
        product_specs=_SpecReader(spec),
        qualifications=qualifications,
        renderer=JinjaRfqRenderer(),
        outbound=outbound,
        clock=FixedClock(NOW + timedelta(hours=3)),
    )
    policy = DisclosurePolicyVersion(
        policy_id="supplier-disclosure",
        policy_version="1.0.0",
        buyer_alias="Buyer Alias",
    )
    packages = []
    for supplier_id, snapshot_id in zip(supplier_ids, snapshot_ids, strict=True):
        package = package_service.build_draft(
            campaign.campaign_id,
            supplier_id,
            qualification_snapshot_id=snapshot_id,
            disclosure_policy=policy,
            as_of=NOW + timedelta(hours=1),
        )
        assert package.structured_request.supplier_id == supplier_id
        assert {question.source_spec_field for question in package.open_questions} == {
            "filter_material"
        }
        package_service.record_internal_decision(
            package.package_id,
            decision=DryRunDecision.APPROVE,
            actor_id="rfq-reviewer",
            actor_role="procurement_approver",
            rationale="approved for internal dry-run rendering only",
            decided_at=NOW + timedelta(hours=2),
            expires_at=NOW + timedelta(days=1),
        )
        package_service.render(package.package_id)
        result = package_service.attempt_dry_run_delivery(package.package_id)
        assert result.side_effect_count == 0
        current = repository.get_package(package.package_id)
        assert current is not None
        assert current.outbound_disposition is OutboundDisposition.BLOCKED_BY_NULL_ADAPTER
        packages.append(current)
    assert outbound.attempt_count == 3
    assert len({package.supplier_id for package in packages}) == 3
    assert all(
        other.supplier_id not in package.model_dump_json()
        for package in packages
        for other in packages
        if other.supplier_id != package.supplier_id
    )
    return tuple(packages)


def _quote_provenance(artifact_id: str) -> ProvenanceRef:
    return ProvenanceRef(
        source="supplier quote fixture",
        evidence=EvidenceLocator(artifact_id=artifact_id, block_ids=("quote-block-1",)),
        observed_at=NOW + timedelta(hours=4),
        source_version=VersionRef(name="supplier-quote-fixture", version="1.0.0"),
    )


def _price(
    *,
    amount: str,
    currency: str,
    basis: str,
    provenance: ProvenanceRef,
) -> PriceComponent:
    return PriceComponent(
        component_type=ComponentType.PRODUCT,
        inclusion_status=InclusionStatus.INCLUDED,
        amount=MonetaryAmount(amount=amount, currency=currency),
        quantity_basis=QuantityBasis(amount=basis, unit="piece"),
        provenance_ref=provenance,
    )


def _confirm_quote(
    quote_service: SupplierQuoteService,
    extraction_service: SupplierQuoteExtractionService,
    review_service: SupplierQuoteReviewService,
    receipt,
    package,
    *,
    amount: str,
    currency: str,
    basis: str,
    incoterm: IncotermCode,
    named_place: str,
    packaging: PriceComponent,
):
    provenance = _quote_provenance(receipt.artifact_refs[0])
    terms = QuoteTerm(
        currency=currency,
        incoterm=Incoterm(
            code=incoterm,
            rules_version="Incoterms 2020",
            named_place=named_place,
        ),
        quote_valid_until=NOW + timedelta(days=14),
        production_lead_time=QuantityBasis(amount="30", unit="day"),
        production_lead_time_start_trigger="deposit received",
        components=(
            packaging,
            PriceComponent(
                component_type=ComponentType.INTERNATIONAL_FREIGHT,
                inclusion_status=InclusionStatus.UNKNOWN,
                provenance_ref=provenance,
            ),
            PriceComponent(
                component_type=ComponentType.INSURANCE,
                inclusion_status=InclusionStatus.UNKNOWN,
                provenance_ref=provenance,
            ),
            PriceComponent(
                component_type=ComponentType.TAX,
                inclusion_status=InclusionStatus.UNKNOWN,
                provenance_ref=provenance,
            ),
        ),
    )
    line_candidate = SupplierQuoteLineCandidate(
        candidate_line_id=f"{receipt.quote_id}-line-mapping",
        quote_id=receipt.quote_id,
        supplier_line_ref="supplier-line-1",
        proposed_rfq_line_id=package.structured_request.lines[0].line_id,
        mapping_confidence=Decimal("0.95"),
        description_raw="supplier quoted line",
        quantity_breaks=(QuantityBasis(amount=basis, unit="piece"),),
        price_basis=QuantityBasis(amount=basis, unit="piece"),
        provenance_refs=(provenance,),
    )
    extraction_service.project_candidates(receipt.quote_id, lines=(line_candidate,))
    review_service.record_decision(
        SupplierQuoteDecision(
            decision_id=f"{receipt.quote_id}-line-mapping-review",
            quote_id=receipt.quote_id,
            field_path="lines[0].rfq_line_id",
            candidate_id=line_candidate.candidate_line_id,
            decision=QuoteDecisionAction.ACCEPT,
            rationale="human mapped the supplier line to the exact RFQ line",
            actor_id="quote-reviewer",
            decided_at=NOW + timedelta(hours=4),
            provenance_ref=provenance,
        )
    )
    term_facts = {
        "terms.currency": currency,
        "terms.incoterm": terms.incoterm.model_dump(mode="json"),
        "terms.validity": {
            "quote_valid_from": None,
            "quote_valid_until": terms.quote_valid_until.isoformat()
            if terms.quote_valid_until
            else None,
        },
        **(
            {"terms.payment_terms": terms.payment_terms}
            if terms.payment_terms is not None
            else {}
        ),
        **(
            {"terms.sample_fee": terms.sample_fee.model_dump(mode="json")}
            if terms.sample_fee is not None
            else {}
        ),
        **(
            {"terms.sample_lead_time": terms.sample_lead_time.model_dump(mode="json")}
            if terms.sample_lead_time is not None
            else {}
        ),
        **(
            {
                "terms.production_lead_time": terms.production_lead_time.model_dump(
                    mode="json"
                )
            }
            if terms.production_lead_time is not None
            else {}
        ),
        **(
            {
                "terms.production_lead_time_start_trigger": (
                    terms.production_lead_time_start_trigger
                )
            }
            if terms.production_lead_time_start_trigger is not None
            else {}
        ),
        **({"terms.assumptions": list(terms.assumptions)} if terms.assumptions else {}),
        **({"terms.exclusions": list(terms.exclusions)} if terms.exclusions else {}),
        **{
            f"terms.components.{component.component_type.value}": component.model_dump(
                mode="json"
            )
            for component in terms.components
        },
    }
    for field_path, typed_value in term_facts.items():
        review_service.record_decision(
            SupplierQuoteDecision(
                decision_id=f"{receipt.quote_id}-{field_path}-review",
                quote_id=receipt.quote_id,
                field_path=field_path,
                decision=QuoteDecisionAction.ENTER_MANUAL_VALUE,
                typed_value=typed_value,
                rationale="human confirmed the commercial term against the quote artifact",
                actor_id="quote-reviewer",
                decided_at=NOW + timedelta(hours=4),
                provenance_ref=provenance,
            )
        )
    review_service.record_decision(
        SupplierQuoteDecision(
            decision_id=f"{receipt.quote_id}-manual-review",
            quote_id=receipt.quote_id,
            field_path="lines[0].product_price",
            decision=QuoteDecisionAction.ENTER_MANUAL_VALUE,
            typed_value={"amount": amount, "currency": currency, "basis": basis},
            rationale="human confirmed the supplier assertion against the quote artifact",
            actor_id="quote-reviewer",
            decided_at=NOW + timedelta(hours=4),
            provenance_ref=provenance,
        )
    )
    line = ConfirmedQuoteLine(
        rfq_line_id=package.structured_request.lines[0].line_id,
        supplier_line_ref="supplier-line-1",
        quantity_basis=QuantityBasis(amount=basis, unit="piece"),
        product_price=_price(
            amount=amount,
            currency=currency,
            basis=basis,
            provenance=provenance,
        ),
        provenance_refs=(provenance,),
    )
    return quote_service.confirm_snapshot(
        receipt.quote_id,
        supplier_profile_version_id=package.supplier_profile_version_id,
        product_spec_version_id=package.product_spec_version_id,
        lines=(line,),
        terms=terms,
        actor_id="quote-reviewer",
        confirmed_at=NOW + timedelta(hours=4),
    )


def test_x_ac_020_to_030_supplier_dry_run_to_quote_comparison(
    tmp_path,
) -> None:
    spec = _frozen_spec()
    supplier_ids = ("supplier-a", "supplier-b", "supplier-c")
    snapshot_ids, qualifications = _qualify_suppliers(spec)
    packages = _build_supplier_packages(
        spec,
        supplier_ids,
        snapshot_ids,
        qualifications,
    )

    quote_repository = InMemorySupplierQuoteRepository()
    package_reader = FixtureSupplierRfqPackageReader(packages)
    intake_service = SupplierQuoteIntakeService(
        CommercialEvidenceAdapter(EvidenceStore(tmp_path / "quote-evidence")),
        quote_repository,
        package_reader,
    )
    quote_service = SupplierQuoteService(
        quote_repository,
        package_reader,
        FixtureProductSpecReader((spec.spec_version_id,)),
    )
    review_service = SupplierQuoteReviewService(quote_repository)
    extraction_service = SupplierQuoteExtractionService(quote_repository)
    formats = (
        (b"%PDF supplier A quote", "quote-a.pdf"),
        (b"PK XLSX supplier B quote", "quote-b.xlsx"),
        (b"Price: USD 35/piece FOB Shanghai", "quote-c.eml"),
    )
    receipts = tuple(
        intake_service.capture(
            supplier_id=supplier_id,
            supplier_rfq_package_id=package.supplier_rfq_package_id,
            content=content,
            filename=filename,
            declared_mime=None,
            idempotency_key=f"quote-intake-{supplier_id}",
            received_at=NOW + timedelta(hours=4),
            quote_received_at=NOW + timedelta(hours=4),
            submitted_by="quote-operator",
        )
        for supplier_id, package, (content, filename) in zip(
            supplier_ids,
            packages,
            formats,
            strict=True,
        )
    )
    provenance_a = _quote_provenance(receipts[0].artifact_refs[0])
    provenance_b = _quote_provenance(receipts[1].artifact_refs[0])
    provenance_c = _quote_provenance(receipts[2].artifact_refs[0])
    quotes = (
        _confirm_quote(
            quote_service,
            extraction_service,
            review_service,
            receipts[0],
            packages[0],
            amount="250",
            currency="CNY",
            basis="1",
            incoterm=IncotermCode.EXW,
            named_place="Factory A",
            packaging=PriceComponent(
                component_type=ComponentType.PACKAGING,
                inclusion_status=InclusionStatus.INCLUDED,
                provenance_ref=provenance_a,
            ),
        ),
        _confirm_quote(
            quote_service,
            extraction_service,
            review_service,
            receipts[1],
            packages[1],
            amount="23000",
            currency="CNY",
            basis="100",
            incoterm=IncotermCode.EXW,
            named_place="Factory B",
            packaging=PriceComponent(
                component_type=ComponentType.PACKAGING,
                inclusion_status=InclusionStatus.QUOTED_SEPARATELY,
                amount=MonetaryAmount(amount="1200", currency="CNY"),
                quantity_basis=QuantityBasis(amount="100", unit="piece"),
                provenance_ref=provenance_b,
            ),
        ),
        _confirm_quote(
            quote_service,
            extraction_service,
            review_service,
            receipts[2],
            packages[2],
            amount="35",
            currency="USD",
            basis="1",
            incoterm=IncotermCode.FOB,
            named_place="Shanghai",
            packaging=PriceComponent(
                component_type=ComponentType.PACKAGING,
                inclusion_status=InclusionStatus.INCLUDED,
                provenance_ref=provenance_c,
            ),
        ),
    )
    fx = FxSnapshot(
        from_currency="USD",
        to_currency="CNY",
        rate="7.2",
        observed_at=NOW,
        source="fixed integration fixture",
        source_version=VersionRef(name="fx-fixture", version="1.0.0"),
        evidence=EvidenceLocator(artifact_id="fx-artifact"),
    )
    fx_reader = InMemoryFxSnapshotReader(
        (FxSnapshotReaderRecord(fx_snapshot_id="fx-1", snapshot=fx),)
    )
    normalization_policy_draft = QuoteNormalizationPolicyVersion(
        normalization_policy_version_id="quote-normalization-v1",
        product_family_id="FE-DRY-01",
        target_currency="CNY",
        target_measurement_unit="piece",
        target_quantity_tiers=(QuantityBasis(amount="100", unit="piece"),),
        allowed_unit_conversion_dimensions=("count",),
        rule_version="1.0.0",
        config_fingerprint="sha256:placeholder",
    )
    normalization_policy = normalization_policy_draft.model_copy(
        update={
            "config_fingerprint": policy_content_fingerprint(normalization_policy_draft)
        }
    )
    normalization = QuoteNormalizationService(
        PintUnitConverter(),
        fx_reader,
    ).normalize(
        quotes,
        policy=normalization_policy,
        as_of=NOW + timedelta(hours=5),
        fx_snapshot_id="fx-1",
    )
    normalized_by_quote = {
        line.supplier_quote_snapshot_id: line for line in normalization.normalized_lines
    }
    assert normalized_by_quote[quotes[0].supplier_quote_snapshot_id].calculation_value == Decimal(
        "250"
    )
    assert normalized_by_quote[quotes[1].supplier_quote_snapshot_id].calculation_value == Decimal(
        "230"
    )
    assert normalized_by_quote[quotes[2].supplier_quote_snapshot_id].calculation_value == Decimal(
        "252.0"
    )

    comparison_service = QuoteComparisonService(quote_repository)
    matrix, issues = comparison_service.compare(
        quotes,
        normalization=normalization,
        as_of=NOW + timedelta(hours=5),
    )
    assert matrix.disposition == "not_comparable"
    assert any(issue.code == "INCOTERM_CODE_MISMATCH" for issue in issues)
    assert (
        quotes[1].supplier_quote_snapshot_id,
        ComponentType.PACKAGING,
        InclusionStatus.QUOTED_SEPARATELY,
    ) not in matrix.unresolved_cost_components
    assert all(value[1] is not ComponentType.PRODUCT for value in matrix.unresolved_cost_components)
    dumped = matrix.model_dump(mode="json")
    assert "winner" not in dumped
    assert "selected_supplier_id" not in dumped
    assert "approved" not in dumped
    repeated, repeated_issues = comparison_service.compare(
        quotes,
        normalization=normalization,
        as_of=NOW + timedelta(hours=5),
    )
    assert repeated.content_fingerprint == matrix.content_fingerprint
    assert tuple(item.issue_id for item in repeated_issues) == tuple(
        item.issue_id for item in issues
    )

    # Continue one exact supplier path through EP07-EP10. No lowest-price auto-selection occurs.
    flow_at = NOW + timedelta(hours=6)
    normalization_a = QuoteNormalizationService(PintUnitConverter(), fx_reader).normalize(
        (quotes[0],),
        policy=normalization_policy,
        as_of=flow_at,
        fx_snapshot_id="fx-1",
    )
    comparison_a, _comparison_issues_a = comparison_service.compare(
        (quotes[0],), normalization=normalization_a, as_of=flow_at
    )
    cost_sources = InMemoryCostingSourceReader()
    cost_sources.save_quote_snapshot(quotes[0])
    cost_sources.save_normalization_run(normalization_a)
    cost_sources.save_product_family(spec.spec_version_id, "FE-DRY-01")
    cost_repository = InMemoryCostScenarioRepository()
    cost_policy_draft = CostScenarioPolicyVersion(
        cost_scenario_policy_version_id="cost-policy-v1",
        product_family_id="FE-DRY-01",
        target_currency="CNY",
        target_margin_rate=Decimal("0.20"),
        required_cost_components=(CostComponentKind.SUPPLIER_PRODUCT,),
        published_at=NOW,
        rule_version="1.0.0",
        config_fingerprint="sha256:placeholder",
    )
    cost_policy = cost_policy_draft.model_copy(
        update={"config_fingerprint": cost_policy_fingerprint(cost_policy_draft)}
    )
    cost_service = CostScenarioService(cost_repository, cost_sources, PintUnitConverter())
    cost = cost_service.build(
        quote_snapshot=quotes[0],
        normalization_run=normalization_a,
        rfq_line_id=quotes[0].confirmed_lines[0].rfq_line_id,
        target_quantity=QuantityBasis(amount="100", unit="piece"),
        policy=cost_policy,
        cost_inputs=(),
        as_of=flow_at,
        proposed_sale_price=MonetaryAmount(amount="400", currency="CNY"),
    )
    assert cost.landed_cost_per_unit == MonetaryAmount(amount="250", currency="CNY")

    qualification = qualifications.snapshot_as_of(
        supplier_ids[0], spec.spec_version_id, flow_at
    )
    assert qualification is not None
    risk_inputs = RiskAssessmentInput(
        product_spec=spec,
        qualification=qualification,
        quote_snapshots=(quotes[0],),
        normalization_run=normalization_a,
        comparison_matrix=comparison_a,
        cost_scenarios=(cost,),
        license_allowed=True,
        quote_quarantine_statuses=("clear",),
    )
    risk_policy_draft = RiskPolicyVersion(
        policy_id="commercial-risk",
        policy_version="1.0.0",
        product_family_id="FE-DRY-01",
        effective_from=NOW,
        effective_until=NOW + timedelta(days=10),
        rule_version="1.0.0",
        warning_codes=("PAYMENT_TERMS_MISSING",),
        allowed_human_roles=("risk_reviewer",),
        minimum_contribution_margin_rate=Decimal("0.20"),
        config_fingerprint="sha256:placeholder",
    )
    risk_policy = risk_policy_draft.model_copy(
        update={"config_fingerprint": risk_policy_fingerprint(risk_policy_draft)}
    )
    journal = AppendOnlyAuditLog()
    system_identity = Identity(
        actor_id="system",
        roles=frozenset({Role.VIEWER}),
        client_identity="ep08",
    )
    risk_repository = InMemoryTradeRiskRepository()
    risk_spec_repository = InMemoryProductSpecRepository()
    risk_spec_repository.save_version(spec)
    risk_source = RepositoryTradeRiskSourceReader(
        risk_inputs,
        product_specs=risk_spec_repository,
        qualifications=qualifications,
        quotes=quote_repository,
        normalizations=cost_sources,
        costs=cost_repository,
    )
    risk_service = TradeRiskService(
        risk_repository,
        source_reader=risk_source,
        audit=GovernanceRiskAuditAdapter(journal, (system_identity,)),
    )
    risk = risk_service.assess(risk_inputs, risk_policy, as_of=flow_at)
    assert risk.disposition.value != "blocked"
    forged_cost = cost.model_copy(update={"contribution_margin_rate": Decimal("-0.50")})
    with pytest.raises(ValueError, match="repository-saved"):
        risk_service.assess(
            risk_inputs.model_copy(update={"cost_scenarios": (forged_cost,)}),
            risk_policy,
            as_of=flow_at,
        )

    cost_reader = CostScenarioDependencyReader(cost_repository)
    risk_reader = RiskAssessmentDependencyReader(risk_repository)
    readiness_repository = InMemoryOperationalReadinessRepository()
    readiness_service = OperationalReadinessService(readiness_repository)
    readiness_item = ReadinessItem(
        item_id="internal_quote_controls_ready",
        state=ReadinessItemState.VERIFIED,
        evidence_refs=(_provenance("readiness-evidence"),),
        verified_at=NOW,
    )
    readiness_draft = OperationalReadinessSnapshot(
        readiness_snapshot_id="readiness-v1",
        resource_id=spec.spec_version_id,
        observed_at=NOW,
        items=(readiness_item,),
        content_fingerprint="sha256:placeholder",
    )
    readiness_snapshot = readiness_draft.model_copy(
        update={"content_fingerprint": readiness_snapshot_fingerprint(readiness_draft)}
    )
    readiness_service.save_snapshot(readiness_snapshot)
    required = {
        OperationalCapability.INTERNAL_QUOTE_DRY_RUN: ("internal_quote_controls_ready",)
    }
    readiness_valid_from = NOW
    readiness_valid_until = NOW + timedelta(days=1)
    readiness_policy = OperationalReadinessPolicyVersion(
        policy_id="readiness",
        version="1.0.0",
        required_items=required,
        production_actions_enabled=False,
        valid_from=readiness_valid_from,
        valid_until=readiness_valid_until,
        config_fingerprint=stable_fingerprint(
            {
                "policy_id": "readiness",
                "version": "1.0.0",
                "required_items": required,
                "production_actions_enabled": False,
                "valid_from": readiness_valid_from,
                "valid_until": readiness_valid_until,
            }
        ),
    )
    evaluation = readiness_service.evaluate(
        readiness_snapshot_id=readiness_snapshot.readiness_snapshot_id,
        policy=readiness_policy,
        capability=OperationalCapability.INTERNAL_QUOTE_DRY_RUN,
        resource_fingerprint=spec.fingerprint,
        as_of=flow_at + timedelta(minutes=1),
    )

    clock = [flow_at + timedelta(minutes=2)]
    maker = Identity(
        actor_id="maker", roles=frozenset({Role.OPERATOR}), client_identity="integration"
    )
    reviewer = Identity(
        actor_id="reviewer", roles=frozenset({Role.REVIEWER}), client_identity="integration"
    )
    approver = Identity(
        actor_id="approver", roles=frozenset({Role.APPROVER}), client_identity="integration"
    )
    admin = Identity(
        actor_id="admin", roles=frozenset({Role.ADMIN}), client_identity="integration"
    )
    identities = (maker, reviewer, approver, admin)
    authorization = GovernanceExecutionAuthorizationAdapter(PolicyEngine(), identities)
    approvals = ApprovalWorkflowService(
        readiness_repository,
        cost_reader=cost_reader,
        risk_reader=risk_reader,
        authorization=authorization,
        audit=GovernanceApprovalAuditAdapter(journal, identities),
        clock=lambda: clock[0],
    )
    request = approvals.request(
        evaluation_id=evaluation.readiness_evaluation_id,
        cost_scenario_id=cost.cost_scenario_snapshot_id,
        cost_scenario_fingerprint=cost.content_fingerprint,
        risk_assessment_id=risk.assessment_id,
        risk_assessment_fingerprint=risk.result_fingerprint,
        maker_id=maker.actor_id,
        expires_at=flow_at + timedelta(days=1),
        idempotency_key="approval-request-1",
        correlation_id="approval-request-1",
    )
    with pytest.raises(PermissionError):
        authorization.require(
            admin.actor_id, "execution.approval.approval", spec.spec_version_id, clock[0]
        )
    clock[0] += timedelta(minutes=1)
    approvals.decide(
        request.approval_request_id,
        stage=ApprovalStage.REVIEW,
        outcome=ApprovalOutcome.APPROVED,
        actor_id=reviewer.actor_id,
        rationale="exact evidence basis reviewed",
        idempotency_key="approval-review-1",
        correlation_id="approval-review-1",
    )
    before_final_approval = clock[0]
    clock[0] += timedelta(minutes=1)
    approvals.decide(
        request.approval_request_id,
        stage=ApprovalStage.APPROVAL,
        outcome=ApprovalOutcome.APPROVED,
        actor_id=approver.actor_id,
        rationale="independent approval granted",
        idempotency_key="approval-final-1",
        correlation_id="approval-final-1",
    )
    assert not approvals.is_effectively_approved(
        request.approval_request_id, before_final_approval
    )
    assert approvals.is_effectively_approved(request.approval_request_id, clock[0])

    product_ref = InputReference(
        stage="ep03",
        snapshot_id=spec.spec_version_id,
        content_fingerprint=spec.fingerprint,
        as_of=spec.created_at,
    )
    rfq_ref = InputReference(
        stage="ep05",
        snapshot_id=packages[0].supplier_rfq_package_id,
        content_fingerprint=packages[0].content_fingerprint,
        as_of=packages[0].created_at,
    )
    customer_repository = InMemoryCustomerQuoteRepository()
    customer_service = CustomerQuoteService(
        customer_repository,
        input_reader=InMemoryCustomerInputReferenceReader((product_ref, rfq_ref)),
        cost_reader=cost_reader,
        risk_reader=risk_reader,
        approvals=approvals,
        renderer=JinjaCustomerQuoteRenderer(),
        clock=lambda: clock[0],
    )
    package_key = "customer-dry-run-1"
    customer_service.record_cost_selection(
        CustomerCostScenarioSelection(
            selection_id="cost-selection-1",
            package_key=package_key,
            cost_scenario_id=cost.cost_scenario_snapshot_id,
            cost_scenario_fingerprint=cost.content_fingerprint,
            actor_id="commercial-reviewer",
            actor_role="commercial_reviewer",
            rationale="human selected the complete scenario",
            provenance_ref=_provenance("customer-cost-selection"),
            decided_at=clock[0],
        )
    )
    customer_line = CustomerQuoteLine(
        line_id="customer-line-1",
        description="Industrial dry-dust filter element",
        quantity=Decimal("100"),
        unit="piece",
        unit_price=MonetaryAmount(amount="400", currency="CNY"),
    )
    customer_terms = CustomerQuoteTerms(
        incoterm=Incoterm(
            code=IncotermCode.FOB,
            rules_version="Incoterms 2020",
            named_place="Shanghai",
        ),
        valid_from=clock[0],
        valid_until=clock[0] + timedelta(days=7),
        payment_terms="T/T subject to final contract",
        delivery_terms="30 days after confirmed trigger",
        assumptions=("Final technical drawing remains subject to confirmation",),
        exclusions=("No order or shipment is created by this draft",),
    )
    for index, (path, value) in enumerate(
        CustomerQuoteService._commercial_facts(
            (customer_line,), customer_terms, ("Internal draft only; not sent",)
        ).items()
    ):
        customer_service.record_commercial_decision(
            CustomerCommercialDecision(
                decision_id=f"customer-decision-{index}",
                package_key=package_key,
                field_path=path,
                action=CustomerCommercialDecisionAction.CONFIRM,
                value=value,
                actor_id="commercial-reviewer",
                actor_role="commercial_reviewer",
                rationale="customer-facing value confirmed manually",
                provenance_ref=_provenance(f"customer-decision-{index}"),
                decided_at=clock[0],
            )
        )
    customer_valid_from = NOW
    customer_valid_until = NOW + timedelta(days=1)
    required_customer_terms = (
        "terms.incoterm",
        "terms.validity",
        "terms.payment_terms",
        "terms.delivery_terms",
        "terms.assumptions",
        "terms.exclusions",
    )
    customer_policy = CustomerQuotePolicyVersion(
        policy_id="customer-quote",
        policy_version="1.0.0",
        required_disclosures=("Internal draft only; not sent",),
        required_term_paths=required_customer_terms,
        allow_budgetary_only=False,
        valid_from=customer_valid_from,
        valid_until=customer_valid_until,
        config_fingerprint=stable_fingerprint(
            {
                "policy_id": "customer-quote",
                "policy_version": "1.0.0",
                "required_disclosures": ("Internal draft only; not sent",),
                "required_term_paths": required_customer_terms,
                "allow_budgetary_only": False,
                "allowed_human_roles": ("commercial_reviewer",),
                "valid_from": customer_valid_from,
                "valid_until": customer_valid_until,
            }
        ),
    )
    customer_package = customer_service.build_package(
        package_key=package_key,
        customer_alias="Customer A",
        product_spec=product_ref,
        rfq=rfq_ref,
        cost_scenario_id=cost.cost_scenario_snapshot_id,
        risk_assessment_id=risk.assessment_id,
        readiness_approval_request_id=request.approval_request_id,
        policy=customer_policy,
        lines=(customer_line,),
        terms=customer_terms,
        disclosures=("Internal draft only; not sent",),
        as_of=clock[0],
        idempotency_key="customer-package-1",
    )
    assert customer_package.status is CustomerQuoteStatus.READY_FOR_HUMAN_APPROVAL
    draft = customer_service.render_local_draft(customer_package.customer_quote_package_id)
    assert draft.quote_artifact_ref.startswith("local-artifact:")
    assert "supplier-a" not in customer_repository.artifact(draft.quote_artifact_ref)

    chain_values = (
        ("ep01", "buyer-rfq-1", "sha256:buyer-rfq", NOW),
        ("ep02", "buyer-rfq-extraction-1", "sha256:buyer-rfq-extraction", NOW),
        ("ep03", spec.spec_version_id, spec.fingerprint, spec.created_at),
        (
            "ep04",
            qualification.qualification_snapshot_id,
            qualification.content_fingerprint,
            qualification.as_of,
        ),
        (
            "ep05",
            packages[0].supplier_rfq_package_id,
            packages[0].content_fingerprint,
            packages[0].created_at,
        ),
        (
            "ep06",
            quotes[0].supplier_quote_snapshot_id,
            quotes[0].content_fingerprint,
            quotes[0].confirmed_at,
        ),
        ("ep07", cost.cost_scenario_snapshot_id, cost.content_fingerprint, cost.as_of),
        ("ep08", risk.assessment_id, risk.result_fingerprint, risk.as_of),
        ("ep09", request.approval_request_id, request.content_fingerprint, request.created_at),
        (
            "ep10",
            customer_package.customer_quote_package_id,
            customer_package.content_fingerprint,
            customer_package.as_of,
        ),
    )
    nodes: list[ExecutionChainNode] = []
    for stage, snapshot_id, fingerprint, occurred_at in chain_values:
        nodes.append(
            ExecutionChainNode(
                stage=stage,
                scope_id=spec.spec_version_id,
                snapshot_id=snapshot_id,
                content_fingerprint=fingerprint,
                occurred_at=occurred_at,
                predecessor_refs=(nodes[-1].snapshot_id,) if nodes else (),
                is_lineage_tip=True,
            )
        )
    audit_verifier = GovernanceAuditChainVerifierAdapter(journal, clock=lambda: clock[0])
    chain = ExecutionChainVerifier(
        InMemoryExecutionChainNodeReader(tuple(nodes)),
        audit_verifier,
        package_reader=customer_repository,
    )
    manifest = chain.build_manifest(customer_package, nodes=tuple(nodes), as_of=clock[0])
    assert chain.verify(manifest).valid
    assert journal.verify().valid
    assert not hasattr(customer_service, "send")
    revised_cost = cost_service.build(
        quote_snapshot=quotes[0],
        normalization_run=normalization_a,
        rfq_line_id=quotes[0].confirmed_lines[0].rfq_line_id,
        target_quantity=QuantityBasis(amount="100", unit="piece"),
        policy=cost_policy,
        cost_inputs=(),
        as_of=flow_at,
        proposed_sale_price=MonetaryAmount(amount="410", currency="CNY"),
        supersedes=cost.cost_scenario_snapshot_id,
    )
    assert revised_cost.supersedes == cost.cost_scenario_snapshot_id
    assert not approvals.is_effectively_approved(request.approval_request_id, clock[0])
