"""M08 backend-first research UI contracts, review store, RBAC, and exports."""

from __future__ import annotations

import csv
import io
import threading
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class Role(StrEnum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    APPROVER = "approver"
    ADMIN = "admin"


class Permission(StrEnum):
    VIEW = "view"
    REVIEW_CLAIM = "review_claim"
    CREATE_EXPERIMENT = "create_experiment"
    APPROVE_EXPERIMENT = "approve_experiment"
    READ_RESTRICTED_RAW = "read_restricted_raw"
    EXPORT = "export"
    EXPORT_RESTRICTED = "export_restricted"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.VIEW, Permission.EXPORT}),
    Role.ANALYST: frozenset({Permission.VIEW, Permission.EXPORT, Permission.CREATE_EXPERIMENT}),
    Role.REVIEWER: frozenset({Permission.VIEW, Permission.EXPORT, Permission.REVIEW_CLAIM}),
    Role.OPERATOR: frozenset({Permission.VIEW, Permission.EXPORT}),
    Role.APPROVER: frozenset({Permission.VIEW, Permission.EXPORT, Permission.APPROVE_EXPERIMENT}),
    # Administration is platform governance, not business approval.
    Role.ADMIN: frozenset({Permission.VIEW, Permission.EXPORT}),
}


class UserContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    user_id: str = Field(min_length=1)
    role: Role
    grants: frozenset[Permission] = frozenset()

    def can(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self.role] or permission in self.grants


class SnapshotContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    snapshot_id: str = Field(min_length=1)
    as_of: datetime
    scoring_version: str = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _aware(value)


class EvidenceNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    node_id: str = Field(min_length=1)
    node_type: str
    label: str
    snapshot_id: str = Field(min_length=1)
    status: str
    parent_id: str | None = None
    locator: dict[str, Any] = {}
    restricted: bool = False


class EvidenceChain(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    opportunity_id: str
    context: SnapshotContext
    nodes: tuple[EvidenceNode, ...]

    def validate_complete(self, required_node_ids: Iterable[str] = ()) -> None:
        if not self.nodes:
            raise ValueError("evidence chain is empty")
        if len({item.node_id for item in self.nodes}) != len(self.nodes):
            raise ValueError("evidence node ids must be unique")
        by_id = {item.node_id: item for item in self.nodes}
        required = set(required_node_ids)
        if not required.issubset(by_id):
            raise ValueError("opportunity evidence node is missing")
        node_types = {item.node_type for item in self.nodes}
        if not {"score", "feature", "claim", "artifact"}.issubset(node_types):
            raise ValueError("evidence chain must include score, feature, claim, and artifact")
        if not node_types.issubset({"score", "feature", "claim", "fact", "artifact"}):
            raise ValueError("evidence chain contains an unsupported or isolated node type")
        for node in self.nodes:
            if node.snapshot_id != self.context.snapshot_id:
                raise ValueError("evidence node snapshot mismatch")
            if node.parent_id and node.parent_id not in {item.node_id for item in self.nodes}:
                raise ValueError("evidence chain is broken")
            seen: set[str] = set()
            current: str | None = node.node_id
            while current is not None:
                if current in seen:
                    raise ValueError("evidence chain contains a cycle")
                seen.add(current)
                current = by_id[current].parent_id
        for node in self.nodes:
            if node.node_type == "score" and node.parent_id is not None:
                raise ValueError("score must be the chain root")
            if node.node_type == "feature" and (
                not node.parent_id or by_id[node.parent_id].node_type != "score"
            ):
                raise ValueError("feature must descend from score")
            if node.node_type in {"claim", "fact"} and (
                not node.parent_id or by_id[node.parent_id].node_type != "feature"
            ):
                raise ValueError("claim/fact must descend from feature")
            if node.node_type == "artifact":
                if not node.parent_id or by_id[node.parent_id].node_type not in {"claim", "fact"}:
                    raise ValueError("artifact must descend from claim/fact")
                if node.restricted and node.locator == {"restricted": True}:
                    continue
                if "artifact_hash" not in node.locator or not any(
                    key in node.locator for key in ("page", "bbox", "start_ms", "url", "offset")
                ):
                    raise ValueError("artifact requires hash and source locator")

    def visible_for(self, user: UserContext) -> EvidenceChain:
        """Return safe metadata for unprivileged users; never expose restricted locators."""
        if user.can(Permission.READ_RESTRICTED_RAW):
            return self
        safe_nodes = tuple(
            node.model_copy(update={"locator": {"restricted": True}}) if node.restricted else node
            for node in self.nodes
        )
        return self.model_copy(update={"nodes": safe_nodes})


class ScoreBreakdown(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    raw_alpha: float | None = None
    risk_penalty: float | None = None
    final_score: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    score_status: str = "stored"


class Opportunity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    opportunity_id: str
    context: SnapshotContext
    title: str
    score: ScoreBreakdown
    statuses: tuple[str, ...] = ()
    stale: bool = False
    evidence_node_ids: tuple[str, ...] = ()


class ReviewTask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    claim_id: str
    snapshot_id: str
    version: int = Field(ge=1)
    status: str = "proposed"
    review_type: str = "claim"


class ReviewAudit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    audit_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    claim_id: str
    snapshot_id: str
    input_version: int
    reviewer_id: str
    decision: str
    reason_code: str = Field(min_length=1)
    note: str | None = None
    prior_version: int
    new_version: int
    downstream_recompute_event_id: str
    decided_at: datetime

    @field_validator("decided_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _aware(value)


class ReviewConflict(RuntimeError):
    """Raised when optimistic review version is stale."""


class ReviewStore:
    def __init__(self, tasks: Iterable[ReviewTask] = ()) -> None:
        self._tasks: dict[str, ReviewTask] = {}
        for task in tasks:
            if task.task_id in self._tasks:
                raise ValueError("duplicate review task id")
            self._tasks[task.task_id] = task
        self._audits: list[ReviewAudit] = []
        self._lock = threading.Lock()

    def get(self, task_id: str) -> ReviewTask:
        return self._tasks[task_id]

    def review(
        self,
        task_id: str,
        user: UserContext,
        *,
        decision: str,
        reason_code: str,
        expected_version: int,
        note: str | None = None,
        decided_at: datetime | None = None,
    ) -> tuple[ReviewTask, ReviewAudit]:
        if not user.can(Permission.REVIEW_CLAIM):
            raise PermissionError("review permission required")
        with self._lock:
            task = self.get(task_id)
            if task.version != expected_version:
                raise ReviewConflict(f"expected version {expected_version}, current {task.version}")
            if decision not in {"accepted", "rejected"} or not reason_code.strip():
                raise ValueError("decision and reason_code are required")
            updated = task.model_copy(update={"status": decision, "version": task.version + 1})
            audit = ReviewAudit(
                task_id=task.task_id,
                claim_id=task.claim_id,
                snapshot_id=task.snapshot_id,
                input_version=task.version,
                reviewer_id=user.user_id,
                decision=decision,
                reason_code=reason_code,
                note=note,
                prior_version=task.version,
                new_version=updated.version,
                downstream_recompute_event_id=str(uuid4()),
                decided_at=decided_at or datetime.now(UTC),
            )
            self._tasks[task_id] = updated
            self._audits.append(audit)
            return updated, audit

    @property
    def audits(self) -> tuple[ReviewAudit, ...]:
        return tuple(self._audits)


class ResearchBackend(Protocol):
    def opportunities(
        self, context: SnapshotContext, *, limit: int = 100
    ) -> tuple[Opportunity, ...]: ...
    def evidence(
        self, opportunity_id: str, context: SnapshotContext, user: UserContext
    ) -> EvidenceChain: ...


class InMemoryBackend:
    def __init__(
        self, opportunities: Iterable[Opportunity], chains: Iterable[EvidenceChain]
    ) -> None:
        self._opportunities = tuple(opportunities)
        self._chains = {chain.opportunity_id: chain for chain in chains}

    def opportunities(
        self, context: SnapshotContext, *, limit: int = 100
    ) -> tuple[Opportunity, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        result = tuple(item for item in self._opportunities if item.context == context)
        for item in result:
            chain = self._chains.get(item.opportunity_id)
            if chain is None:
                raise ValueError("opportunity evidence chain missing")
            chain.validate_complete(item.evidence_node_ids)
        return result[:limit]

    def evidence(
        self, opportunity_id: str, context: SnapshotContext, user: UserContext
    ) -> EvidenceChain:
        chain = self._chains[opportunity_id]
        if chain.context != context:
            raise ValueError("snapshot context mismatch")
        chain.validate_complete()
        return chain.visible_for(user)


class ExportAudit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    export_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    snapshot_id: str
    as_of: datetime
    filters: dict[str, Any]
    row_count: int = Field(ge=0)
    restricted_fields_removed: tuple[str, ...] = ()
    exported_at: datetime

    @field_validator("as_of", "exported_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _aware(value)


class ExportPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    restricted_fields: frozenset[str] = frozenset({"raw", "raw_secret", "raw_content", "raw_text"})


class ExportService:
    def __init__(self, policy: ExportPolicy | None = None) -> None:
        self._audits: list[ExportAudit] = []
        self.policy = policy or ExportPolicy()

    def csv(
        self,
        rows: Sequence[Mapping[str, Any]],
        user: UserContext,
        context: SnapshotContext,
        *,
        filters: Mapping[str, Any] | None = None,
    ) -> tuple[str, ExportAudit]:
        if not user.can(Permission.EXPORT):
            raise PermissionError("export permission required")
        restricted_fields = self.policy.restricted_fields
        if user.can(Permission.EXPORT_RESTRICTED):
            restricted_fields = frozenset()
        visible = [
            {key: value for key, value in row.items() if key not in restricted_fields}
            for row in rows
        ]
        fields = sorted({key for row in visible for key in row})
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(visible)
        audit = ExportAudit(
            user_id=user.user_id,
            snapshot_id=context.snapshot_id,
            as_of=context.as_of,
            filters=dict(filters or {}),
            row_count=len(visible),
            restricted_fields_removed=tuple(sorted(restricted_fields)),
            exported_at=datetime.now(UTC),
        )
        self._audits.append(audit)
        return output.getvalue(), audit

    @property
    def audits(self) -> tuple[ExportAudit, ...]:
        return tuple(self._audits)


class PerspectiveAdapter:
    """Small real Perspective table adapter for sorting/grouping in the UI."""

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        import perspective  # type: ignore[import-untyped]

        self._table = perspective.table([dict(row) for row in rows])

    def records(self, *, group_by: Sequence[str] = ()) -> list[dict[str, Any]]:
        view = self._table.view(group_by=list(group_by)) if group_by else self._table.view()
        try:
            return [dict(record) for record in view.to_records()]
        finally:
            view.delete()

    def close(self) -> None:
        self._table.delete()


def freshness_status(as_of: datetime, retrieved_at: datetime, *, sla: timedelta) -> str:
    age = _aware(as_of) - _aware(retrieved_at)
    return "stale" if age > sla else "current"


def display_status(status: str) -> str:
    labels = {
        "proposed_claim": "Proposed claim",
        "accepted_claim": "Accepted claim",
        "forecast": "Forecast",
        "missing": "Missing",
        "stale": "Stale",
    }
    return labels.get(status, status.replace("_", " ").title())


def render_streamlit(backend: ResearchBackend, user: UserContext) -> None:
    """Minimal real Streamlit adapter; all data and scores come from the backend."""
    import streamlit as st

    st.title("Trade Alpha Research Station")
    snapshot_id = st.text_input("Snapshot ID")
    as_of_text = st.text_input("As-of (ISO-8601)")
    scoring_version = st.text_input("Scoring version", value="stored")
    if not snapshot_id or not as_of_text:
        st.info("Enter snapshot_id and as_of to query a consistent snapshot.")
        return
    context = SnapshotContext(
        snapshot_id=snapshot_id,
        as_of=datetime.fromisoformat(as_of_text),
        scoring_version=scoring_version,
    )
    opportunities = backend.opportunities(context)
    st.caption(f"Snapshot: {context.snapshot_id} | As-of: {context.as_of.isoformat()}")
    st.dataframe(
        [
            {
                "opportunity_id": item.opportunity_id,
                "title": item.title,
                "final_score": item.score.final_score,
                "status": ", ".join(display_status(status) for status in item.statuses),
            }
            for item in opportunities
        ]
    )


__all__ = [
    "EvidenceChain",
    "EvidenceNode",
    "ExportAudit",
    "ExportPolicy",
    "ExportService",
    "InMemoryBackend",
    "Opportunity",
    "PerspectiveAdapter",
    "Permission",
    "ReviewAudit",
    "ReviewConflict",
    "ReviewStore",
    "ReviewTask",
    "Role",
    "ScoreBreakdown",
    "SnapshotContext",
    "UserContext",
    "freshness_status",
    "display_status",
    "render_streamlit",
]
