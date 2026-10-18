"""Deterministic, internal-only commercial risk evaluation for EP-08."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from trade_alpha.contracts.common import VersionRef, require_utc
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.costing.models import CostScenarioSnapshot
from trade_alpha.execution.specifications.models import ProductSpecVersion, SpecState
from trade_alpha.execution.supplier_quotes.models import (
    QuoteNormalizationRun,
    SupplierComparisonMatrix,
    SupplierQuoteSnapshot,
)
from trade_alpha.execution.suppliers.models import (
    QualificationDisposition,
    SupplierQualificationSnapshot,
    qualification_snapshot_content_fingerprint,
)
from trade_alpha.governance.security import SecretScanner

from .models import (
    FindingState,
    RiskAssessmentInput,
    RiskDecision,
    RiskDecisionAction,
    RiskDimension,
    RiskDisposition,
    RiskFinding,
    RiskPolicyVersion,
    RiskSeverity,
    TradeRiskAssessmentSnapshot,
    assessment_content_fingerprint,
    deterministic_finding_id,
    policy_content_fingerprint,
)
from .ports import (
    ExecutionAuditPort,
    InMemoryExecutionAudit,
    RiskDecisionAuthorizationPort,
    TradeRiskRepositoryPort,
    TradeRiskSourceReader,
)
from .repository import InMemoryTradeRiskRepository, effective_risk_decisions


class TradeRiskService:
    """Evaluate existing snapshots; this service intentionally has no outbound port."""

    def __init__(
        self,
        repository: TradeRiskRepositoryPort | None = None,
        *,
        source_reader: TradeRiskSourceReader | None = None,
        authorization: RiskDecisionAuthorizationPort | None = None,
        audit: ExecutionAuditPort | None = None,
    ) -> None:
        self.repository = repository or InMemoryTradeRiskRepository()
        self.source_reader = source_reader
        self.authorization = authorization
        self.audit = audit or InMemoryExecutionAudit()

    def assess(
        self,
        inputs: RiskAssessmentInput,
        policy: RiskPolicyVersion,
        *,
        as_of: datetime,
        created_at: datetime | None = None,
    ) -> TradeRiskAssessmentSnapshot:
        moment = require_utc(as_of)
        self._validate_policy(policy, moment)
        creation = require_utc(created_at or moment)
        if creation < moment:
            raise ValueError("risk assessment created_at cannot predate as_of")
        if self.source_reader is None:
            raise ValueError("risk assessment requires a repository-backed source reader")
        saved_inputs = self.source_reader.read(as_of=moment)
        if type(saved_inputs) is not RiskAssessmentInput or saved_inputs != inputs:
            raise ValueError("risk inputs must match repository-saved snapshots")
        self._validate_inputs(inputs, policy, moment)
        findings = self._evaluate(inputs, policy, moment)
        supplier_id = inputs.qualification.supplier_id
        quote_ids = tuple(item.supplier_quote_snapshot_id for item in inputs.quote_snapshots)
        cost_ids = tuple(item.cost_scenario_snapshot_id for item in inputs.cost_scenarios)
        valid_until = self._earliest_expiry(inputs, policy)
        disposition = self._disposition(findings, valid_until, moment)
        input_fingerprint = self._input_fingerprint(inputs, policy, moment)
        assessment_id = str(
            uuid5(NAMESPACE_URL, f"trade-alpha/ep08/assessment/{input_fingerprint}")
        )
        draft = TradeRiskAssessmentSnapshot(
            assessment_id=assessment_id,
            supplier_id=supplier_id,
            product_family_id=inputs.product_spec.profile_ref.name,
            product_spec_version_id=inputs.product_spec.spec_version_id,
            qualification_snapshot_id=inputs.qualification.qualification_snapshot_id,
            quote_snapshot_ids=quote_ids,
            normalization_run_id=inputs.normalization_run.normalization_run_id,
            comparison_matrix_id=inputs.comparison_matrix.comparison_matrix_id,
            cost_scenario_ids=cost_ids,
            policy_version=VersionRef(name=policy.policy_id, version=policy.policy_version),
            policy_config_fingerprint=policy.config_fingerprint,
            as_of=moment,
            valid_until=valid_until,
            findings=findings,
            disposition=disposition,
            input_fingerprint=input_fingerprint,
            result_fingerprint="pending",
            created_at=creation,
        )
        assessment = draft.model_copy(
            update={"result_fingerprint": assessment_content_fingerprint(draft)}
        )
        saved = self.repository.save_assessment(assessment)
        self._audit(
            "risk.assessed", assessment, assessment, moment, "deterministic risk assessment"
        )
        return saved

    def record_decision(self, decision: RiskDecision, *, policy: RiskPolicyVersion) -> RiskDecision:
        assessment = self.repository.get_assessment(decision.assessment_id)
        if assessment is None:
            raise KeyError(f"unknown risk assessment {decision.assessment_id}")
        self._require_policy(assessment, policy)
        if decision.actor_role not in policy.allowed_human_roles:
            raise PermissionError("actor role is not allowed by risk policy")
        if self.authorization is not None and not self.authorization.may_decide(
            decision.actor_id, decision.actor_role, "risk.review"
        ):
            raise PermissionError("actor is not authorized for risk review")
        if decision.decided_at < assessment.as_of:
            raise ValueError("risk decision cannot predate assessment as_of")
        finding_by_id = {item.finding_id: item for item in assessment.findings}
        if any(item not in finding_by_id for item in decision.finding_ids):
            raise ValueError("risk decision references an unknown finding")
        if (
            any(
                finding_by_id[item].severity is RiskSeverity.HARD_BLOCK
                for item in decision.finding_ids
            )
            and decision.decision is not RiskDecisionAction.BLOCK
        ):
            raise ValueError("hard-block finding cannot be overridden by ordinary decision")
        SecretScanner().assert_clean({"risk_decision": decision.rationale})
        saved = self.repository.save_decision(decision)
        self._audit(
            "risk.decision_recorded",
            assessment,
            saved,
            decision.decided_at,
            decision.rationale,
            actor_id=decision.actor_id,
            client_identity=decision.client_identity,
        )
        return saved

    def freeze_snapshot(
        self, assessment_id: str, *, policy: RiskPolicyVersion, as_of: datetime
    ) -> TradeRiskAssessmentSnapshot:
        moment = require_utc(as_of)
        assessment = self.repository.get_assessment(assessment_id)
        if assessment is None:
            raise KeyError(f"unknown risk assessment {assessment_id}")
        if moment < assessment.as_of:
            raise ValueError("risk snapshot cannot be frozen before its assessment as_of")
        self._require_policy(assessment, policy)
        decisions = effective_risk_decisions(
            self.repository.decisions_for(assessment_id, as_of=moment)
        )
        decision_by_finding = {
            finding_id: decision
            for decision in decisions
            for finding_id in decision.finding_ids
            if decision.decision is RiskDecisionAction.RESOLVE
        }
        blocked_by_decision = {
            finding_id
            for decision in decisions
            if decision.decision is RiskDecisionAction.BLOCK
            for finding_id in decision.finding_ids
        }
        updated_findings = tuple(
            item.model_copy(
                update={
                    "state": FindingState.RESOLVED,
                    "resolved_by_decision_id": decision_by_finding[item.finding_id].decision_id,
                }
            )
            if item.finding_id in decision_by_finding
            else item.model_copy(update={"severity": RiskSeverity.HARD_BLOCK})
            if item.finding_id in blocked_by_decision
            else item
            for item in assessment.findings
        )
        disposition = self._disposition(updated_findings, assessment.valid_until, moment)
        payload = {
            "prior": assessment.result_fingerprint,
            "decisions": [item.decision_id for item in decisions],
            "as_of": moment,
            "findings": updated_findings,
            "disposition": disposition,
        }
        lineage_fingerprint = _fingerprint(payload)
        if not decisions and moment == assessment.as_of:
            return assessment
        draft = assessment.model_copy(
            update={
                "assessment_id": str(
                    uuid5(NAMESPACE_URL, f"trade-alpha/ep08/frozen/{lineage_fingerprint}")
                ),
                "findings": updated_findings,
                "disposition": disposition,
                "effective_decision_ids": tuple(item.decision_id for item in decisions),
                "supersedes_assessment_id": assessment.assessment_id,
                "as_of": moment,
                "result_fingerprint": "pending",
            }
        )
        snapshot = draft.model_copy(
            update={"result_fingerprint": assessment_content_fingerprint(draft)}
        )
        saved = self.repository.save_assessment(snapshot)
        self._audit("risk.snapshot_frozen", assessment, saved, moment, "risk snapshot frozen")
        return saved

    @staticmethod
    def _validate_policy(policy: RiskPolicyVersion, as_of: datetime) -> None:
        if type(policy) is not RiskPolicyVersion:
            raise TypeError("risk policy has an invalid type")
        RiskPolicyVersion.model_validate(policy.model_dump())
        if policy.config_fingerprint != policy_content_fingerprint(policy):
            raise ValueError("risk policy config fingerprint mismatch")
        if not (
            policy.effective_from <= as_of
            and (policy.effective_until is None or as_of <= policy.effective_until)
        ):
            raise ValueError("risk policy is not effective at as_of")

    @staticmethod
    def _validate_inputs(
        inputs: RiskAssessmentInput, policy: RiskPolicyVersion, as_of: datetime
    ) -> None:
        if type(inputs.product_spec) is not ProductSpecVersion:
            raise TypeError("risk input product_spec must be an exact ProductSpecVersion")
        if type(inputs.qualification) is not SupplierQualificationSnapshot:
            raise TypeError(
                "risk input qualification must be an exact SupplierQualificationSnapshot"
            )
        if type(inputs.normalization_run) is not QuoteNormalizationRun:
            raise TypeError("risk input normalization_run must be an exact QuoteNormalizationRun")
        if type(inputs.comparison_matrix) is not SupplierComparisonMatrix:
            raise TypeError(
                "risk input comparison_matrix must be an exact SupplierComparisonMatrix"
            )
        if not inputs.quote_snapshots:
            raise ValueError("risk assessment requires at least one quote snapshot")
        if any(type(item) is not SupplierQuoteSnapshot for item in inputs.quote_snapshots):
            raise TypeError(
                "risk input quote_snapshots must contain exact SupplierQuoteSnapshot values"
            )
        if any(type(item) is not CostScenarioSnapshot for item in inputs.cost_scenarios):
            raise TypeError(
                "risk input cost_scenarios must contain exact CostScenarioSnapshot values"
            )
        spec = inputs.product_spec
        qualification = inputs.qualification
        if spec.created_at > as_of:
            raise ValueError("product specification is after as_of")
        if policy.product_family_id != spec.profile_ref.name:
            raise ValueError("risk policy product family does not match the specification")
        if spec.state is not SpecState.FROZEN_FOR_SUPPLIER_RFQ:
            raise ValueError("risk assessment requires a frozen product specification")
        if qualification.product_spec_version_id != spec.spec_version_id:
            raise ValueError("qualification and product specification scope mismatch")
        if qualification.as_of > as_of:
            raise ValueError("qualification snapshot is after as_of")
        if qualification.valid_until is not None and qualification.valid_until < as_of:
            raise ValueError("qualification snapshot is expired at as_of")
        supplier_id = qualification.supplier_id
        if any(item.supplier_id != supplier_id for item in inputs.quote_snapshots):
            raise ValueError("quote supplier scope does not match qualification")
        if any(
            item.product_spec_version_id != spec.spec_version_id
            or item.supplier_profile_version_id != qualification.supplier_profile_version_id
            for item in inputs.quote_snapshots
        ):
            raise ValueError("quote snapshot scope does not match qualification/specification")
        quote_ids = {item.supplier_quote_snapshot_id for item in inputs.quote_snapshots}
        if any(
            item.supplier_id != supplier_id
            or item.product_spec_version_id != spec.spec_version_id
            or item.supplier_quote_snapshot_id not in quote_ids
            or item.normalization_run_id != inputs.normalization_run.normalization_run_id
            for item in inputs.cost_scenarios
        ):
            raise ValueError("cost scenario scope does not match quote/specification")
        if inputs.normalization_run.as_of > as_of or inputs.comparison_matrix.as_of > as_of:
            raise ValueError("normalization or comparison input is after as_of")
        if set(inputs.normalization_run.quote_snapshot_ids) != {
            item.supplier_quote_snapshot_id for item in inputs.quote_snapshots
        }:
            raise ValueError("normalization does not cover exact quote snapshots")
        if set(inputs.comparison_matrix.quote_snapshot_ids) != {
            item.supplier_quote_snapshot_id for item in inputs.quote_snapshots
        }:
            raise ValueError("comparison matrix does not cover exact quote snapshots")
        if any(
            item.received_at > as_of or item.confirmed_at > as_of for item in inputs.quote_snapshots
        ):
            raise ValueError("quote snapshot is after as_of")
        quote_provenance = tuple(
            ref
            for quote in inputs.quote_snapshots
            for ref in (
                *(ref for line in quote.confirmed_lines for ref in line.provenance_refs),
                *(component.provenance_ref for component in quote.confirmed_terms.components),
            )
        )
        if any(ref.observed_at > as_of for ref in quote_provenance):
            raise ValueError("quote evidence is after risk assessment as_of")
        if any(item.product_family_id != spec.profile_ref.name for item in inputs.cost_scenarios):
            raise ValueError("cost scenario product family does not match the specification")
        if inputs.qualification.content_fingerprint != qualification_snapshot_content_fingerprint(
            qualification
        ):
            raise ValueError("qualification snapshot content fingerprint mismatch")

    def _evaluate(
        self, inputs: RiskAssessmentInput, policy: RiskPolicyVersion, as_of: datetime
    ) -> tuple[RiskFinding, ...]:
        findings: list[RiskFinding] = []
        spec = inputs.product_spec
        qualification = inputs.qualification

        def add(
            dimension: RiskDimension,
            code: str,
            severity: RiskSeverity,
            refs: tuple[str, ...],
            action: str,
        ) -> None:
            configured = self._configured_severity(policy, code, severity)
            findings.append(
                RiskFinding(
                    finding_id=deterministic_finding_id(qualification.supplier_id, code, refs),
                    dimension=dimension,
                    code=code,
                    severity=configured,
                    subject_id=qualification.supplier_id,
                    input_refs=refs,
                    evidence_refs=refs,
                    message_key=f"commercial_risk.{code.lower()}",
                    suggested_action=action,
                )
            )

        if spec.state is not SpecState.FROZEN_FOR_SUPPLIER_RFQ:
            add(
                RiskDimension.SAFETY,
                "SPEC_NOT_FROZEN",
                RiskSeverity.HARD_BLOCK,
                (spec.spec_version_id,),
                "freeze specification",
            )
        if spec.risk_flags or spec.readiness.risk_flags:
            add(
                RiskDimension.SAFETY,
                "SAFETY_SCOPE_UNRESOLVED",
                RiskSeverity.HARD_BLOCK,
                (spec.spec_version_id,),
                "resolve safety scope",
            )
        if qualification.disposition not in {
            QualificationDisposition.DRY_RUN_ELIGIBLE,
            QualificationDisposition.DRY_RUN_ELIGIBLE_WITH_CONDITIONS,
        }:
            add(
                RiskDimension.SUPPLIER,
                "QUALIFICATION_NOT_ELIGIBLE",
                RiskSeverity.HARD_BLOCK,
                (qualification.qualification_snapshot_id,),
                "obtain valid qualification",
            )
        if not qualification.screening_run_refs:
            add(
                RiskDimension.SUPPLIER,
                "SUPPLIER_SCREENING_MISSING",
                RiskSeverity.HARD_BLOCK,
                (qualification.qualification_snapshot_id,),
                "obtain a current screening run",
            )
        if not inputs.license_allowed:
            add(
                RiskDimension.SUPPLIER,
                "LICENSE_POLICY_DENIED",
                RiskSeverity.HARD_BLOCK,
                (inputs.license_reason or "license",),
                "resolve source license",
            )
        if any(status != "clear" for status in inputs.quote_quarantine_statuses):
            add(
                RiskDimension.QUOTE,
                "QUOTE_QUARANTINED",
                RiskSeverity.HARD_BLOCK,
                tuple(inputs.quote_quarantine_statuses),
                "resolve quote evidence quarantine",
            )
        if inputs.comparison_matrix.disposition.value == "not_comparable":
            add(
                RiskDimension.QUOTE,
                "COMPARISON_NOT_COMPARABLE",
                RiskSeverity.HARD_BLOCK,
                (inputs.comparison_matrix.comparison_matrix_id,),
                "resolve comparison issues",
            )
        if inputs.comparison_matrix.issue_refs:
            add(
                RiskDimension.QUOTE,
                "QUOTE_COMPARABILITY_ISSUES",
                RiskSeverity.APPROVAL_REQUIRED,
                tuple(inputs.comparison_matrix.issue_refs),
                "review quote issues",
            )
        if inputs.normalization_run.issues:
            add(
                RiskDimension.QUOTE,
                "QUOTE_NORMALIZATION_ISSUES",
                RiskSeverity.APPROVAL_REQUIRED,
                (inputs.normalization_run.normalization_run_id,),
                "resolve normalization issues",
            )
        for quote in inputs.quote_snapshots:
            status = quote.validity_as_of(as_of)
            if status != "valid":
                add(
                    RiskDimension.QUOTE,
                    f"QUOTE_{status.upper()}",
                    RiskSeverity.HARD_BLOCK,
                    (quote.supplier_quote_snapshot_id,),
                    "obtain a current valid quote",
                )
            if (
                quote.confirmed_terms.incoterm is None
                or not quote.confirmed_terms.incoterm.named_place.strip()
            ):
                add(
                    RiskDimension.TRANSACTION,
                    "NAMED_PLACE_MISSING",
                    RiskSeverity.HARD_BLOCK,
                    (quote.supplier_quote_snapshot_id,),
                    "obtain a named delivery place",
                )
            elif quote.confirmed_terms.incoterm.code.value == "DDP":
                add(
                    RiskDimension.TRANSACTION,
                    "DDP_NOT_ALLOWED",
                    RiskSeverity.HARD_BLOCK,
                    (quote.supplier_quote_snapshot_id,),
                    "use an approved non-DDP Incoterm",
                )
            if not quote.confirmed_terms.payment_terms:
                add(
                    RiskDimension.TRANSACTION,
                    "PAYMENT_TERMS_MISSING",
                    RiskSeverity.APPROVAL_REQUIRED,
                    (quote.supplier_quote_snapshot_id,),
                    "obtain payment terms and specialist review",
                )
            if quote.unresolved_fields:
                add(
                    RiskDimension.QUOTE,
                    "QUOTE_FIELDS_UNRESOLVED",
                    RiskSeverity.APPROVAL_REQUIRED,
                    (quote.supplier_quote_snapshot_id, *quote.unresolved_fields),
                    "resolve quote fields",
                )
            if quote.deviations or any(line.deviations for line in quote.confirmed_lines):
                add(
                    RiskDimension.QUOTE,
                    "QUOTE_SPEC_DEVIATION",
                    RiskSeverity.APPROVAL_REQUIRED,
                    (quote.supplier_quote_snapshot_id,),
                    "review quote deviation",
                )
        if not inputs.cost_scenarios:
            add(
                RiskDimension.COST,
                "COST_SCENARIO_MISSING",
                RiskSeverity.HARD_BLOCK,
                (spec.spec_version_id,),
                "create cost scenario",
            )
        for scenario in inputs.cost_scenarios:
            scenario_id = scenario.cost_scenario_snapshot_id
            scenario_as_of = self._datetime_of(scenario, "as_of")
            if scenario_as_of is not None and scenario_as_of > as_of:
                add(
                    RiskDimension.COST,
                    "COST_SCENARIO_AFTER_AS_OF",
                    RiskSeverity.HARD_BLOCK,
                    (scenario_id,),
                    "use a historical cost snapshot",
                )
            quote = next(
                item
                for item in inputs.quote_snapshots
                if item.supplier_quote_snapshot_id == scenario.supplier_quote_snapshot_id
            )
            if scenario.supplier_quote_content_fingerprint != quote.content_fingerprint:
                add(
                    RiskDimension.COST,
                    "COST_QUOTE_FINGERPRINT_MISMATCH",
                    RiskSeverity.HARD_BLOCK,
                    (scenario_id,),
                    "reload the repository-saved quote scenario",
                )
            if (
                scenario.normalization_result_fingerprint
                != inputs.normalization_run.result_fingerprint
            ):
                add(
                    RiskDimension.COST,
                    "COST_NORMALIZATION_FINGERPRINT_MISMATCH",
                    RiskSeverity.HARD_BLOCK,
                    (scenario_id,),
                    "reload the repository-saved normalization run",
                )
            unresolved = tuple(
                item.value for item in getattr(scenario, "unresolved_required_components", ())
            )
            if unresolved or self._value_of(scenario, "landed_cost_per_unit") is None:
                add(
                    RiskDimension.COST,
                    "COST_INPUT_UNRESOLVED",
                    RiskSeverity.HARD_BLOCK,
                    (scenario_id, *unresolved),
                    "resolve required cost inputs",
                )
            margin = self._value_of(scenario, "contribution_margin_rate")
            if (
                policy.minimum_contribution_margin_rate is not None
                and margin is not None
                and margin < policy.minimum_contribution_margin_rate
            ):
                add(
                    RiskDimension.COST,
                    "MARGIN_FLOOR_NOT_MET",
                    RiskSeverity.HARD_BLOCK,
                    (scenario_id,),
                    "revise cost or commercial terms",
                )
            elif policy.minimum_contribution_margin_rate is not None and margin is None:
                add(
                    RiskDimension.COST,
                    "MARGIN_NOT_CALCULABLE",
                    RiskSeverity.HARD_BLOCK,
                    (scenario_id,),
                    "calculate contribution margin",
                )
        missing_transaction = tuple(
            item
            for item in policy.required_transaction_evidence
            if item not in inputs.transaction_evidence
        )
        if missing_transaction:
            add(
                RiskDimension.TRANSACTION,
                "TRANSACTION_EVIDENCE_MISSING",
                RiskSeverity.APPROVAL_REQUIRED,
                missing_transaction,
                "obtain specialist transaction evidence",
            )
        return tuple(sorted(findings, key=lambda item: item.finding_id))

    @staticmethod
    def _configured_severity(
        policy: RiskPolicyVersion, code: str, default: RiskSeverity
    ) -> RiskSeverity:
        if default is RiskSeverity.HARD_BLOCK:
            # Safety, identity, validity and data-integrity gates cannot be
            # downgraded by a policy typo or an ordinary human decision.
            return RiskSeverity.HARD_BLOCK
        if code in policy.hard_block_codes:
            return RiskSeverity.HARD_BLOCK
        if code in policy.approval_required_codes:
            return RiskSeverity.APPROVAL_REQUIRED
        if code in policy.warning_codes:
            return RiskSeverity.WARNING
        return default

    @staticmethod
    def _disposition(
        findings: tuple[RiskFinding, ...], valid_until: datetime | None, as_of: datetime
    ) -> RiskDisposition:
        if valid_until is not None and valid_until < as_of:
            return RiskDisposition.EXPIRED
        open_findings = tuple(item for item in findings if item.state is FindingState.OPEN)
        if any(item.severity is RiskSeverity.HARD_BLOCK for item in open_findings):
            return RiskDisposition.BLOCKED
        if open_findings:
            return RiskDisposition.REVIEW_REQUIRED
        return RiskDisposition.CLEAR_FOR_INTERNAL_QUOTE

    @staticmethod
    def _input_fingerprint(
        inputs: RiskAssessmentInput, policy: RiskPolicyVersion, as_of: datetime
    ) -> str:
        return _fingerprint(
            {"inputs": inputs, "policy": policy_content_fingerprint(policy), "as_of": as_of}
        )

    @staticmethod
    def _earliest_expiry(inputs: RiskAssessmentInput, policy: RiskPolicyVersion) -> datetime | None:
        values = [policy.effective_until, inputs.qualification.valid_until]
        values.extend(item.confirmed_terms.quote_valid_until for item in inputs.quote_snapshots)
        values.extend(
            TradeRiskService._datetime_of(item, "valid_until") for item in inputs.cost_scenarios
        )
        return min((item for item in values if item is not None), default=None)

    @staticmethod
    def _datetime_of(value: Any, field: str) -> datetime | None:
        item = getattr(value, field, None)
        return None if item is None else require_utc(item)

    @staticmethod
    def _value_of(value: Any, field: str) -> Any:
        return getattr(value, field, None)

    @staticmethod
    def _require_policy(assessment: TradeRiskAssessmentSnapshot, policy: RiskPolicyVersion) -> None:
        if (
            assessment.policy_version.name != policy.policy_id
            or assessment.policy_version.version != policy.policy_version
        ):
            raise ValueError("policy must exactly match risk assessment policy version")
        if assessment.policy_config_fingerprint != policy.config_fingerprint:
            raise ValueError("risk policy configuration does not match assessment")

    def _audit(
        self,
        action: str,
        before: Any,
        after: Any,
        at: datetime,
        reason: str,
        *,
        actor_id: str = "system",
        client_identity: str = "ep08",
    ) -> None:
        SecretScanner().assert_clean({"risk_audit_reason": reason})
        self.audit.append(
            actor_id=actor_id,
            client_identity=client_identity,
            action=action,
            resource=getattr(before, "assessment_id", "risk"),
            before=before,
            after=after,
            occurred_at=at,
            reason=reason,
            correlation_id=f"ep08:{getattr(before, 'assessment_id', 'risk')}",
        )


def _fingerprint(value: Any) -> str:
    return stable_fingerprint(value)
