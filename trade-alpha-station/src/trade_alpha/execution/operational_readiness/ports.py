"""Narrow EP-09 ports; M10 adapters can be attached without changing the domain."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import ExecutionDependencySnapshot


class ExecutionAuthorizationPort(Protocol):
    def require(self, actor_id: str, action: str, resource_id: str, at: datetime) -> None: ...


class ExecutionAuditPort(Protocol):
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
    ) -> str: ...


class ExecutionDependencyReader(Protocol):
    def get_saved_as_of(
        self, dependency_id: str, as_of: datetime
    ) -> ExecutionDependencySnapshot | None: ...
