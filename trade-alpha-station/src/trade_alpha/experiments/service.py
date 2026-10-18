"""Append-only state machine and controlled connector reference implementation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from threading import Lock

from trade_alpha.contracts.common import require_utc

from .models import (
    ActionDraft,
    BusinessEvent,
    Experiment,
    ExperimentStatus,
    Money,
    OutcomeKind,
    OutcomeLabel,
    Role,
)


class RBACDenied(PermissionError):
    pass


class StateTransitionError(ValueError):
    pass


class PolicyBlocked(ValueError):
    pass


@dataclass(frozen=True)
class ActionApproval:
    action_id: str
    approver: str
    approved_at: datetime


@dataclass(frozen=True)
class ActionAttempt:
    action_id: str
    attempt_number: int
    idempotency_key: str
    occurred_at: datetime
    external_called: bool
    result_ref: str


class ActionConnector:
    """Mockable connector boundary; unsafe commercial commitments are forbidden."""

    def __init__(self) -> None:
        self.external_effects: list[str] = []

    def send(self, action: ActionDraft) -> str:
        self.external_effects.append(action.idempotency_key)
        return f"receipt:{action.idempotency_key}"


class ExperimentRepository:
    """In-memory reference for append-only PostgreSQL event-table semantics."""

    _TRANSITIONS = {
        ExperimentStatus.PROPOSED: {ExperimentStatus.APPROVED, ExperimentStatus.REJECTED},
        ExperimentStatus.APPROVED: {ExperimentStatus.RUNNING, ExperimentStatus.STOPPED},
        ExperimentStatus.RUNNING: {
            ExperimentStatus.PAUSED,
            ExperimentStatus.COMPLETED,
            ExperimentStatus.STOPPED,
        },
        ExperimentStatus.PAUSED: {ExperimentStatus.RUNNING, ExperimentStatus.STOPPED},
        ExperimentStatus.COMPLETED: {ExperimentStatus.EVALUATED},
        ExperimentStatus.STOPPED: {ExperimentStatus.EVALUATED},
        ExperimentStatus.EVALUATED: set(),
        ExperimentStatus.REJECTED: set(),
    }
    _PROHIBITED_ACTIONS = frozenset({"payment", "contract_signature", "order_commitment"})

    def __init__(self) -> None:
        self._experiments: dict[str, Experiment] = {}
        self._status_history: dict[str, list[tuple[ExperimentStatus, str, datetime, str]]] = (
            defaultdict(list)
        )
        self._actions: dict[str, ActionDraft] = {}
        self._approvals: dict[str, ActionApproval] = {}
        self._attempts: list[ActionAttempt] = []
        self._reserved_keys: dict[str, str] = {}
        self._reservation_lock = Lock()
        self._events: list[BusinessEvent] = []
        self._outcomes: list[OutcomeLabel] = []

    def propose(self, experiment: Experiment, actor: str, role: Role, at: datetime) -> None:
        self._require(role, Role.ANALYST)
        if actor != experiment.owner or experiment.status is not ExperimentStatus.PROPOSED:
            raise RBACDenied("only the owner analyst may propose a proposed experiment")
        if experiment.experiment_id in self._experiments:
            raise ValueError("experiment_id already exists; frozen snapshot cannot be replaced")
        self._experiments[experiment.experiment_id] = experiment
        self._status_history[experiment.experiment_id].append(
            (experiment.status, actor, require_utc(at), "proposed")
        )

    def approve_experiment(
        self, experiment_id: str, actor: str, role: Role, at: datetime
    ) -> Experiment:
        self._require(role, Role.APPROVER)
        experiment = self._experiment(experiment_id)
        if actor != experiment.approver or actor == experiment.owner:
            raise RBACDenied("designated approver must be distinct from owner")
        missing = []
        if experiment.budget is None:
            missing.append("budget")
        if not experiment.stop_conditions:
            missing.append("stop_conditions")
        if not experiment.success_definition:
            missing.append("success_definition")
        if experiment.start_at is None or experiment.end_at is None:
            missing.append("term")
        if missing:
            raise StateTransitionError(f"approval requires: {', '.join(missing)}")
        return self._transition(experiment_id, ExperimentStatus.APPROVED, actor, at, "approved")

    def transition(
        self, experiment_id: str, target: ExperimentStatus, actor: str, role: Role, at: datetime
    ) -> Experiment:
        self._require(role, Role.ANALYST)
        if actor != self._experiment(experiment_id).owner:
            raise RBACDenied("only experiment owner may perform ordinary state transitions")
        if target in {
            ExperimentStatus.APPROVED,
            ExperimentStatus.REJECTED,
            ExperimentStatus.STOPPED,
        }:
            raise RBACDenied("approval/rejection/stop require their dedicated controlled flows")
        return self._transition(experiment_id, target, actor, at, "state transition")

    def stop(
        self, experiment_id: str, actor: str, role: Role, reason: str, at: datetime
    ) -> Experiment:
        if role not in {Role.APPROVER, Role.ADMIN}:
            raise RBACDenied("only approver or admin may stop an experiment")
        return self._transition(experiment_id, ExperimentStatus.STOPPED, actor, at, reason)

    def draft_action(self, action: ActionDraft, role: Role) -> None:
        self._require(role, Role.ANALYST)
        if action.action_id in self._actions:
            raise ValueError("action_id already exists")
        if action.idempotency_key in self._reserved_keys or any(
            item.idempotency_key == action.idempotency_key for item in self._actions.values()
        ):
            raise ValueError("idempotency key is already bound to another action")
        experiment = self._experiment(action.experiment_id)
        if experiment.status not in {ExperimentStatus.APPROVED, ExperimentStatus.RUNNING}:
            raise StateTransitionError("action draft requires approved or running experiment")
        if action.action_type in self._PROHIBITED_ACTIONS:
            raise PolicyBlocked("payment, contract signature, and order commitment are prohibited")
        if action.risk_level == "compliance_blocked":
            self._transition(
                action.experiment_id,
                ExperimentStatus.STOPPED,
                action.actor,
                action.created_at,
                "compliance blocked",
            )
            raise PolicyBlocked("compliance stop condition reached")
        self._validate_budget(experiment, action)
        self._actions[action.action_id] = action

    def approve_action(
        self, action_id: str, actor: str, role: Role, at: datetime
    ) -> ActionApproval:
        self._require(role, Role.APPROVER)
        if existing := self._approvals.get(action_id):
            return existing
        action = self._actions[action_id]
        experiment = self._experiment(action.experiment_id)
        if actor != experiment.approver or actor == action.actor:
            raise RBACDenied("a distinct designated approver is required for an external action")
        approval = ActionApproval(action_id, actor, require_utc(at))
        self._approvals[action_id] = approval
        return approval

    def execute_action(
        self, action_id: str, connector: ActionConnector, at: datetime
    ) -> ActionAttempt:
        # The reservation, connector invocation, and attempt append share one
        # critical section. A production database maps this to a unique key
        # reservation transaction; no racing retry can create another effect.
        with self._reservation_lock:
            action = self._actions[action_id]
            experiment = self._experiment(action.experiment_id)
            if experiment.status is ExperimentStatus.APPROVED:
                self._transition(
                    action.experiment_id, ExperimentStatus.RUNNING, action.actor, at, "first action"
                )
                experiment = self._experiment(action.experiment_id)
            if experiment.status is not ExperimentStatus.RUNNING:
                raise StateTransitionError(
                    "stopped/paused/completed experiment cannot execute external action"
                )
            if action_id not in self._approvals:
                raise RBACDenied("human approval is required before connector execution")
            if action.idempotency_key in self._reserved_keys:
                attempt = ActionAttempt(
                    action_id,
                    self._attempt_count(action_id) + 1,
                    action.idempotency_key,
                    require_utc(at),
                    False,
                    self._reserved_keys[action.idempotency_key],
                )
            else:
                receipt = connector.send(action)
                self._reserved_keys[action.idempotency_key] = receipt
                attempt = ActionAttempt(
                    action_id,
                    self._attempt_count(action_id) + 1,
                    action.idempotency_key,
                    require_utc(at),
                    True,
                    receipt,
                )
            self._attempts.append(attempt)
            return attempt

    def record_event(self, event: BusinessEvent, actor: str, role: Role) -> None:
        self._require(role, Role.ANALYST)
        if event.actor != actor:
            raise RBACDenied("event actor must match the authenticated analyst")
        self._experiment(event.experiment_id)
        if any(item.event_id == event.event_id for item in self._events):
            raise ValueError("event_id already exists")
        self._events.append(event)

    def correct_event(
        self, event: BusinessEvent, replacement: BusinessEvent, actor: str, role: Role
    ) -> None:
        self._require(role, Role.ADMIN)
        if replacement.actor != actor:
            raise RBACDenied("correction actor must match authenticated admin")
        if (
            event not in self._events
            or replacement.reversal_of_event_id != event.event_id
            or any(item.event_id == replacement.event_id for item in self._events)
            or any(item.reversal_of_event_id == event.event_id for item in self._events)
        ):
            raise ValueError(
                "correction requires an existing event and explicit reversal reference"
            )
        self._events.append(replacement)

    def record_outcome(self, outcome: OutcomeLabel, actor: str, role: Role) -> None:
        self._require(role, Role.ANALYST)
        if outcome.actor != actor:
            raise RBACDenied("outcome actor must match the authenticated analyst")
        self._experiment(outcome.experiment_id)
        if any(item.label_id == outcome.label_id for item in self._outcomes):
            raise ValueError("label_id already exists")
        self._outcomes.append(outcome)

    def outcomes_for(self, experiment_id: str) -> tuple[OutcomeLabel, ...]:
        return tuple(item for item in self._outcomes if item.experiment_id == experiment_id)

    def events_for(self, experiment_id: str) -> tuple[BusinessEvent, ...]:
        return tuple(item for item in self._events if item.experiment_id == experiment_id)

    def attempts_for(self, action_id: str) -> tuple[ActionAttempt, ...]:
        return tuple(item for item in self._attempts if item.action_id == action_id)

    def recompute_cost(self, experiment_id: str, currency: str) -> Money:
        reversed_event_ids = {
            event.reversal_of_event_id
            for event in self.events_for(experiment_id)
            if event.reversal_of_event_id
        }
        total = sum(
            (
                event.money.amount
                for event in self.events_for(experiment_id)
                if event.event_id not in reversed_event_ids
                and event.money
                and event.money.currency == currency
            ),
            Decimal("0"),
        )
        return Money(amount=total, currency=currency)

    def mark_censored_no_response(
        self, experiment_id: str, window_end: datetime, now: datetime, actor: str, role: Role
    ) -> OutcomeLabel | None:
        self._require(role, Role.ANALYST)
        if require_utc(now) < require_utc(window_end):
            return None
        if any(item.kind is OutcomeKind.NO_RESPONSE for item in self.outcomes_for(experiment_id)):
            return None
        label = OutcomeLabel(
            label_id=f"{experiment_id}:no-response:{window_end.isoformat()}",
            experiment_id=experiment_id,
            kind=OutcomeKind.NO_RESPONSE,
            actor=actor,
            observed_at=now,
            detail="observation window elapsed",
            observation_window_end=window_end,
        )
        self.record_outcome(label, actor, role)
        return label

    def snapshot_hash(self, experiment_id: str) -> str:
        return self._experiment(experiment_id).opportunity_snapshot.snapshot_hash

    def experiment(self, experiment_id: str) -> Experiment:
        """Return the immutable current aggregate view; history remains append-only."""
        return self._experiment(experiment_id)

    def _transition(
        self, experiment_id: str, target: ExperimentStatus, actor: str, at: datetime, reason: str
    ) -> Experiment:
        experiment = self._experiment(experiment_id)
        if target not in self._TRANSITIONS[experiment.status]:
            raise StateTransitionError(f"invalid transition {experiment.status}->{target}")
        updated = experiment.model_copy(update={"status": target})
        self._experiments[experiment_id] = updated
        self._status_history[experiment_id].append((target, actor, require_utc(at), reason))
        return updated

    @staticmethod
    def _require(actual: Role, expected: Role) -> None:
        if actual is not expected:
            raise RBACDenied(f"requires {expected}")

    def _validate_budget(self, experiment: Experiment, action: ActionDraft) -> None:
        if experiment.budget is None:
            raise PolicyBlocked("experiment has no approved budget")
        budget = experiment.budget
        if (
            action.estimated_cost.currency != budget.cash_max.currency
            or action.estimated_loss.currency != budget.max_loss.currency
        ):
            raise PolicyBlocked("action budget currency mismatch")
        existing = [
            item for item in self._actions.values() if item.experiment_id == action.experiment_id
        ]
        used_cash = sum((item.estimated_cost.amount for item in existing), Decimal("0"))
        used_loss = sum((item.estimated_loss.amount for item in existing), Decimal("0"))
        if (
            used_cash + action.estimated_cost.amount > budget.cash_max.amount
            or used_loss + action.estimated_loss.amount > budget.max_loss.amount
            or len(existing) + 1 > budget.outreach_count
        ):
            self._transition(
                action.experiment_id,
                ExperimentStatus.STOPPED,
                action.actor,
                action.created_at,
                "budget/max-loss exceeded",
            )
            raise PolicyBlocked("budget/max-loss stop condition reached")

    def _experiment(self, experiment_id: str) -> Experiment:
        try:
            return self._experiments[experiment_id]
        except KeyError as error:
            raise KeyError(f"unknown experiment: {experiment_id}") from error

    def _attempt_count(self, action_id: str) -> int:
        return len(self.attempts_for(action_id))
