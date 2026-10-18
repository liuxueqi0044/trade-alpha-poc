"""Deny-by-default authorization enforced below the presentation layer."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .audit import AppendOnlyAuditLog
from .models import (
    Action,
    DataClassification,
    ExportReceipt,
    Identity,
    PolicyDecision,
    ProtectedResource,
    Role,
    stable_hash,
)
from .registry import LicenseRegistry
from .security import SecretScanner, neutralize_csv_cell


class AuthorizationDenied(PermissionError):
    pass


_ROLE_ACTIONS: dict[Role, frozenset[Action]] = {
    Role.VIEWER: frozenset({Action.READ}),
    Role.ANALYST: frozenset({Action.READ, Action.EXPORT, Action.PROPOSE_EXPERIMENT}),
    Role.REVIEWER: frozenset(
        {Action.READ, Action.READ_RAW, Action.EXPORT, Action.REVIEW, Action.REVIEW_EXECUTION}
    ),
    Role.OPERATOR: frozenset({Action.READ, Action.READ_RAW, Action.OPERATE_PIPELINE}),
    Role.APPROVER: frozenset(
        {Action.READ, Action.EXPORT, Action.APPROVE_EXPERIMENT, Action.APPROVE_EXECUTION}
    ),
    # Deliberately excludes both approval actions (M10-AC-013).
    Role.ADMIN: frozenset({Action.READ, Action.MANAGE_SYSTEM, Action.MANAGE_ROLE}),
}


class PolicyEngine:
    def decide(
        self,
        identity: Identity,
        action: Action,
        resource: ProtectedResource,
        at: datetime,
    ) -> PolicyDecision:
        role_actions = frozenset(
            granted for role in identity.roles for granted in _ROLE_ACTIONS.get(role, frozenset())
        )
        if action not in role_actions:
            return PolicyDecision(
                allowed=False,
                reason="role_action_denied",
                actor_id=identity.actor_id,
                action=action,
                resource_id=resource.resource_id,
                evaluated_at=at,
            )
        required_grant = self._classification_grant(action, resource.classification)
        if required_grant and required_grant not in identity.grants:
            return PolicyDecision(
                allowed=False,
                reason=f"missing_explicit_grant:{required_grant}",
                actor_id=identity.actor_id,
                action=action,
                resource_id=resource.resource_id,
                evaluated_at=at,
            )
        return PolicyDecision(
            allowed=True,
            reason="allowed_by_role_and_classification",
            actor_id=identity.actor_id,
            action=action,
            resource_id=resource.resource_id,
            evaluated_at=at,
        )

    def require(
        self,
        identity: Identity,
        action: Action,
        resource: ProtectedResource,
        at: datetime,
    ) -> PolicyDecision:
        decision = self.decide(identity, action, resource, at)
        if not decision.allowed:
            raise AuthorizationDenied(
                f"{action.value} denied for {resource.resource_id}: {decision.reason}"
            )
        return decision

    @staticmethod
    def _classification_grant(
        action: Action, classification: DataClassification
    ) -> str | None:
        if classification in {DataClassification.PUBLIC, DataClassification.INTERNAL}:
            return None
        operation = "export" if action is Action.EXPORT else "read"
        return f"{operation}:{classification.value}"


class ProtectedDataGateway:
    """The only read boundary; direct API calls receive the same policy check as UI calls."""

    def __init__(
        self, policy: PolicyEngine, licenses: LicenseRegistry | None = None
    ) -> None:
        self._policy = policy
        self._licenses = licenses
        self._values: dict[str, tuple[ProtectedResource, Any]] = {}

    def seed(self, resource: ProtectedResource, value: Any) -> None:
        if resource.resource_id in self._values:
            raise ValueError(f"duplicate resource: {resource.resource_id}")
        self._values[resource.resource_id] = (resource, value)

    def read(
        self, identity: Identity, resource_id: str, at: datetime, *, raw: bool = False
    ) -> Any:
        resource, value = self._values[resource_id]
        self._policy.require(identity, Action.READ_RAW if raw else Action.READ, resource, at)
        if resource.source_id and self._licenses:
            source_decision = self._licenses.export_policy(resource.source_id)
            if not source_decision.allowed:
                raise AuthorizationDenied(
                    f"read denied for {resource.resource_id}: source_policy_blocked"
                )
        return value


class RoleManager:
    def __init__(self, policy: PolicyEngine, audit: AppendOnlyAuditLog) -> None:
        self._policy = policy
        self._audit = audit

    def grant_role(
        self,
        *,
        admin: Identity,
        subject: Identity,
        role: Role,
        at: datetime,
        reason: str,
        correlation_id: str,
    ) -> Identity:
        role_resource = ProtectedResource(
            resource_id=f"identity:{subject.actor_id}",
            classification=DataClassification.INTERNAL,
            owner="M10",
        )
        self._policy.require(admin, Action.MANAGE_ROLE, role_resource, at)
        updated = subject.model_copy(update={"roles": frozenset({*subject.roles, role})})
        self._audit.append(
            identity=admin,
            action="role.granted",
            resource=role_resource.resource_id,
            before=subject,
            after=updated,
            occurred_at=at,
            reason=reason,
            correlation_id=correlation_id,
        )
        return updated


class ExportController:
    def __init__(
        self,
        policy: PolicyEngine,
        audit: AppendOnlyAuditLog,
        licenses: LicenseRegistry | None = None,
    ) -> None:
        self._policy = policy
        self._audit = audit
        self._licenses = licenses
        self._receipts: list[ExportReceipt] = []

    @property
    def receipts(self) -> tuple[ExportReceipt, ...]:
        return tuple(self._receipts)

    def export_csv(
        self,
        *,
        identity: Identity,
        resource: ProtectedResource,
        rows: Sequence[Mapping[str, Any]],
        snapshot_id: str,
        filters: Mapping[str, Any],
        at: datetime,
        correlation_id: str,
    ) -> tuple[bytes, ExportReceipt]:
        self._policy.require(identity, Action.EXPORT, resource, at)
        if resource.source_id and self._licenses:
            source_decision = self._licenses.source_policy(resource.source_id)
            if not source_decision.allowed:
                raise AuthorizationDenied(
                    f"export denied for {resource.resource_id}: source_policy_blocked"
                )
        columns = sorted({key for row in rows for key in row})
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: neutralize_csv_cell(row.get(key)) for key in columns})
        content = output.getvalue().encode("utf-8")
        SecretScanner().assert_clean({"export": content})
        receipt = ExportReceipt(
            export_id=f"export:{len(self._receipts) + 1}",
            actor=identity.actor_id,
            resource_id=resource.resource_id,
            classification=resource.classification,
            snapshot_id=snapshot_id,
            filter_hash=stable_hash(filters),
            row_count=len(rows),
            exported_at=at,
            content_hash=stable_hash(content.hex()),
        )
        self._receipts.append(receipt)
        self._audit.append(
            identity=identity,
            action="data.exported",
            resource=resource.resource_id,
            before={"filter_hash": receipt.filter_hash, "snapshot_id": snapshot_id},
            after=receipt,
            occurred_at=at,
            reason="authorized export",
            correlation_id=correlation_id,
        )
        return content, receipt
