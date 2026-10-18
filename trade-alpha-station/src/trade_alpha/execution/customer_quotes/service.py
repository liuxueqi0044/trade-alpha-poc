"""EP-10 local customer quote package, draft and execution-chain services."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.operational_readiness import (
    ApprovalRequestStatus,
    ApprovalWorkflowService,
)
from trade_alpha.execution.operational_readiness.models import (
    ExecutionDependencyKind,
    ExecutionDependencySnapshot,
)
from trade_alpha.execution.operational_readiness.ports import ExecutionDependencyReader

from .models import (
    ChainVerificationResult,
    CustomerCommercialDecision,
    CustomerCommercialDecisionAction,
    CustomerCommunicationDraft,
    CustomerCostScenarioSelection,
    CustomerDraftDisposition,
    CustomerQuoteLine,
    CustomerQuotePackageSnapshot,
    CustomerQuotePolicyVersion,
    CustomerQuoteRenderView,
    CustomerQuoteStatus,
    CustomerQuoteTerms,
    ExecutionChainManifest,
    ExecutionChainNode,
    InputReference,
    budgetary_no_approval_binding,
    customer_quote_policy_fingerprint,
)
from .ports import (
    AuditChainVerifierPort,
    CustomerInputReferenceReader,
    CustomerQuotePackageReader,
    CustomerQuoteRendererPort,
    ExecutionChainNodeReader,
)
from .repository import InMemoryCustomerQuoteRepository


class CustomerQuoteService:
    def __init__(
        self,
        repository: InMemoryCustomerQuoteRepository,
        *,
        input_reader: CustomerInputReferenceReader,
        cost_reader: ExecutionDependencyReader,
        risk_reader: ExecutionDependencyReader,
        approvals: ApprovalWorkflowService,
        renderer: CustomerQuoteRendererPort,
        clock: Any | None = None,
    ) -> None:
        self.repository = repository
        self.input_reader = input_reader
        self.cost_reader = cost_reader
        self.risk_reader = risk_reader
        self.approvals = approvals
        self.renderer = renderer
        self.clock = clock or (lambda: datetime.now(UTC))

    def record_cost_selection(
        self, selection: CustomerCostScenarioSelection
    ) -> CustomerCostScenarioSelection:
        if type(selection) is not CustomerCostScenarioSelection:
            raise TypeError("cost scenario selection has an invalid type")
        return self.repository.save_selection(selection)

    def record_commercial_decision(
        self, decision: CustomerCommercialDecision
    ) -> CustomerCommercialDecision:
        if type(decision) is not CustomerCommercialDecision:
            raise TypeError("commercial decision has an invalid type")
        return self.repository.save_decision(decision)

    def build_package(
        self,
        *,
        package_key: str,
        customer_alias: str,
        product_spec: InputReference,
        rfq: InputReference,
        cost_scenario_id: str,
        risk_assessment_id: str,
        readiness_approval_request_id: str | None,
        policy: CustomerQuotePolicyVersion,
        lines: tuple[CustomerQuoteLine, ...],
        terms: CustomerQuoteTerms,
        disclosures: tuple[str, ...],
        as_of: datetime,
        idempotency_key: str,
        supersedes: str | None = None,
    ) -> CustomerQuotePackageSnapshot:
        moment = require_utc(as_of)
        if type(policy) is not CustomerQuotePolicyVersion:
            raise TypeError("customer quote policy has an invalid type")
        CustomerQuotePolicyVersion.model_validate(policy.model_dump())
        normalized_disclosures = tuple(sorted(set(disclosures)))
        if any(not item.strip() for item in normalized_disclosures):
            raise ValueError("customer quote disclosures cannot be blank")
        if moment < policy.valid_from or (
            policy.valid_until is not None and moment > policy.valid_until
        ):
            raise ValueError("customer quote policy is not valid at as_of")
        if policy.config_fingerprint != customer_quote_policy_fingerprint(policy):
            raise ValueError("customer quote policy config_fingerprint mismatch")
        if type(product_spec) is not InputReference or type(rfq) is not InputReference:
            raise TypeError("product spec and RFQ must be exact input references")
        if product_spec.stage != "ep03" or rfq.stage != "ep05":
            raise ValueError("customer quote requires exact EP03 product and EP05 RFQ references")
        self._input_reference(product_spec, moment)
        self._input_reference(rfq, moment)
        if not lines or len({line.line_id for line in lines}) != len(lines):
            raise ValueError("customer quote requires unique non-empty lines")
        cost = self._dependency(
            self.cost_reader, cost_scenario_id, moment, ExecutionDependencyKind.COST
        )
        risk = self._dependency(
            self.risk_reader, risk_assessment_id, moment, ExecutionDependencyKind.RISK
        )
        request = (
            self.approvals.approval_request(readiness_approval_request_id)
            if readiness_approval_request_id is not None
            else None
        )
        if request is not None:
            if (
                request.resource_id != product_spec.snapshot_id
                or request.resource_fingerprint != product_spec.content_fingerprint
            ):
                raise ValueError(
                    "readiness approval must bind the exact customer quote resource fingerprint"
                )
            if (
                request.cost_scenario_id != cost.dependency_id
                or request.cost_scenario_fingerprint != cost.content_fingerprint
                or request.risk_assessment_id != risk.dependency_id
                or request.risk_assessment_fingerprint != risk.content_fingerprint
            ):
                raise ValueError("readiness approval must bind the exact cost and risk basis")
            request_status = self.approvals.status_as_of(request.approval_request_id, moment)
            request_effective = self.approvals.is_effectively_approved(
                request.approval_request_id, moment
            )
        else:
            request_status = ApprovalRequestStatus.PENDING_REVIEW
            request_effective = False
        selection = self.repository.selection(package_key, moment)
        if selection is not None and (
            selection.cost_scenario_id != cost.dependency_id
            or selection.cost_scenario_fingerprint != cost.content_fingerprint
        ):
            raise ValueError("manual cost selection does not bind the exact cost scenario")
        if selection is not None and (
            selection.actor_role not in policy.allowed_human_roles
            or selection.provenance_ref.observed_at > selection.decided_at
        ):
            raise PermissionError("cost selection role or evidence is not allowed by policy")
        decisions = self._effective_decisions(package_key, moment)
        if any(
            item.actor_role not in policy.allowed_human_roles
            or item.provenance_ref.observed_at > item.decided_at
            for item in decisions
        ):
            raise PermissionError("commercial decision role or evidence is not allowed by policy")
        expected = self._commercial_facts(lines, terms, normalized_disclosures)
        matched = self._matched_decisions(expected, decisions)
        missing = tuple(
            sorted(
                {
                    *set(expected).difference(matched),
                    *set(policy.required_term_paths).difference(expected),
                }
            )
        )
        status = self._status(
            policy=policy,
            cost=cost,
            risk=risk,
            request_status=request_status,
            request_effective=request_effective,
            selection=selection,
            missing=missing,
            disclosures=normalized_disclosures,
        )
        payload = {
            "package_key": package_key,
            "customer_alias": customer_alias,
            "product_spec": product_spec,
            "rfq": rfq,
            "cost": cost,
            "risk": risk,
            "approval": request.content_fingerprint if request else None,
            "policy": policy.config_fingerprint,
            "selection": selection,
            "lines": lines,
            "terms": terms,
            "disclosures": normalized_disclosures,
            "decisions": tuple(sorted(item.decision_id for item in decisions)),
            "status": status,
            "as_of": moment,
            "template": self.renderer.template_fingerprint,
        }
        fingerprint = stable_fingerprint(payload)
        if existing := self.repository.idempotent(idempotency_key, fingerprint):
            if type(existing) is not CustomerQuotePackageSnapshot:
                raise ValueError("idempotency key belongs to another operation")
            return existing
        old = self.repository.packages(package_key)
        same = next((item for item in old if item.content_fingerprint == fingerprint), None)
        if same is not None:
            self.repository.save_idempotent(idempotency_key, fingerprint, same)
            return same
        if old and supersedes != old[-1].customer_quote_package_id:
            raise ValueError("changed customer quote package must supersede current lineage tip")
        if not old and supersedes is not None:
            raise ValueError("first customer quote package cannot supersede")
        value = CustomerQuotePackageSnapshot(
            customer_quote_package_id=str(
                uuid5(NAMESPACE_URL, f"trade-alpha/ep10/package/{fingerprint}")
            ),
            version=len(old) + 1,
            package_key=package_key,
            customer_alias=customer_alias,
            product_spec=product_spec,
            rfq=rfq,
            cost_scenario=cost,
            risk_assessment=risk,
            readiness_approval_request_id=request.approval_request_id if request else None,
            readiness_approval_fingerprint=request.content_fingerprint if request else None,
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            selected_cost_scenario_id=selection.cost_scenario_id if selection else None,
            lines=lines,
            terms=terms,
            disclosures=normalized_disclosures,
            decision_ids=tuple(sorted(item.decision_id for item in decisions)),
            status=status,
            as_of=moment,
            content_fingerprint=fingerprint,
            supersedes=supersedes,
        )
        result = self.repository.save_package(value)
        self.repository.save_idempotent(idempotency_key, fingerprint, result)
        return result

    def render_local_draft(self, package_id: str) -> CustomerCommunicationDraft:
        package = self.repository.package(package_id)
        if package is None:
            raise ValueError("customer quote package not found")
        if package.status not in {
            CustomerQuoteStatus.READY_FOR_HUMAN_APPROVAL,
            CustomerQuoteStatus.BUDGETARY_ONLY,
        }:
            raise ValueError("only ready or disclosed budgetary packages may be rendered")
        view = CustomerQuoteRenderView(
            customer_alias=package.customer_alias,
            lines=package.lines,
            terms=package.terms,
            disclosures=package.disclosures,
        )
        quote = self.renderer.render_quote(view)
        communication = self.renderer.render_communication(view)
        for disclosure in package.disclosures:
            if disclosure not in quote or disclosure not in communication:
                raise ValueError("template omitted required customer disclosure")
        quote_ref = "local-artifact:" + stable_fingerprint({"quote": quote})
        communication_ref = "local-artifact:" + stable_fingerprint({"communication": communication})
        self.repository.save_artifact(quote_ref, quote)
        self.repository.save_artifact(communication_ref, communication)
        fingerprint = stable_fingerprint(
            {
                "package": package.content_fingerprint,
                "template": self.renderer.template_fingerprint,
                "quote": quote_ref,
                "communication": communication_ref,
            }
        )
        return CustomerCommunicationDraft(
            draft_id=str(uuid5(NAMESPACE_URL, f"trade-alpha/ep10/draft/{fingerprint}")),
            customer_quote_package_id=package.customer_quote_package_id,
            customer_quote_package_fingerprint=package.content_fingerprint,
            template_fingerprint=self.renderer.template_fingerprint,
            quote_artifact_ref=quote_ref,
            communication_artifact_ref=communication_ref,
            recipient_reference=package.customer_alias,
            channel_intent="local_draft",
            content_hash=fingerprint,
            disposition=CustomerDraftDisposition.RENDERED_NOT_SENT,
            created_at=require_utc(self.clock()),
        )

    def _dependency(
        self,
        reader: ExecutionDependencyReader,
        value_id: str,
        as_of: datetime,
        kind: ExecutionDependencyKind,
    ) -> ExecutionDependencySnapshot:
        value = reader.get_saved_as_of(value_id, as_of)
        if type(value) is not ExecutionDependencySnapshot:
            raise TypeError("dependency reader returned an invalid snapshot type")
        if (
            value.kind is not kind
            or value.as_of > as_of
            or (value.valid_until is not None and value.valid_until < as_of)
        ):
            raise ValueError("customer quote dependency is not effective at as_of")
        return value

    def _input_reference(self, value: InputReference, as_of: datetime) -> InputReference:
        saved = self.input_reader.get_saved_reference(value.stage, value.snapshot_id, as_of)
        if type(saved) is not InputReference or saved != value:
            raise ValueError("customer quote input must match a repository-saved reference")
        if value.as_of > as_of:
            raise ValueError("customer quote cannot use a future input reference")
        return value

    @staticmethod
    def _commercial_facts(
        lines: tuple[CustomerQuoteLine, ...],
        terms: CustomerQuoteTerms,
        disclosures: tuple[str, ...] = (),
    ) -> dict[str, object]:
        facts: dict[str, object] = {}
        for index, line in enumerate(lines):
            facts[f"lines[{index}].description"] = line.description
            facts[f"lines[{index}].unit_price"] = line.unit_price.model_dump(mode="json")
            facts[f"lines[{index}].quantity"] = {"quantity": str(line.quantity), "unit": line.unit}
        if terms.incoterm is not None:
            facts["terms.incoterm"] = terms.incoterm.model_dump(mode="json")
        if terms.valid_from is not None or terms.valid_until is not None:
            facts["terms.validity"] = {
                "valid_from": terms.valid_from.isoformat() if terms.valid_from else None,
                "valid_until": terms.valid_until.isoformat() if terms.valid_until else None,
            }
        if terms.payment_terms is not None:
            facts["terms.payment_terms"] = terms.payment_terms
        if terms.delivery_terms is not None:
            facts["terms.delivery_terms"] = terms.delivery_terms
        if terms.assumptions:
            facts["terms.assumptions"] = terms.assumptions
        if terms.exclusions:
            facts["terms.exclusions"] = terms.exclusions
        for index, disclosure in enumerate(disclosures):
            facts[f"disclosures[{index}]"] = disclosure
        return facts

    @staticmethod
    def _matched_decisions(
        expected: dict[str, object], decisions: tuple[CustomerCommercialDecision, ...]
    ) -> dict[str, CustomerCommercialDecision]:
        matched: dict[str, CustomerCommercialDecision] = {}
        for decision in decisions:
            if (
                decision.action is CustomerCommercialDecisionAction.CONFIRM
                and decision.field_path in expected
                and stable_fingerprint({"value": decision.value})
                == stable_fingerprint({"value": expected[decision.field_path]})
            ):
                if decision.field_path in matched:
                    raise ValueError("multiple effective commercial decisions exist for one field")
                matched[decision.field_path] = decision
        return matched

    def _effective_decisions(
        self, package_key: str, as_of: datetime
    ) -> tuple[CustomerCommercialDecision, ...]:
        decisions = self.repository.decisions(package_key, as_of)
        by_id = {item.decision_id: item for item in decisions}
        superseded: set[str] = set()
        for item in decisions:
            if item.supersedes_decision_id is not None:
                prior = by_id.get(item.supersedes_decision_id)
                if (
                    prior is None
                    or prior.package_key != item.package_key
                    or prior.field_path != item.field_path
                ):
                    raise ValueError("decision supersedes must reference same package field")
                superseded.add(prior.decision_id)
        return tuple(item for item in decisions if item.decision_id not in superseded)

    @staticmethod
    def _status(
        *,
        policy: CustomerQuotePolicyVersion,
        cost: ExecutionDependencySnapshot,
        risk: ExecutionDependencySnapshot,
        request_status: ApprovalRequestStatus,
        request_effective: bool,
        selection: CustomerCostScenarioSelection | None,
        missing: tuple[str, ...],
        disclosures: tuple[str, ...],
    ) -> CustomerQuoteStatus:
        if risk.hard_block or request_status in {
            ApprovalRequestStatus.REJECTED,
            ApprovalRequestStatus.EXPIRED,
            ApprovalRequestStatus.SUPERSEDED,
        }:
            return CustomerQuoteStatus.BLOCKED
        all_disclosures = set(policy.required_disclosures).issubset(disclosures)
        if not cost.complete:
            if (
                policy.allow_budgetary_only
                and all_disclosures
                and selection is not None
                and not missing
                and any(item.startswith("uncertainty:") for item in disclosures)
            ):
                return CustomerQuoteStatus.BUDGETARY_ONLY
            return CustomerQuoteStatus.INCOMPLETE
        if selection is None or missing or not all_disclosures:
            return CustomerQuoteStatus.INCOMPLETE
        if not request_effective:
            return CustomerQuoteStatus.APPROVAL_REQUIRED
        return CustomerQuoteStatus.READY_FOR_HUMAN_APPROVAL


class ExecutionChainVerifier:
    REQUIRED_STAGES = frozenset({f"ep{item:02d}" for item in range(1, 11)})

    def __init__(
        self,
        node_reader: ExecutionChainNodeReader,
        audit: AuditChainVerifierPort,
        *,
        package_reader: CustomerQuotePackageReader,
    ) -> None:
        self.node_reader = node_reader
        self.audit = audit
        self.package_reader = package_reader

    def build_manifest(
        self,
        package: CustomerQuotePackageSnapshot,
        *,
        nodes: tuple[ExecutionChainNode, ...],
        as_of: datetime,
    ) -> ExecutionChainManifest:
        moment = require_utc(as_of)
        payload = {
            "package": package.content_fingerprint,
            "nodes": nodes,
            "audit_tip": self.audit.tip_hash(),
            "as_of": moment,
        }
        fingerprint = stable_fingerprint(payload)
        return ExecutionChainManifest(
            manifest_id=str(uuid5(NAMESPACE_URL, f"trade-alpha/ep10/manifest/{fingerprint}")),
            customer_quote_package_id=package.customer_quote_package_id,
            customer_quote_package_fingerprint=package.content_fingerprint,
            nodes=nodes,
            audit_tip_hash=self.audit.tip_hash(),
            as_of=moment,
            content_fingerprint=fingerprint,
        )

    def verify(self, manifest: ExecutionChainManifest) -> ChainVerificationResult:
        issues: list[str] = []
        saved_package = self.package_reader.package(manifest.customer_quote_package_id)
        package = saved_package if type(saved_package) is CustomerQuotePackageSnapshot else None
        if package is None or (
            package.content_fingerprint != manifest.customer_quote_package_fingerprint
        ):
            issues.append("unsaved_or_forged_customer_quote_package")
        expected_fingerprint = stable_fingerprint(
            {
                "package": manifest.customer_quote_package_fingerprint,
                "nodes": manifest.nodes,
                "audit_tip": manifest.audit_tip_hash,
                "as_of": manifest.as_of,
            }
        )
        if expected_fingerprint != manifest.content_fingerprint:
            issues.append("manifest_content_fingerprint_mismatch")
        stages = {node.stage for node in manifest.nodes}
        if len(stages) != len(manifest.nodes):
            issues.append("duplicate_chain_stage")
        missing = self.REQUIRED_STAGES - stages
        if missing:
            issues.append("missing_stages:" + ",".join(sorted(missing)))
        unexpected = stages - self.REQUIRED_STAGES
        if unexpected:
            issues.append("unexpected_stages:" + ",".join(sorted(unexpected)))
        if any(node.occurred_at > manifest.as_of for node in manifest.nodes):
            issues.append("future_chain_node")
        if len({node.scope_id for node in manifest.nodes}) != 1:
            issues.append("chain_scope_mismatch")
        elif (
            package is not None
            and manifest.nodes
            and manifest.nodes[0].scope_id != package.product_spec.snapshot_id
        ):
            issues.append("chain_scope_package_mismatch")
        if any(not node.is_lineage_tip for node in manifest.nodes):
            issues.append("stale_chain_lineage")
        for node in manifest.nodes:
            saved = self.node_reader.get_saved_node(node.stage, node.snapshot_id)
            if type(saved) is not ExecutionChainNode or saved != node:
                issues.append(f"unsaved_or_forged_node:{node.stage}:{node.snapshot_id}")
        ep10_nodes = [node for node in manifest.nodes if node.stage == "ep10"]
        if len(ep10_nodes) != 1 or (
            ep10_nodes[0].snapshot_id != manifest.customer_quote_package_id
            or ep10_nodes[0].content_fingerprint != manifest.customer_quote_package_fingerprint
        ):
            issues.append("ep10_package_binding_mismatch")
        if package is not None:
            expected_package_bindings = {
                "ep03": (
                    package.product_spec.snapshot_id,
                    package.product_spec.content_fingerprint,
                ),
                "ep05": (package.rfq.snapshot_id, package.rfq.content_fingerprint),
                "ep07": (
                    package.cost_scenario.dependency_id,
                    package.cost_scenario.content_fingerprint,
                ),
                "ep08": (
                    package.risk_assessment.dependency_id,
                    package.risk_assessment.content_fingerprint,
                ),
            }
            if (
                package.readiness_approval_request_id is None
                or package.readiness_approval_fingerprint is None
            ):
                if package.status is CustomerQuoteStatus.BUDGETARY_ONLY:
                    expected_package_bindings["ep09"] = budgetary_no_approval_binding(package)
                else:
                    issues.append("ep09_package_binding_missing")
            else:
                expected_package_bindings["ep09"] = (
                    package.readiness_approval_request_id,
                    package.readiness_approval_fingerprint,
                )
            by_stage = {node.stage: node for node in manifest.nodes}
            for stage, (snapshot_id, fingerprint) in expected_package_bindings.items():
                bound_node = by_stage.get(stage)
                if bound_node is None or (
                    bound_node.snapshot_id != snapshot_id
                    or bound_node.content_fingerprint != fingerprint
                ):
                    issues.append(f"{stage}_package_binding_mismatch")
        ordered = sorted(manifest.nodes, key=lambda node: node.stage)
        if any(
            left.snapshot_id not in right.predecessor_refs
            for left, right in zip(ordered, ordered[1:], strict=False)
        ):
            issues.append("chain_predecessor_binding_invalid")
        if any(
            right.occurred_at < left.occurred_at
            for left, right in zip(ordered, ordered[1:], strict=False)
        ):
            issues.append("chain_time_order_invalid")
        if not self.audit.verify():
            issues.append("audit_chain_invalid")
        elif self.audit.tip_hash() != manifest.audit_tip_hash:
            issues.append("audit_tip_mismatch")
        return ChainVerificationResult(
            valid=not issues,
            issues=tuple(sorted(set(issues))),
            verified_at=self.audit.verified_at(),
        )
