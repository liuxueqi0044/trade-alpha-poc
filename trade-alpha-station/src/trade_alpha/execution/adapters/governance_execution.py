"""M10-backed authorization and audit adapters for EP-08/EP-09 dry runs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from trade_alpha.governance import (
    Action,
    AppendOnlyAuditLog,
    DataClassification,
    Identity,
    PolicyEngine,
    ProtectedResource,
)


class GovernanceExecutionAuthorizationAdapter:
    """Map narrow execution actions to explicit M10 actions, deny by default."""

    _ACTIONS = {
        "execution.approval.make": Action.OPERATE_PIPELINE,
        "execution.approval.review": Action.REVIEW_EXECUTION,
        "execution.approval.approval": Action.APPROVE_EXECUTION,
    }

    def __init__(self, policy: PolicyEngine, identities: tuple[Identity, ...]) -> None:
        self.policy = policy
        self.identities = {identity.actor_id: identity for identity in identities}
        if len(self.identities) != len(identities):
            raise ValueError("execution identities must have unique actor IDs")

    def require(self, actor_id: str, action: str, resource_id: str, at: datetime) -> None:
        identity = self.identities.get(actor_id)
        mapped = self._ACTIONS.get(action)
        if identity is None or mapped is None:
            raise PermissionError("execution actor or action is not authorized")
        self.policy.require(
            identity,
            mapped,
            ProtectedResource(
                resource_id=resource_id,
                classification=DataClassification.INTERNAL,
                owner="EP09",
            ),
            at,
        )


class GovernanceApprovalAuditAdapter:
    """Write EP-09 approval transitions into M10's tamper-evident journal."""

    def __init__(self, audit: AppendOnlyAuditLog, identities: tuple[Identity, ...]) -> None:
        self.audit = audit
        self.identities = {identity.actor_id: identity for identity in identities}
        if len(self.identities) != len(identities):
            raise ValueError("audit identities must have unique actor IDs")

    def append(
        self,
        *,
        actor_id: str,
        action: str,
        resource_id: str,
        before: object,
        after: object,
        occurred_at: datetime,
        reason: str,
        correlation_id: str,
    ) -> str:
        identity = self.identities.get(actor_id)
        if identity is None:
            raise PermissionError("unknown execution audit actor")
        return self.audit.append(
            identity=identity,
            action=action,
            resource=resource_id,
            before=before,
            after=after,
            occurred_at=occurred_at,
            reason=reason,
            correlation_id=correlation_id,
        ).event_hash


class GovernanceRiskAuditAdapter:
    """Write EP-08 assessments and decisions into the same M10 audit chain."""

    def __init__(self, audit: AppendOnlyAuditLog, identities: tuple[Identity, ...]) -> None:
        self.audit = audit
        self.identities = {identity.actor_id: identity for identity in identities}
        if len(self.identities) != len(identities):
            raise ValueError("audit identities must have unique actor IDs")

    def append(
        self,
        *,
        actor_id: str,
        client_identity: str,
        action: str,
        resource: str,
        before: Any,
        after: Any,
        occurred_at: datetime,
        reason: str,
        correlation_id: str,
    ) -> Any:
        identity = self.identities.get(actor_id)
        if identity is None or identity.client_identity != client_identity:
            raise PermissionError("risk audit identity does not match M10 identity")
        return self.audit.append(
            identity=identity,
            action=action,
            resource=resource,
            before=before,
            after=after,
            occurred_at=occurred_at,
            reason=reason,
            correlation_id=correlation_id,
        )


class GovernanceAuditChainVerifierAdapter:
    """Expose M10's hash journal through EP-10's narrow verifier contract."""

    def __init__(
        self,
        audit: AppendOnlyAuditLog,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.audit = audit
        self.clock = clock or (lambda: datetime.now(UTC))

    def verify(self) -> bool:
        return self.audit.verify().valid

    def tip_hash(self) -> str:
        return (
            self.audit.events[-1].event_hash
            if self.audit.events
            else AppendOnlyAuditLog.GENESIS_HASH
        )

    def verified_at(self) -> datetime:
        return self.clock()
