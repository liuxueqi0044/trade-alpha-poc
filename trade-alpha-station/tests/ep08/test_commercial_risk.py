from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from trade_alpha.contracts.common import VersionRef
from trade_alpha.execution.adapters.execution_dependencies import RiskAssessmentDependencyReader
from trade_alpha.execution.commercial_risk import (
    FindingState,
    InMemoryTradeRiskRepository,
    RiskAssessmentInput,
    RiskDecision,
    RiskDecisionAction,
    RiskDimension,
    RiskDisposition,
    RiskFinding,
    RiskPolicyVersion,
    RiskSeverity,
    TradeRiskAssessmentSnapshot,
    TradeRiskService,
    assessment_content_fingerprint,
    policy_content_fingerprint,
)

NOW = datetime(2026, 8, 27, tzinfo=UTC)


def policy() -> RiskPolicyVersion:
    draft = RiskPolicyVersion(
        policy_id="commercial-risk",
        policy_version="1.0.0",
        product_family_id="FE-DRY-01",
        effective_from=NOW - timedelta(days=1),
        rule_version="1.0.0",
        allowed_human_roles=("risk_reviewer",),
        hard_block_codes=("SPEC_NOT_FROZEN",),
        config_fingerprint="sha256:placeholder",
    )
    return draft.model_copy(update={"config_fingerprint": policy_content_fingerprint(draft)})


def test_policy_is_canonical_and_frozen() -> None:
    current = policy()
    assert current.config_fingerprint == policy_content_fingerprint(current)
    with pytest.raises(ValidationError):
        RiskPolicyVersion.model_validate(current.model_dump() | {"unknown": "field"})
    with pytest.raises(ValidationError):
        RiskPolicyVersion(
            policy_id="p",
            policy_version="1",
            product_family_id="f",
            effective_from=NOW,
            effective_until=NOW - timedelta(seconds=1),
            rule_version="1",
            allowed_human_roles=("reviewer",),
            config_fingerprint="sha256:x",
        )


def test_duck_product_spec_is_rejected_before_any_evaluation() -> None:
    class Duck:
        pass

    with pytest.raises(ValidationError):
        RiskAssessmentInput(
            product_spec=Duck(),
            qualification=Duck(),
            quote_snapshots=(Duck(),),
            normalization_run=Duck(),
            comparison_matrix=Duck(),
        )


def test_hard_block_finding_cannot_be_resolved_by_ordinary_decision() -> None:
    finding = RiskFinding(
        finding_id="finding-1",
        dimension=RiskDimension.SAFETY,
        code="SPEC_NOT_FROZEN",
        severity=RiskSeverity.HARD_BLOCK,
        subject_id="supplier-a",
        input_refs=("spec-v1",),
        evidence_refs=("spec-v1",),
        message_key="commercial_risk.spec_not_frozen",
        suggested_action="freeze specification",
    )
    assessment = TradeRiskAssessmentSnapshot(
        assessment_id="assessment-1",
        supplier_id="supplier-a",
        product_family_id="FE-DRY-01",
        product_spec_version_id="spec-v1",
        qualification_snapshot_id="qualification-1",
        quote_snapshot_ids=("quote-1",),
        normalization_run_id="normalization-1",
        comparison_matrix_id="matrix-1",
        policy_version=VersionRef(name="commercial-risk", version="1.0.0"),
        policy_config_fingerprint=policy().config_fingerprint,
        as_of=NOW,
        findings=(finding,),
        disposition=RiskDisposition.BLOCKED,
        input_fingerprint="sha256:input",
        result_fingerprint="sha256:result",
        created_at=NOW,
    )
    repository = InMemoryTradeRiskRepository()
    repository.save_assessment(assessment)
    with pytest.raises(ValueError, match="hard-block"):
        TradeRiskService(repository).record_decision(
            RiskDecision(
                decision_id="decision-1",
                assessment_id="assessment-1",
                finding_ids=("finding-1",),
                decision=RiskDecisionAction.RESOLVE,
                actor_id="reviewer",
                actor_role="risk_reviewer",
                client_identity="test",
                rationale="attempted override",
                decided_at=NOW,
            ),
            policy=policy(),
        )


def test_risk_finding_resolution_requires_explicit_decision_reference() -> None:
    with pytest.raises(ValidationError, match="resolved risk finding"):
        RiskFinding(
            finding_id="finding-1",
            dimension=RiskDimension.COST,
            code="COST_INPUT_UNRESOLVED",
            severity=RiskSeverity.HARD_BLOCK,
            state=FindingState.RESOLVED,
            subject_id="supplier-a",
            input_refs=("cost-1",),
            message_key="commercial_risk.cost_input_unresolved",
            suggested_action="resolve cost",
        )


def test_non_hard_decisions_change_disposition_and_keep_canonical_fingerprint() -> None:
    finding = RiskFinding(
        finding_id="finding-warning",
        dimension=RiskDimension.TRANSACTION,
        code="PAYMENT_TERMS_MISSING",
        severity=RiskSeverity.APPROVAL_REQUIRED,
        subject_id="supplier-a",
        input_refs=("quote-1",),
        message_key="commercial_risk.payment_terms_missing",
        suggested_action="review payment terms",
    )
    draft = TradeRiskAssessmentSnapshot(
        assessment_id="assessment-review",
        supplier_id="supplier-a",
        product_family_id="FE-DRY-01",
        product_spec_version_id="spec-v1",
        qualification_snapshot_id="qualification-1",
        quote_snapshot_ids=("quote-1",),
        normalization_run_id="normalization-1",
        comparison_matrix_id="matrix-1",
        policy_version=VersionRef(name="commercial-risk", version="1.0.0"),
        policy_config_fingerprint=policy().config_fingerprint,
        as_of=NOW,
        findings=(finding,),
        disposition=RiskDisposition.REVIEW_REQUIRED,
        input_fingerprint="sha256:input",
        result_fingerprint="pending",
        created_at=NOW,
    )
    assessment = draft.model_copy(
        update={"result_fingerprint": assessment_content_fingerprint(draft)}
    )
    repository = InMemoryTradeRiskRepository()
    repository.save_assessment(assessment)
    service = TradeRiskService(repository)
    service.record_decision(
        RiskDecision(
            decision_id="resolve-warning",
            assessment_id=assessment.assessment_id,
            finding_ids=(finding.finding_id,),
            decision=RiskDecisionAction.RESOLVE,
            actor_id="reviewer",
            actor_role="risk_reviewer",
            client_identity="test",
            rationale="evidence reviewed",
            decided_at=NOW + timedelta(minutes=1),
        ),
        policy=policy(),
    )
    frozen = service.freeze_snapshot(
        assessment.assessment_id,
        policy=policy(),
        as_of=NOW + timedelta(minutes=1),
    )
    assert frozen.disposition is RiskDisposition.CLEAR_FOR_INTERNAL_QUOTE
    assert frozen.result_fingerprint == assessment_content_fingerprint(frozen)
    reader = RiskAssessmentDependencyReader(repository)
    assert reader.get_saved_as_of(frozen.assessment_id, frozen.as_of) is not None
    assert reader.get_saved_as_of(assessment.assessment_id, frozen.as_of) is None
