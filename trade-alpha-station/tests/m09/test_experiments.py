from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Thread

import pytest
from pydantic import ValidationError

from trade_alpha.experiments import (
    ActionConnector,
    ActionDraft,
    Budget,
    BusinessEvent,
    Experiment,
    ExperimentRepository,
    ExperimentStatus,
    Money,
    OpportunitySnapshot,
    OutcomeLabel,
    PolicyBlocked,
    Quantity,
    RBACDenied,
    Role,
    StateTransitionError,
)
from trade_alpha.experiments.models import OutcomeKind

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def money(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency="CNY")


def approved_experiment(repo: ExperimentRepository, experiment_id: str = "exp-1") -> Experiment:
    experiment = Experiment(
        experiment_id=experiment_id,
        experiment_type="buyer_outreach",
        opportunity_snapshot=OpportunitySnapshot(
            opportunity_snapshot_id="opp-1", snapshot_hash="sha256:frozen", frozen_at=NOW
        ),
        mandate_version="1.0.0",
        strategy_version="1.0.0",
        owner="analyst-1",
        approver="approver-1",
        budget=Budget(
            research_hours=Decimal("4"),
            outreach_count=2,
            cash_max=money("100"),
            max_loss=money("40"),
        ),
        start_at=NOW,
        end_at=NOW + timedelta(days=7),
        success_definition="qualified RFQ",
        stop_conditions=("budget", "compliance", "human"),
    )
    repo.propose(experiment, "analyst-1", Role.ANALYST, NOW)
    return repo.approve_experiment(experiment_id, "approver-1", Role.APPROVER, NOW)


def draft(
    experiment_id: str = "exp-1",
    action_id: str = "action-1",
    *,
    risk: str = "low",
    cost: str = "10",
) -> ActionDraft:
    return ActionDraft(
        action_id=action_id,
        experiment_id=experiment_id,
        action_type="buyer_outreach",
        idempotency_key=f"idem:{action_id}",
        actor="analyst-1",
        estimated_cost=money(cost),
        estimated_loss=money("5"),
        risk_level=risk,
        created_at=NOW,
    )


def test_approval_requires_frozen_snapshot_mandate_roles_term_budget_and_stops() -> None:
    repo = ExperimentRepository()
    incomplete = Experiment(
        experiment_id="bad",
        experiment_type="supplier_quote",
        opportunity_snapshot=OpportunitySnapshot(
            opportunity_snapshot_id="opp", snapshot_hash="hash", frozen_at=NOW
        ),
        mandate_version="1",
        strategy_version="1",
        owner="analyst",
        approver="approver",
    )
    repo.propose(incomplete, "analyst", Role.ANALYST, NOW)
    with pytest.raises(StateTransitionError, match="budget"):
        repo.approve_experiment("bad", "approver", Role.APPROVER, NOW)
    with pytest.raises(RBACDenied):
        repo.approve_experiment("bad", "analyst", Role.ANALYST, NOW)
    assert (
        approved_experiment(repo).status is ExperimentStatus.APPROVED
    )  # M09-AC-001, AC-002, AC-012


def test_action_human_gate_idempotency_and_retry_attempt_audit() -> None:
    repo = ExperimentRepository()
    approved_experiment(repo)
    action = draft()
    repo.draft_action(action, Role.ANALYST)
    connector = ActionConnector()
    with pytest.raises(RBACDenied):
        repo.execute_action(action.action_id, connector, NOW)
    assert not connector.external_effects  # M09-AC-003
    original_approval = repo.approve_action(action.action_id, "approver-1", Role.APPROVER, NOW)
    assert (
        repo.approve_action(action.action_id, "other", Role.APPROVER, NOW + timedelta(days=1))
        == original_approval
    )
    attempts = [repo.execute_action(action.action_id, connector, NOW) for _ in range(3)]
    assert [item.external_called for item in attempts] == [True, False, False]
    assert len(connector.external_effects) == 1
    assert len(repo.attempts_for(action.action_id)) == 3  # M09-AC-004
    with pytest.raises(PolicyBlocked):
        repo.draft_action(
            draft(action_id="pay", risk="low").model_copy(update={"action_type": "payment"}),
            Role.ANALYST,
        )


def test_negative_outcomes_snapshot_immutability_and_censored_timeout() -> None:
    repo = ExperimentRepository()
    approved_experiment(repo)
    before = repo.snapshot_hash("exp-1")
    for kind in (
        OutcomeKind.REJECTED,
        OutcomeKind.FAILED,
        OutcomeKind.NO_RESPONSE,
        OutcomeKind.QUALITY_ISSUE,
    ):
        repo.record_outcome(
            OutcomeLabel(
                label_id=f"label:{kind}",
                experiment_id="exp-1",
                kind=kind,
                actor="analyst-1",
                observed_at=NOW,
                detail=str(kind),
            ),
            "analyst-1",
            Role.ANALYST,
        )
    assert {item.kind for item in repo.outcomes_for("exp-1")} >= {
        OutcomeKind.REJECTED,
        OutcomeKind.FAILED,
        OutcomeKind.NO_RESPONSE,
        OutcomeKind.QUALITY_ISSUE,
    }  # M09-AC-005
    assert repo.snapshot_hash("exp-1") == before == "sha256:frozen"  # M09-AC-006
    assert (
        repo.mark_censored_no_response(
            "exp-1", NOW + timedelta(days=1), NOW, "analyst-1", Role.ANALYST
        )
        is None
    )
    censored = repo.mark_censored_no_response(
        "exp-1", NOW - timedelta(days=1), NOW, "analyst-1", Role.ANALYST
    )
    assert censored is None  # existing no-response is preserved; no duplicate  # M09-AC-011


def test_money_quantity_evidence_and_append_only_correction() -> None:
    repo = ExperimentRepository()
    approved_experiment(repo)
    with pytest.raises(ValidationError, match="original money and quantity"):
        BusinessEvent(
            event_id="bad",
            experiment_id="exp-1",
            event_type="payment_received",
            actor="analyst-1",
            occurred_at=NOW,
            recorded_at=NOW,
            artifact_id="artifact:payment",
        )
    receipt = BusinessEvent(
        event_id="payment-1",
        experiment_id="exp-1",
        event_type="payment_received",
        actor="analyst-1",
        occurred_at=NOW,
        recorded_at=NOW,
        money=money("12"),
        quantity=Quantity(amount=Decimal("2"), unit="piece"),
        artifact_id="m01:artifact-payment",
    )
    repo.record_event(receipt, "analyst-1", Role.ANALYST)
    replacement = receipt.model_copy(
        update={
            "event_id": "payment-1-reversal",
            "reversal_of_event_id": "payment-1",
            "money": money("7"),
        }
    )
    with pytest.raises(RBACDenied):
        repo.correct_event(receipt, replacement, "approver-1", Role.APPROVER)
    replacement = replacement.model_copy(update={"actor": "admin-1"})
    repo.correct_event(receipt, replacement, "admin-1", Role.ADMIN)
    assert [item.event_id for item in repo.events_for("exp-1")] == [
        "payment-1",
        "payment-1-reversal",
    ]
    assert repo.recompute_cost("exp-1", "CNY").amount == Decimal(
        "7"
    )  # M09-AC-007, AC-009, AC-014, AC-013


def test_budget_compliance_and_human_stop_block_actions() -> None:
    repo = ExperimentRepository()
    approved_experiment(repo)
    with pytest.raises(PolicyBlocked, match="budget"):
        repo.draft_action(draft(cost="101"), Role.ANALYST)
    assert repo.experiment("exp-1").status is ExperimentStatus.STOPPED
    with pytest.raises(StateTransitionError):
        repo.draft_action(draft(action_id="later"), Role.ANALYST)

    second = ExperimentRepository()
    approved_experiment(second, "exp-2")
    with pytest.raises(PolicyBlocked, match="compliance"):
        second.draft_action(draft("exp-2", risk="compliance_blocked"), Role.ANALYST)
    assert second.experiment("exp-2").status is ExperimentStatus.STOPPED
    third = ExperimentRepository()
    approved_experiment(third, "exp-3")
    third.stop("exp-3", "approver-1", Role.APPROVER, "human stop", NOW)
    with pytest.raises(StateTransitionError):
        third.draft_action(draft("exp-3"), Role.ANALYST)  # M09-AC-008


def test_append_only_ids_and_rbac_reject_mutations_without_changing_history() -> None:
    repo = ExperimentRepository()
    approved = approved_experiment(repo)
    frozen_hash = repo.snapshot_hash("exp-1")
    with pytest.raises(ValueError, match="already exists"):
        repo.propose(
            approved.model_copy(update={"owner": "analyst-2", "status": ExperimentStatus.PROPOSED}),
            "analyst-2",
            Role.ANALYST,
            NOW,
        )
    assert repo.snapshot_hash("exp-1") == frozen_hash

    action = draft()
    repo.draft_action(action, Role.ANALYST)
    with pytest.raises(ValueError, match="action_id"):
        repo.draft_action(action.model_copy(update={"risk_level": "changed"}), Role.ANALYST)
    with pytest.raises(ValueError, match="idempotency"):
        repo.draft_action(
            draft(action_id="other").model_copy(update={"idempotency_key": action.idempotency_key}),
            Role.ANALYST,
        )
    with pytest.raises(RBACDenied):
        repo.transition("exp-1", ExperimentStatus.RUNNING, "approver-1", Role.APPROVER, NOW)

    event = BusinessEvent(
        event_id="e-1",
        experiment_id="exp-1",
        event_type="reply_received",
        actor="analyst-1",
        occurred_at=NOW,
        recorded_at=NOW,
    )
    with pytest.raises(RBACDenied):
        repo.record_event(event, "approver-1", Role.APPROVER)
    repo.record_event(event, "analyst-1", Role.ANALYST)
    with pytest.raises(ValueError, match="event_id"):
        repo.record_event(event, "analyst-1", Role.ANALYST)
    outcome = OutcomeLabel(
        label_id="o-1",
        experiment_id="exp-1",
        kind=OutcomeKind.FAILED,
        actor="analyst-1",
        observed_at=NOW,
        detail="negative",
    )
    with pytest.raises(RBACDenied):
        repo.record_outcome(outcome, "approver-1", Role.APPROVER)
    repo.record_outcome(outcome, "analyst-1", Role.ANALYST)
    with pytest.raises(ValueError, match="label_id"):
        repo.record_outcome(outcome, "analyst-1", Role.ANALYST)
    assert [item.event_id for item in repo.events_for("exp-1")] == ["e-1"]
    assert [item.label_id for item in repo.outcomes_for("exp-1")] == ["o-1"]


def test_concurrent_action_retries_reserve_one_external_effect() -> None:
    repo = ExperimentRepository()
    approved_experiment(repo)
    action = draft()
    repo.draft_action(action, Role.ANALYST)
    repo.approve_action(action.action_id, "approver-1", Role.APPROVER, NOW)
    connector = ActionConnector()
    barrier = Barrier(2)
    attempts = []

    def run() -> None:
        barrier.wait()
        attempts.append(repo.execute_action(action.action_id, connector, NOW))

    threads = [Thread(target=run), Thread(target=run)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(attempts) == 2
    assert sum(item.external_called for item in attempts) == 1
    assert len(connector.external_effects) == 1
    assert [item.attempt_number for item in repo.attempts_for(action.action_id)] == [1, 2]
