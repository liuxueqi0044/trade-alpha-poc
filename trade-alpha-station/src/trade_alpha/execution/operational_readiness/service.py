"""Evidence-first readiness evaluation and exact maker-reviewer-approver workflow."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint

from .models import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalRequestStatus,
    ApprovalStage,
    ExecutionDependencyKind,
    ExecutionDependencySnapshot,
    OperationalCapability,
    OperationalReadinessPolicyVersion,
    OperationalReadinessSnapshot,
    ReadinessEvaluationSnapshot,
    ReadinessEvaluationStatus,
    ReadinessItemState,
    approval_request_fingerprint,
    evaluation_fingerprint,
    readiness_evaluation_fingerprint,
    readiness_policy_fingerprint,
    readiness_snapshot_fingerprint,
)
from .ports import ExecutionAuditPort, ExecutionAuthorizationPort, ExecutionDependencyReader
from .repository import InMemoryOperationalReadinessRepository


class OperationalReadinessService:
    def __init__(self, repository: InMemoryOperationalReadinessRepository) -> None:
        self.repository = repository

    def save_snapshot(self, snapshot: OperationalReadinessSnapshot) -> OperationalReadinessSnapshot:
        if type(snapshot) is not OperationalReadinessSnapshot:
            raise TypeError("operational readiness snapshot has an invalid type")
        OperationalReadinessSnapshot.model_validate(snapshot.model_dump())
        if snapshot.content_fingerprint != readiness_snapshot_fingerprint(snapshot):
            raise ValueError("operational readiness snapshot content fingerprint mismatch")
        if len({item.item_id for item in snapshot.items}) != len(snapshot.items):
            raise ValueError("readiness snapshot item ids must be unique")
        return self.repository.save_snapshot(snapshot)

    def evaluate(
        self,
        *,
        readiness_snapshot_id: str,
        policy: OperationalReadinessPolicyVersion,
        capability: OperationalCapability,
        resource_fingerprint: str,
        as_of: datetime,
    ) -> ReadinessEvaluationSnapshot:
        moment = require_utc(as_of)
        if type(policy) is not OperationalReadinessPolicyVersion:
            raise TypeError("operational readiness policy has an invalid type")
        OperationalReadinessPolicyVersion.model_validate(policy.model_dump())
        if policy.config_fingerprint != readiness_policy_fingerprint(policy):
            raise ValueError("readiness policy config_fingerprint mismatch")
        snapshot = self.repository.snapshot(readiness_snapshot_id)
        if snapshot is None:
            raise ValueError("readiness snapshot not found")
        if snapshot.observed_at > moment:
            raise ValueError("readiness snapshot is newer than evaluation as_of")
        if moment < policy.valid_from or (
            policy.valid_until is not None and moment > policy.valid_until
        ):
            raise ValueError("readiness policy is not valid at as_of")
        blocks: list[str] = []
        if capability not in policy.required_items:
            blocks.append("capability_not_configured")
        if (
            capability is not OperationalCapability.INTERNAL_QUOTE_DRY_RUN
            and not policy.production_actions_enabled
        ):
            blocks.append("production_actions_disabled")
        by_id = {item.item_id: item for item in snapshot.items}
        for item_id in policy.required_items.get(capability, ()):
            item = by_id.get(item_id)
            if item is None:
                blocks.append(f"missing:{item_id}")
            elif item.state is not ReadinessItemState.VERIFIED:
                blocks.append(f"{item.state.value}:{item_id}")
            elif item.verified_at is None or item.verified_at > moment:
                blocks.append(f"invalid_verification_time:{item_id}")
            elif any(ref.observed_at > moment for ref in item.evidence_refs):
                blocks.append(f"future_evidence:{item_id}")
        payload: dict[str, object] = {
            "resource_id": snapshot.resource_id,
            "resource_fingerprint": resource_fingerprint,
            "snapshot": snapshot.readiness_snapshot_id,
            "snapshot_fingerprint": snapshot.content_fingerprint,
            "policy": (policy.policy_id, policy.version, policy.config_fingerprint),
            "capability": capability,
            "as_of": moment,
            "hard_blocks": tuple(sorted(blocks)),
        }
        fingerprint = evaluation_fingerprint(payload)
        result = ReadinessEvaluationSnapshot(
            readiness_evaluation_id=str(
                uuid5(NAMESPACE_URL, f"trade-alpha/ep09/evaluation/{fingerprint}")
            ),
            resource_id=snapshot.resource_id,
            resource_fingerprint=resource_fingerprint,
            readiness_snapshot_id=snapshot.readiness_snapshot_id,
            readiness_snapshot_fingerprint=snapshot.content_fingerprint,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            policy_config_fingerprint=policy.config_fingerprint,
            capability=capability,
            as_of=moment,
            status=ReadinessEvaluationStatus.BLOCKED if blocks else ReadinessEvaluationStatus.READY,
            hard_blocks=tuple(sorted(blocks)),
            content_fingerprint=fingerprint,
        )
        return self.repository.save_evaluation(result)


class ApprovalWorkflowService:
    def __init__(
        self,
        repository: InMemoryOperationalReadinessRepository,
        *,
        cost_reader: ExecutionDependencyReader,
        risk_reader: ExecutionDependencyReader,
        authorization: ExecutionAuthorizationPort,
        audit: ExecutionAuditPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.cost_reader = cost_reader
        self.risk_reader = risk_reader
        self.authorization = authorization
        self.audit = audit
        self.clock = clock or (lambda: datetime.now(UTC))

    def request(
        self,
        *,
        evaluation_id: str,
        cost_scenario_id: str,
        cost_scenario_fingerprint: str,
        risk_assessment_id: str,
        risk_assessment_fingerprint: str,
        maker_id: str,
        expires_at: datetime,
        idempotency_key: str,
        correlation_id: str,
    ) -> ApprovalRequest:
        evaluation = self._evaluation(evaluation_id)
        now = require_utc(self.clock())
        expiry = require_utc(expires_at)
        self.authorization.require(maker_id, "execution.approval.make", evaluation.resource_id, now)
        if evaluation.status is not ReadinessEvaluationStatus.READY:
            raise ValueError("blocked readiness evaluation cannot be submitted")
        if evaluation.as_of > now:
            raise ValueError("readiness evaluation is newer than approval request")
        if not self.repository.is_current_evaluation_as_of(evaluation_id, now):
            raise ValueError("readiness evaluation is not the current lineage tip")
        cost = self._dependency(
            self.cost_reader, cost_scenario_id, now, ExecutionDependencyKind.COST
        )
        risk = self._dependency(
            self.risk_reader, risk_assessment_id, now, ExecutionDependencyKind.RISK
        )
        if (
            cost.content_fingerprint != cost_scenario_fingerprint
            or risk.content_fingerprint != risk_assessment_fingerprint
        ):
            raise ValueError("approval dependencies have a content fingerprint mismatch")
        if not cost.complete or risk.hard_block:
            raise ValueError("incomplete cost or hard-block risk cannot be submitted")
        if expiry <= now:
            raise ValueError("approval request must expire in the future")
        payload = {
            "evaluation": evaluation.content_fingerprint,
            "cost_id": cost.dependency_id,
            "cost": cost.content_fingerprint,
            "risk_id": risk.dependency_id,
            "risk": risk.content_fingerprint,
            "maker": maker_id,
            "expires": expiry,
        }
        request_fingerprint = stable_fingerprint(payload)
        if existing := self.repository.idempotent(idempotency_key, request_fingerprint):
            if type(existing) is not ApprovalRequest:
                raise ValueError("idempotency key belongs to another operation")
            return existing
        value = ApprovalRequest(
            approval_request_id=str(
                uuid5(NAMESPACE_URL, f"trade-alpha/ep09/request/{request_fingerprint}")
            ),
            resource_id=evaluation.resource_id,
            resource_fingerprint=evaluation.resource_fingerprint,
            readiness_evaluation_id=evaluation.readiness_evaluation_id,
            readiness_evaluation_fingerprint=evaluation.content_fingerprint,
            cost_scenario_id=cost.dependency_id,
            cost_scenario_fingerprint=cost.content_fingerprint,
            risk_assessment_id=risk.dependency_id,
            risk_assessment_fingerprint=risk.content_fingerprint,
            maker_id=maker_id,
            created_at=now,
            expires_at=expiry,
            idempotency_key=idempotency_key,
            content_fingerprint=request_fingerprint,
        )
        result = self.repository.save_request(value)
        self.repository.save_idempotent(idempotency_key, request_fingerprint, result)
        self._audit(
            maker_id,
            "execution.approval.requested",
            result.resource_id,
            None,
            result,
            now,
            correlation_id,
        )
        return result

    def decide(
        self,
        request_id: str,
        *,
        stage: ApprovalStage,
        outcome: ApprovalOutcome,
        actor_id: str,
        rationale: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> ApprovalRequest:
        request = self._request(request_id)
        now = require_utc(self.clock())
        decision_payload = {
            "request": request.content_fingerprint,
            "stage": stage,
            "outcome": outcome,
            "actor": actor_id,
            "rationale": rationale,
        }
        fingerprint = stable_fingerprint(decision_payload)
        if existing := self.repository.idempotent(idempotency_key, fingerprint):
            if type(existing) is not ApprovalRequest:
                raise ValueError("idempotency key belongs to another operation")
            return existing
        if request.status not in {
            ApprovalRequestStatus.PENDING_REVIEW,
            ApprovalRequestStatus.PENDING_APPROVAL,
        }:
            raise ValueError("approval request is terminal")
        if request.expires_at <= now:
            expired = self.repository.replace_request(
                request.model_copy(update={"status": ApprovalRequestStatus.EXPIRED})
            )
            self._audit(
                actor_id,
                "execution.approval.expired",
                request.resource_id,
                request,
                expired,
                now,
                correlation_id,
            )
            return expired
        expected = (
            ApprovalStage.REVIEW
            if request.status is ApprovalRequestStatus.PENDING_REVIEW
            else ApprovalStage.APPROVAL
        )
        if stage is not expected:
            raise ValueError("approval decision stage does not match request state")
        if actor_id == request.maker_id or any(
            item.actor_id == actor_id for item in self.repository.decisions(request_id)
        ):
            raise PermissionError("maker, reviewer, and approver must be different identities")
        self.authorization.require(
            actor_id, f"execution.approval.{stage.value}", request.resource_id, now
        )
        if now < request.created_at:
            raise ValueError("approval decision cannot predate request")
        # Re-resolve all gates, making approvals non-portable if their inputs change.
        evaluation = self._evaluation(request.readiness_evaluation_id)
        if (
            evaluation.content_fingerprint != request.readiness_evaluation_fingerprint
            or evaluation.status is not ReadinessEvaluationStatus.READY
            or not self.repository.is_current_evaluation_as_of(
                evaluation.readiness_evaluation_id, now
            )
        ):
            raise ValueError("readiness evaluation is no longer effective")
        cost = self._dependency(
            self.cost_reader, request.cost_scenario_id, now, ExecutionDependencyKind.COST
        )
        risk = self._dependency(
            self.risk_reader, request.risk_assessment_id, now, ExecutionDependencyKind.RISK
        )
        if (
            cost.content_fingerprint != request.cost_scenario_fingerprint
            or risk.content_fingerprint != request.risk_assessment_fingerprint
        ):
            raise ValueError("approval dependencies changed")
        if not cost.complete or risk.hard_block:
            raise ValueError("approval dependencies are no longer eligible")
        decision = ApprovalDecision(
            decision_id=str(uuid5(NAMESPACE_URL, f"trade-alpha/ep09/decision/{fingerprint}")),
            approval_request_id=request_id,
            request_fingerprint=request.content_fingerprint,
            stage=stage,
            outcome=outcome,
            actor_id=actor_id,
            rationale=rationale,
            decided_at=now,
            idempotency_key=idempotency_key,
        )
        if any(item.stage is stage for item in self.repository.decisions(request_id)):
            raise ValueError("only one effective terminal decision is allowed per stage")
        status = self._next_status(stage, outcome)
        updated = request.model_copy(update={"status": status})
        updated = self.repository.commit_decision(
            expected_status=request.status,
            decision=decision,
            updated=updated,
        )
        self.repository.save_idempotent(idempotency_key, fingerprint, updated)
        self._audit(
            actor_id,
            f"execution.approval.{stage.value}.{outcome.value}",
            request.resource_id,
            request,
            updated,
            now,
            correlation_id,
        )
        return updated

    def is_effectively_approved(self, request_id: str, as_of: datetime) -> bool:
        request = self._request(request_id)
        moment = require_utc(as_of)
        if self.status_as_of(request_id, moment) is not ApprovalRequestStatus.APPROVED:
            return False
        evaluation = self._evaluation(request.readiness_evaluation_id)
        if (
            evaluation.as_of > moment
            or evaluation.content_fingerprint != request.readiness_evaluation_fingerprint
            or evaluation.status is not ReadinessEvaluationStatus.READY
            or not self.repository.is_current_evaluation_as_of(
                evaluation.readiness_evaluation_id, moment
            )
        ):
            return False
        try:
            cost = self._dependency(
                self.cost_reader, request.cost_scenario_id, moment, ExecutionDependencyKind.COST
            )
            risk = self._dependency(
                self.risk_reader, request.risk_assessment_id, moment, ExecutionDependencyKind.RISK
            )
        except (TypeError, ValueError):
            return False
        return (
            cost.content_fingerprint == request.cost_scenario_fingerprint
            and risk.content_fingerprint == request.risk_assessment_fingerprint
            and cost.complete
            and not risk.hard_block
        )

    def status_as_of(self, request_id: str, as_of: datetime) -> ApprovalRequestStatus:
        """Rebuild approval state from append-only decisions at a point in time."""
        request = self._request(request_id)
        moment = require_utc(as_of)
        if moment < request.created_at:
            raise ValueError("approval request did not exist at as_of")
        if request.expires_at <= moment:
            return ApprovalRequestStatus.EXPIRED
        decisions = tuple(
            sorted(
                (
                    item
                    for item in self.repository.decisions(request_id)
                    if item.decided_at <= moment
                ),
                key=lambda item: item.decided_at,
            )
        )
        status = ApprovalRequestStatus.PENDING_REVIEW
        seen_actors = {request.maker_id}
        for decision in decisions:
            if (
                decision.request_fingerprint != request.content_fingerprint
                or decision.decided_at < request.created_at
                or decision.actor_id in seen_actors
            ):
                raise ValueError("approval decision history is invalid")
            expected = (
                ApprovalStage.REVIEW
                if status is ApprovalRequestStatus.PENDING_REVIEW
                else ApprovalStage.APPROVAL
                if status is ApprovalRequestStatus.PENDING_APPROVAL
                else None
            )
            if decision.stage is not expected:
                raise ValueError("approval decision history has an invalid stage sequence")
            seen_actors.add(decision.actor_id)
            status = self._next_status(decision.stage, decision.outcome)
        return status

    def approval_request(self, request_id: str) -> ApprovalRequest:
        """Return the immutable saved request for EP-10's narrow approval reader."""
        return self._request(request_id)

    def _dependency(
        self,
        reader: ExecutionDependencyReader,
        dependency_id: str,
        as_of: datetime,
        kind: ExecutionDependencyKind,
    ) -> ExecutionDependencySnapshot:
        value = reader.get_saved_as_of(dependency_id, as_of)
        if type(value) is not ExecutionDependencySnapshot:
            raise TypeError("dependency reader returned an invalid snapshot type")
        if (
            value.kind is not kind
            or value.as_of > as_of
            or (value.valid_until is not None and value.valid_until < as_of)
            or not value.is_lineage_tip
        ):
            raise ValueError("dependency is not effective at approval time")
        return value

    def _evaluation(self, value_id: str) -> ReadinessEvaluationSnapshot:
        value = self.repository.evaluation(value_id)
        if value is None:
            raise ValueError("readiness evaluation not found")
        if value.content_fingerprint != readiness_evaluation_fingerprint(value):
            raise ValueError("readiness evaluation content fingerprint mismatch")
        return value

    def _request(self, value_id: str) -> ApprovalRequest:
        value = self.repository.request(value_id)
        if value is None:
            raise ValueError("approval request not found")
        if value.content_fingerprint != approval_request_fingerprint(value):
            raise ValueError("approval request content fingerprint mismatch")
        return value

    @staticmethod
    def _next_status(stage: ApprovalStage, outcome: ApprovalOutcome) -> ApprovalRequestStatus:
        if outcome is ApprovalOutcome.REJECTED:
            return ApprovalRequestStatus.REJECTED
        if outcome is ApprovalOutcome.REQUEST_CHANGES:
            return ApprovalRequestStatus.CHANGES_REQUESTED
        return (
            ApprovalRequestStatus.PENDING_APPROVAL
            if stage is ApprovalStage.REVIEW
            else ApprovalRequestStatus.APPROVED
        )

    def _audit(
        self,
        actor: str,
        action: str,
        resource: str,
        before: object,
        after: object,
        at: datetime,
        correlation_id: str,
    ) -> None:
        self.audit.append(
            actor_id=actor,
            action=action,
            resource_id=resource,
            before=before,
            after=after,
            occurred_at=at,
            reason=action,
            correlation_id=correlation_id,
        )
