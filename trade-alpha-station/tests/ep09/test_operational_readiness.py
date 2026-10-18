from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trade_alpha.contracts.common import EvidenceLocator, VersionRef
from trade_alpha.execution.contracts import ProvenanceRef
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
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
    ReadinessEvaluationStatus,
    ReadinessItem,
    ReadinessItemState,
)

NOW = datetime(2026, 8, 27, tzinfo=UTC)


class Reader:
    def __init__(self, value: ExecutionDependencySnapshot) -> None:
        self.value = value

    def get_saved_as_of(
        self, dependency_id: str, as_of: datetime
    ) -> ExecutionDependencySnapshot | None:
        if dependency_id == self.value.dependency_id and self.value.as_of <= as_of:
            return self.value
        return None


class Authorization:
    def require(self, actor_id: str, action: str, resource_id: str, at: datetime) -> None:
        if actor_id == "admin":
            raise PermissionError("Admin has no execution approval grant")


class Audit:
    def __init__(self) -> None:
        self.events: list[str] = []

    def append(self, **kwargs: object) -> str:  # type: ignore[no-untyped-def]
        value = stable_fingerprint(kwargs)
        self.events.append(value)
        return value


def provenance() -> ProvenanceRef:
    return ProvenanceRef(
        source="fixture",
        evidence=EvidenceLocator(artifact_id="artifact:readiness", block_ids=("b1",)),
        observed_at=NOW,
        source_version=VersionRef(name="fixture", version="1"),
    )


def policy() -> OperationalReadinessPolicyVersion:
    required = {OperationalCapability.INTERNAL_QUOTE_DRY_RUN: ("legal_entity_registered",)}
    valid_from, valid_until = NOW - timedelta(days=1), NOW + timedelta(days=1)
    return OperationalReadinessPolicyVersion(
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


def snapshot() -> OperationalReadinessSnapshot:
    item = ReadinessItem(
        item_id="legal_entity_registered",
        state=ReadinessItemState.VERIFIED,
        evidence_refs=(provenance(),),
        verified_at=NOW,
    )
    return OperationalReadinessSnapshot(
        readiness_snapshot_id="ops-1",
        resource_id="spec:one",
        observed_at=NOW,
        items=(item,),
        content_fingerprint=stable_fingerprint(
            {
                "resource_id": "spec:one",
                "observed_at": NOW,
                "items": (item,),
            }
        ),
    )


def dependency(kind: ExecutionDependencyKind, value_id: str) -> ExecutionDependencySnapshot:
    return ExecutionDependencySnapshot(
        dependency_id=value_id,
        kind=kind,
        content_fingerprint=f"sha256:{value_id}",
        as_of=NOW,
        valid_until=NOW + timedelta(days=1),
        complete=True,
        hard_block=False,
    )


def test_verified_evidence_production_gate_and_maker_reviewer_approver() -> None:
    with pytest.raises(ValueError, match="evidence"):
        ReadinessItem(item_id="bad", state=ReadinessItemState.VERIFIED, verified_at=NOW)
    repository = InMemoryOperationalReadinessRepository()
    readiness = OperationalReadinessService(repository)
    saved = readiness.save_snapshot(snapshot())
    internal = readiness.evaluate(
        readiness_snapshot_id=saved.readiness_snapshot_id,
        policy=policy(),
        capability=OperationalCapability.INTERNAL_QUOTE_DRY_RUN,
        resource_fingerprint="spec-fp",
        as_of=NOW,
    )
    assert internal.status is ReadinessEvaluationStatus.READY
    customer_evaluation = readiness.evaluate(
        readiness_snapshot_id=saved.readiness_snapshot_id,
        policy=policy(),
        capability=OperationalCapability.CUSTOMER_QUOTE,
        resource_fingerprint="spec-fp",
        as_of=NOW,
    )
    assert customer_evaluation.status is ReadinessEvaluationStatus.BLOCKED
    assert "capability_not_configured" in customer_evaluation.hard_blocks

    cost, risk = (
        dependency(ExecutionDependencyKind.COST, "cost-1"),
        dependency(ExecutionDependencyKind.RISK, "risk-1"),
    )
    audit = Audit()
    clock = [NOW]
    workflow = ApprovalWorkflowService(
        repository,
        cost_reader=Reader(cost),
        risk_reader=Reader(risk),
        authorization=Authorization(),
        audit=audit,
        clock=lambda: clock[0],
    )
    request = workflow.request(
        evaluation_id=internal.readiness_evaluation_id,
        cost_scenario_id=cost.dependency_id,
        cost_scenario_fingerprint=cost.content_fingerprint,
        risk_assessment_id=risk.dependency_id,
        risk_assessment_fingerprint=risk.content_fingerprint,
        maker_id="maker",
        expires_at=NOW + timedelta(hours=1),
        idempotency_key="make-1",
        correlation_id="cor-1",
    )
    reviewed = workflow.decide(
        request.approval_request_id,
        stage=ApprovalStage.REVIEW,
        outcome=ApprovalOutcome.APPROVED,
        actor_id="reviewer",
        rationale="evidence reviewed",
        idempotency_key="review-1",
        correlation_id="cor-2",
    )
    assert reviewed.status.value == "pending_approval"
    approved = workflow.decide(
        request.approval_request_id,
        stage=ApprovalStage.APPROVAL,
        outcome=ApprovalOutcome.APPROVED,
        actor_id="approver",
        rationale="approved",
        idempotency_key="approve-1",
        correlation_id="cor-3",
    )
    assert approved.status.value == "approved"
    assert workflow.is_effectively_approved(request.approval_request_id, NOW)
    with pytest.raises(ValueError, match="did not exist"):
        workflow.is_effectively_approved(
            request.approval_request_id, NOW - timedelta(microseconds=1)
        )
    readiness.evaluate(
        readiness_snapshot_id=saved.readiness_snapshot_id,
        policy=policy(),
        capability=OperationalCapability.INTERNAL_QUOTE_DRY_RUN,
        resource_fingerprint="spec-fp-v2",
        as_of=NOW + timedelta(seconds=1),
    )
    assert not workflow.is_effectively_approved(
        request.approval_request_id, NOW + timedelta(seconds=1)
    )
    assert len(audit.events) == 3
    pending = workflow.request(
        evaluation_id=internal.readiness_evaluation_id,
        cost_scenario_id=cost.dependency_id,
        cost_scenario_fingerprint=cost.content_fingerprint,
        risk_assessment_id=risk.dependency_id,
        risk_assessment_fingerprint=risk.content_fingerprint,
        maker_id="maker",
        expires_at=NOW + timedelta(hours=2),
        idempotency_key="make-2",
        correlation_id="cor-4",
    )
    with pytest.raises(PermissionError, match="different identities"):
        workflow.decide(
            pending.approval_request_id,
            stage=ApprovalStage.REVIEW,
            outcome=ApprovalOutcome.APPROVED,
            actor_id="maker",
            rationale="bad",
            idempotency_key="bad",
            correlation_id="bad",
        )
    workflow.decide(
        pending.approval_request_id,
        stage=ApprovalStage.REVIEW,
        outcome=ApprovalOutcome.APPROVED,
        actor_id="reviewer-2",
        rationale="reviewed before the newer evaluation became effective",
        idempotency_key="review-2",
        correlation_id="cor-5",
    )
    clock[0] = NOW + timedelta(seconds=1)
    with pytest.raises(ValueError, match="no longer effective"):
        workflow.decide(
            pending.approval_request_id,
            stage=ApprovalStage.APPROVAL,
            outcome=ApprovalOutcome.APPROVED,
            actor_id="approver-2",
            rationale="must not approve a stale readiness evaluation",
            idempotency_key="approve-stale",
            correlation_id="cor-6",
        )
