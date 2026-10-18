from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from jinja2.exceptions import SecurityError, UndefinedError

from trade_alpha.contracts.common import EvidenceLocator, VersionRef
from trade_alpha.execution.adapters.jinja_customer_quote_renderer import JinjaCustomerQuoteRenderer
from trade_alpha.execution.contracts import Incoterm, IncotermCode, MonetaryAmount, ProvenanceRef
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.customer_quotes import (
    CustomerCommercialDecision,
    CustomerCommercialDecisionAction,
    CustomerCostScenarioSelection,
    CustomerQuoteLine,
    CustomerQuotePolicyVersion,
    CustomerQuoteRenderView,
    CustomerQuoteService,
    CustomerQuoteStatus,
    CustomerQuoteTerms,
    ExecutionChainNode,
    ExecutionChainVerifier,
    InMemoryCustomerInputReferenceReader,
    InMemoryCustomerQuoteRepository,
    InMemoryExecutionChainNodeReader,
    InputReference,
    budgetary_no_approval_binding,
    customer_quote_policy_fingerprint,
)
from trade_alpha.execution.operational_readiness import (
    ApprovalOutcome,
    ApprovalStage,
    ApprovalWorkflowService,
    ExecutionDependencyKind,
    ExecutionDependencySnapshot,
    InMemoryOperationalReadinessRepository,
    OperationalCapability,
    OperationalReadinessPolicyVersion,
    OperationalReadinessService,
    OperationalReadinessSnapshot,
    ReadinessItem,
    ReadinessItemState,
)

NOW = datetime(2026, 8, 27, tzinfo=UTC)


class Reader:
    def __init__(self, value: ExecutionDependencySnapshot) -> None:
        self.value = value

    def get_saved_as_of(self, value_id: str, as_of: datetime) -> ExecutionDependencySnapshot | None:
        return (
            self.value
            if self.value.dependency_id == value_id and self.value.as_of <= as_of
            else None
        )


class Authorization:
    def require(self, actor_id: str, action: str, resource_id: str, at: datetime) -> None:
        return None


class Audit:
    def append(self, **kwargs: object) -> str:  # type: ignore[no-untyped-def]
        return stable_fingerprint(kwargs)

    def verify(self) -> bool:
        return True

    def tip_hash(self) -> str:
        return "sha256:audit-tip"

    def verified_at(self) -> datetime:
        return NOW


def provenance() -> ProvenanceRef:
    return ProvenanceRef(
        source="customer quote fixture",
        evidence=EvidenceLocator(artifact_id="customer-quote-evidence"),
        observed_at=NOW,
        source_version=VersionRef(name="customer-quote-fixture", version="1"),
    )


def readiness_approval(
    cost: ExecutionDependencySnapshot, risk: ExecutionDependencySnapshot
) -> ApprovalWorkflowService:
    item = ReadinessItem(
        item_id="legal_entity_registered",
        state=ReadinessItemState.VERIFIED,
        evidence_refs=(
            ProvenanceRef(
                source="fixture",
                evidence=EvidenceLocator(artifact_id="e"),
                observed_at=NOW,
                source_version=VersionRef(name="f", version="1"),
            ),
        ),
        verified_at=NOW,
    )
    snapshot = OperationalReadinessSnapshot(
        readiness_snapshot_id="ops",
        resource_id="spec",
        observed_at=NOW,
        items=(item,),
        content_fingerprint=stable_fingerprint(
            {"resource_id": "spec", "observed_at": NOW, "items": (item,)}
        ),
    )
    required = {OperationalCapability.INTERNAL_QUOTE_DRY_RUN: ("legal_entity_registered",)}
    valid_from, valid_until = NOW - timedelta(days=1), NOW + timedelta(days=1)
    policy = OperationalReadinessPolicyVersion(
        policy_id="ops",
        version="1",
        required_items=required,
        production_actions_enabled=False,
        valid_from=valid_from,
        valid_until=valid_until,
        config_fingerprint=stable_fingerprint(
            {
                "policy_id": "ops",
                "version": "1",
                "required_items": required,
                "production_actions_enabled": False,
                "valid_from": valid_from,
                "valid_until": valid_until,
            }
        ),
    )
    repository = InMemoryOperationalReadinessRepository()
    service = OperationalReadinessService(repository)
    service.save_snapshot(snapshot)
    evaluation = service.evaluate(
        readiness_snapshot_id="ops",
        policy=policy,
        capability=OperationalCapability.INTERNAL_QUOTE_DRY_RUN,
        resource_fingerprint="spec-fp",
        as_of=NOW,
    )
    flow = ApprovalWorkflowService(
        repository,
        cost_reader=Reader(cost),
        risk_reader=Reader(risk),
        authorization=Authorization(),
        audit=Audit(),
        clock=lambda: NOW,
    )
    request = flow.request(
        evaluation_id=evaluation.readiness_evaluation_id,
        cost_scenario_id=cost.dependency_id,
        cost_scenario_fingerprint=cost.content_fingerprint,
        risk_assessment_id=risk.dependency_id,
        risk_assessment_fingerprint=risk.content_fingerprint,
        maker_id="maker",
        expires_at=NOW + timedelta(hours=2),
        idempotency_key="m",
        correlation_id="m",
    )
    flow.decide(
        request.approval_request_id,
        stage=ApprovalStage.REVIEW,
        outcome=ApprovalOutcome.APPROVED,
        actor_id="reviewer",
        rationale="ok",
        idempotency_key="r",
        correlation_id="r",
    )
    flow.decide(
        request.approval_request_id,
        stage=ApprovalStage.APPROVAL,
        outcome=ApprovalOutcome.APPROVED,
        actor_id="approver",
        rationale="ok",
        idempotency_key="a",
        correlation_id="a",
    )
    return flow


def test_customer_quote_is_manually_bound_rendered_locally_and_chain_verified() -> None:
    cost = ExecutionDependencySnapshot(
        dependency_id="cost",
        kind=ExecutionDependencyKind.COST,
        content_fingerprint="cost-fp",
        as_of=NOW,
        valid_until=NOW + timedelta(days=1),
        complete=True,
    )
    risk = ExecutionDependencySnapshot(
        dependency_id="risk",
        kind=ExecutionDependencyKind.RISK,
        content_fingerprint="risk-fp",
        as_of=NOW,
        valid_until=NOW + timedelta(days=1),
    )
    approvals = readiness_approval(cost, risk)
    required_terms = (
        "terms.incoterm",
        "terms.validity",
        "terms.payment_terms",
        "terms.delivery_terms",
        "terms.assumptions",
        "terms.exclusions",
    )
    valid_from, valid_until = NOW - timedelta(days=1), NOW + timedelta(days=1)
    policy = CustomerQuotePolicyVersion(
        policy_id="customer",
        policy_version="1",
        required_disclosures=("Internal draft only",),
        required_term_paths=required_terms,
        allow_budgetary_only=False,
        valid_from=valid_from,
        valid_until=valid_until,
        config_fingerprint=stable_fingerprint(
            {
                "policy_id": "customer",
                "policy_version": "1",
                "required_disclosures": ("Internal draft only",),
                "required_term_paths": required_terms,
                "allow_budgetary_only": False,
                "allowed_human_roles": ("commercial_reviewer",),
                "valid_from": valid_from,
                "valid_until": valid_until,
            }
        ),
    )
    product = InputReference(
        stage="ep03", snapshot_id="spec", content_fingerprint="spec-fp", as_of=NOW
    )
    rfq = InputReference(
        stage="ep05", snapshot_id="rfq", content_fingerprint="rfq-fp", as_of=NOW
    )
    service = CustomerQuoteService(
        InMemoryCustomerQuoteRepository(),
        input_reader=InMemoryCustomerInputReferenceReader((product, rfq)),
        cost_reader=Reader(cost),
        risk_reader=Reader(risk),
        approvals=approvals,
        renderer=JinjaCustomerQuoteRenderer(),
        clock=lambda: NOW,
    )
    selection = CustomerCostScenarioSelection(
        selection_id="select",
        package_key="customer-1",
        cost_scenario_id="cost",
        cost_scenario_fingerprint="cost-fp",
        actor_id="seller",
        actor_role="commercial_reviewer",
        rationale="human selected scenario",
        provenance_ref=provenance(),
        decided_at=NOW,
    )
    service.record_cost_selection(selection)
    line = CustomerQuoteLine(
        line_id="line",
        description="Filter element",
        quantity=Decimal("10"),
        unit="piece",
        unit_price=MonetaryAmount(amount="12", currency="USD"),
    )
    terms = CustomerQuoteTerms(
        incoterm=Incoterm(code=IncotermCode.FOB, rules_version="2020", named_place="Shanghai"),
        valid_from=NOW,
        valid_until=NOW + timedelta(days=1),
        payment_terms="T/T",
        delivery_terms="30 days",
        assumptions=("Subject to final technical confirmation",),
        exclusions=("No shipment commitment",),
    )
    facts = CustomerQuoteService._commercial_facts(
        (line,), terms, ("Internal draft only",)
    )
    for index, (path, value) in enumerate(facts.items()):
        service.record_commercial_decision(
            CustomerCommercialDecision(
                decision_id=f"d{index}",
                package_key="customer-1",
                field_path=path,
                action=CustomerCommercialDecisionAction.CONFIRM,
                value=value,
                actor_id="seller",
                actor_role="commercial_reviewer",
                rationale="human confirmed",
                provenance_ref=provenance(),
                decided_at=NOW,
            )
        )
    request_id = next(iter(approvals.repository._requests))  # type: ignore[attr-defined]
    package = service.build_package(
        package_key="customer-1",
        customer_alias="Customer A",
        product_spec=product,
        rfq=rfq,
        cost_scenario_id="cost",
        risk_assessment_id="risk",
        readiness_approval_request_id=request_id,
        policy=policy,
        lines=(line,),
        terms=terms,
        disclosures=("Internal draft only",),
        as_of=NOW,
        idempotency_key="package-1",
    )
    assert package.status is CustomerQuoteStatus.READY_FOR_HUMAN_APPROVAL
    draft = service.render_local_draft(package.customer_quote_package_id)
    assert draft.quote_artifact_ref.startswith("local-artifact:")
    assert "cost" not in service.repository.artifact(draft.quote_artifact_ref).lower()  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="idempotency"):
        service.build_package(
            package_key="customer-1",
            customer_alias="Changed",
            product_spec=product,
            rfq=rfq,
            cost_scenario_id="cost",
            risk_assessment_id="risk",
            readiness_approval_request_id=request_id,
            policy=policy,
            lines=(line,),
            terms=terms,
            disclosures=("Internal draft only",),
            as_of=NOW,
            idempotency_key="package-1",
        )
    approval_request = approvals.approval_request(request_id)
    assert approval_request is not None
    chain_bindings = {
        3: (product.snapshot_id, product.content_fingerprint),
        5: (rfq.snapshot_id, rfq.content_fingerprint),
        7: (cost.dependency_id, cost.content_fingerprint),
        8: (risk.dependency_id, risk.content_fingerprint),
        9: (approval_request.approval_request_id, approval_request.content_fingerprint),
        10: (package.customer_quote_package_id, package.content_fingerprint),
    }
    nodes_list: list[ExecutionChainNode] = []
    for i in range(1, 11):
        snapshot_id, content_fingerprint = chain_bindings.get(i, (f"n{i}", f"f{i}"))
        nodes_list.append(
            ExecutionChainNode(
                stage=f"ep{i:02d}",
                scope_id="spec",
                snapshot_id=snapshot_id,
                content_fingerprint=content_fingerprint,
                occurred_at=NOW,
                predecessor_refs=(nodes_list[-1].snapshot_id,) if nodes_list else (),
                is_lineage_tip=True,
            )
        )
    nodes = tuple(nodes_list)
    verifier = ExecutionChainVerifier(
        InMemoryExecutionChainNodeReader(nodes), Audit(), package_reader=service.repository
    )
    manifest = verifier.build_manifest(package, nodes=nodes, as_of=NOW)
    assert verifier.verify(manifest).valid
    broken_nodes = (
        *nodes[:-1],
        nodes[-1].model_copy(update={"predecessor_refs": ()}),
    )
    broken_verifier = ExecutionChainVerifier(
        InMemoryExecutionChainNodeReader(broken_nodes),
        Audit(),
        package_reader=service.repository,
    )
    broken = broken_verifier.build_manifest(package, nodes=broken_nodes, as_of=NOW)
    assert "chain_predecessor_binding_invalid" in broken_verifier.verify(broken).issues
    unrelated_nodes = tuple(
        node.model_copy(update={"snapshot_id": "unrelated-cost", "content_fingerprint": "fake"})
        if node.stage == "ep07"
        else node
        for node in nodes
    )
    unrelated_verifier = ExecutionChainVerifier(
        InMemoryExecutionChainNodeReader(unrelated_nodes),
        Audit(),
        package_reader=service.repository,
    )
    unrelated = unrelated_verifier.build_manifest(package, nodes=unrelated_nodes, as_of=NOW)
    assert "ep07_package_binding_mismatch" in unrelated_verifier.verify(unrelated).issues

    view = CustomerQuoteRenderView(
        customer_alias=package.customer_alias,
        lines=package.lines,
        terms=package.terms,
        disclosures=package.disclosures,
    )
    with pytest.raises(UndefinedError):
        JinjaCustomerQuoteRenderer(quote_template="{{ cost_scenario }}").render_quote(view)
    with pytest.raises(SecurityError):
        JinjaCustomerQuoteRenderer(quote_template="{{ lines[0].__class__ }}").render_quote(
            view
        )

    incomplete_cost = cost.model_copy(update={"complete": False, "hard_block": True})
    budget_service = CustomerQuoteService(
        InMemoryCustomerQuoteRepository(),
        input_reader=InMemoryCustomerInputReferenceReader((product, rfq)),
        cost_reader=Reader(incomplete_cost),
        risk_reader=Reader(risk),
        approvals=approvals,
        renderer=JinjaCustomerQuoteRenderer(),
        clock=lambda: NOW,
    )
    budget_key = "customer-budget"
    budget_service.record_cost_selection(
        selection.model_copy(
            update={
                "selection_id": "select-budget",
                "package_key": budget_key,
                "cost_scenario_fingerprint": incomplete_cost.content_fingerprint,
            }
        )
    )
    disclosure = "uncertainty: final freight cost is not yet known"
    for index, (path, value) in enumerate(
        CustomerQuoteService._commercial_facts((line,), terms, (disclosure,)).items()
    ):
        budget_service.record_commercial_decision(
            CustomerCommercialDecision(
                decision_id=f"budget-d{index}",
                package_key=budget_key,
                field_path=path,
                action=CustomerCommercialDecisionAction.CONFIRM,
                value=value,
                actor_id="seller",
                actor_role="commercial_reviewer",
                rationale="human confirmed disclosed budgetary value",
                provenance_ref=provenance(),
                decided_at=NOW,
            )
        )
    budget_policy_draft = policy.model_copy(
        update={
            "required_disclosures": (disclosure,),
            "allow_budgetary_only": True,
            "config_fingerprint": "pending",
        }
    )
    budget_policy = budget_policy_draft.model_copy(
        update={
            "config_fingerprint": customer_quote_policy_fingerprint(budget_policy_draft)
        }
    )
    budget = budget_service.build_package(
        package_key=budget_key,
        customer_alias="Customer A",
        product_spec=product,
        rfq=rfq,
        cost_scenario_id=incomplete_cost.dependency_id,
        risk_assessment_id=risk.dependency_id,
        readiness_approval_request_id=None,
        policy=budget_policy,
        lines=(line,),
        terms=terms,
        disclosures=(disclosure,),
        as_of=NOW,
        idempotency_key="budget-package",
    )
    assert budget.status is CustomerQuoteStatus.BUDGETARY_ONLY
    budget_draft = budget_service.render_local_draft(budget.customer_quote_package_id)
    assert budget_draft.disposition.value == "rendered_not_sent"
    budget_ep09 = budgetary_no_approval_binding(budget)
    budget_bindings = {
        3: (product.snapshot_id, product.content_fingerprint),
        5: (rfq.snapshot_id, rfq.content_fingerprint),
        7: (incomplete_cost.dependency_id, incomplete_cost.content_fingerprint),
        8: (risk.dependency_id, risk.content_fingerprint),
        9: budget_ep09,
        10: (budget.customer_quote_package_id, budget.content_fingerprint),
    }
    budget_nodes: list[ExecutionChainNode] = []
    for i in range(1, 11):
        snapshot_id, content_fingerprint = budget_bindings.get(i, (f"budget-n{i}", f"budget-f{i}"))
        budget_nodes.append(
            ExecutionChainNode(
                stage=f"ep{i:02d}",
                scope_id=product.snapshot_id,
                snapshot_id=snapshot_id,
                content_fingerprint=content_fingerprint,
                occurred_at=NOW,
                predecessor_refs=(budget_nodes[-1].snapshot_id,) if budget_nodes else (),
                is_lineage_tip=True,
            )
        )
    budget_verifier = ExecutionChainVerifier(
        InMemoryExecutionChainNodeReader(tuple(budget_nodes)),
        Audit(),
        package_reader=budget_service.repository,
    )
    budget_manifest = budget_verifier.build_manifest(
        budget, nodes=tuple(budget_nodes), as_of=NOW
    )
    assert budget_verifier.verify(budget_manifest).valid


def test_customer_renderer_cannot_access_internal_package_fields() -> None:
    renderer = JinjaCustomerQuoteRenderer(quote_template="{{ cost_scenario }}")
    assert "cost_scenario" not in renderer.template_fingerprint
