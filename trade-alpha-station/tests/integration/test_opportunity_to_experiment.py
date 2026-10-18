from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_alpha.experiments import (
    Budget,
    Experiment,
    ExperimentRepository,
    ExperimentStatus,
    Money,
    OpportunitySnapshot,
    OutcomeLabel,
    RBACDenied,
)
from trade_alpha.experiments import Role as ExperimentRole
from trade_alpha.experiments.models import OutcomeKind
from trade_alpha.governance import (
    Action,
    AppendOnlyAuditLog,
    DataClassification,
    Identity,
    PolicyEngine,
    ProtectedResource,
    Role,
    VersionManifest,
    stable_hash,
)
from trade_alpha.research import (
    FeatureDefinition,
    FeatureValue,
    ModelApproval,
    create_limited_live_plan,
    feature_vector,
    score_opportunity,
    select_as_of,
)
from trade_alpha.research import (
    ScoreBreakdown as ResearchScoreBreakdown,
)
from trade_alpha.ui import (
    EvidenceChain,
    EvidenceNode,
    InMemoryBackend,
    Opportunity,
    Permission,
    ScoreBreakdown,
    SnapshotContext,
    UserContext,
)
from trade_alpha.ui import Role as UiRole

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def governance_identity(actor: str, role: Role) -> Identity:
    return Identity(
        actor_id=actor,
        roles=frozenset({role}),
        client_identity="opportunity-integration",
    )


def research_score() -> tuple[FeatureDefinition, FeatureValue, ResearchScoreBreakdown]:
    definition = FeatureDefinition(
        feature_name="demand_acceleration",
        feature_version="1.0.0",
        entity_grain="opportunity",
        availability_lag_days=0,
        missing_policy="explicit_missing_indicator",
        expected_direction="positive",
        weight=2.0,
    )
    value = FeatureValue(
        opportunity_id="opp-DE-12",
        feature_name=definition.feature_name,
        value=0.8,
        observed_at=NOW,
        available_at=NOW,
        feature_version=definition.feature_version,
    )
    selected = select_as_of([value], NOW, [definition])
    vector, missing = feature_vector([definition], selected, value.opportunity_id)
    score = score_opportunity(
        value.opportunity_id,
        [definition],
        vector,
        missing,
        risk_components={"regulatory": 0.1},
        confidence_components={"coverage": 0.9},
        score_version="score/1.0.0",
    )
    return definition, value, score


def test_x_ac_004_005_006_010_opportunity_ui_experiment_feedback_and_replay() -> None:
    definition, value, score = research_score()
    context = SnapshotContext(
        snapshot_id="gold-20260824",
        as_of=NOW,
        scoring_version=score.score_version,
    )
    chain = EvidenceChain(
        opportunity_id=score.opportunity_id,
        context=context,
        nodes=(
            EvidenceNode(
                node_id="score:opp-DE-12",
                node_type="score",
                label="stored final score",
                snapshot_id=context.snapshot_id,
                status="stored",
            ),
            EvidenceNode(
                node_id="feature:demand",
                node_type="feature",
                label=definition.feature_name,
                snapshot_id=context.snapshot_id,
                status="observed",
                parent_id="score:opp-DE-12",
            ),
            EvidenceNode(
                node_id="claim:demand",
                node_type="claim",
                label="accepted customs observation",
                snapshot_id=context.snapshot_id,
                status="accepted_claim",
                parent_id="feature:demand",
            ),
            EvidenceNode(
                node_id="artifact:customs",
                node_type="artifact",
                label="immutable source artifact",
                snapshot_id=context.snapshot_id,
                status="source",
                parent_id="claim:demand",
                locator={"artifact_hash": "sha256:fixture", "page": 1},
                restricted=True,
            ),
        ),
    )
    ui_opportunity = Opportunity(
        opportunity_id=score.opportunity_id,
        context=context,
        title="DE demand acceleration / HS12",
        score=ScoreBreakdown(
            raw_alpha=score.raw_alpha,
            risk_penalty=score.risk_penalty,
            confidence=score.confidence,
            final_score=score.final_score,
            score_status="stored",
        ),
        statuses=("accepted_claim",),
        evidence_node_ids=tuple(node.node_id for node in chain.nodes),
    )
    backend = InMemoryBackend((ui_opportunity,), (chain,))
    viewer = UserContext(user_id="viewer-1", role=UiRole.VIEWER)
    displayed = backend.opportunities(context)[0]
    assert displayed.score.final_score == score.final_score
    assert backend.evidence(score.opportunity_id, context, viewer).nodes[-1].locator == {
        "restricted": True
    }

    analyst_ui = UserContext(user_id="analyst-1", role=UiRole.ANALYST)
    assert analyst_ui.can(Permission.CREATE_EXPERIMENT)
    live_plan = create_limited_live_plan(
        ModelApproval(
            model_id="strategy-1",
            state="paper_approved",
            owner="analyst-1",
            max_budget=50,
            failure_conditions=("loss", "no_response"),
            rollback="stop connector and preserve attempts",
            frozen_report_ref="report:paper-1",
            scope="DE/HS12",
        )
    )
    experiment_resource = ProtectedResource(
        resource_id="experiment:exp-1",
        classification=DataClassification.INTERNAL,
        owner="M09",
    )
    policy = PolicyEngine()
    governance_analyst = governance_identity("analyst-1", Role.ANALYST)
    assert policy.require(
        governance_analyst, Action.PROPOSE_EXPERIMENT, experiment_resource, NOW
    ).allowed

    frozen_hash = stable_hash(score)
    experiment = Experiment(
        experiment_id="exp-1",
        experiment_type="buyer_outreach",
        opportunity_snapshot=OpportunitySnapshot(
            opportunity_snapshot_id=score.opportunity_id,
            snapshot_hash=frozen_hash,
            frozen_at=NOW,
        ),
        mandate_version="mandate/1.0.0",
        strategy_version=live_plan.model_id,
        owner=live_plan.owner,
        approver="approver-1",
        budget=Budget(
            research_hours=Decimal("4"),
            outreach_count=2,
            cash_max=Money(amount=Decimal(str(live_plan.maximum_budget)), currency="CNY"),
            max_loss=Money(amount=Decimal("20"), currency="CNY"),
        ),
        start_at=NOW,
        end_at=NOW + timedelta(days=14),
        success_definition="qualified RFQ",
        stop_conditions=live_plan.failure_conditions,
    )
    repository = ExperimentRepository()
    repository.propose(experiment, "analyst-1", ExperimentRole.ANALYST, NOW)
    admin = governance_identity("admin-1", Role.ADMIN)
    assert not policy.decide(admin, Action.APPROVE_EXPERIMENT, experiment_resource, NOW).allowed
    with pytest.raises(RBACDenied):
        repository.approve_experiment("exp-1", "admin-1", ExperimentRole.ADMIN, NOW)
    governance_approver = governance_identity("approver-1", Role.APPROVER)
    assert policy.require(
        governance_approver, Action.APPROVE_EXPERIMENT, experiment_resource, NOW
    ).allowed
    assert (
        repository.approve_experiment(
            "exp-1", "approver-1", ExperimentRole.APPROVER, NOW
        ).status
        is ExperimentStatus.APPROVED
    )

    audit = AppendOnlyAuditLog()
    audit.append(
        identity=governance_analyst,
        action="experiment.proposed",
        resource=experiment_resource.resource_id,
        before=None,
        after=experiment,
        occurred_at=NOW,
        reason="paper-approved opportunity",
        correlation_id="corr-exp-1",
    )
    audit.append(
        identity=governance_approver,
        action="experiment.approved",
        resource=experiment_resource.resource_id,
        before=ExperimentStatus.PROPOSED,
        after=ExperimentStatus.APPROVED,
        occurred_at=NOW,
        reason="bounded budget and stop conditions verified",
        correlation_id="corr-exp-1",
    )
    assert audit.verify().valid

    manifest_values = {
        "architecture_spec_version": "0.1.0",
        "code_sha": "git:fixture",
        "environment_lock_hash": "sha256:lock",
        "source_versions": {"customs": "retrieval-1"},
        "schema_versions": {"opportunity": "1.0.0"},
        "parser_versions": {"table": "1.0.0"},
        "prompt_versions": {"claim": "deterministic"},
        "model_versions": {"baseline": "1.0.0"},
        "entity_resolution_version": "1.0.0",
        "feature_set_version": definition.feature_version,
        "signal_version": score.score_version,
        "mandate_version": experiment.mandate_version,
        "input_snapshot_ids": (context.snapshot_id,),
    }
    first_manifest = VersionManifest(**manifest_values)
    replay_definition, replay_value, replay_score = research_score()
    replay_manifest = VersionManifest(**manifest_values)
    assert replay_definition == definition and replay_value == value
    assert stable_hash(replay_score) == frozen_hash
    assert replay_manifest.manifest_hash == first_manifest.manifest_hash

    repository.record_outcome(
        OutcomeLabel(
            label_id="outcome:failed-1",
            experiment_id="exp-1",
            kind=OutcomeKind.FAILED,
            actor="analyst-1",
            observed_at=NOW + timedelta(days=7),
            detail="no qualified response",
        ),
        "analyst-1",
        ExperimentRole.ANALYST,
    )
    assert repository.snapshot_hash("exp-1") == frozen_hash
    assert backend.opportunities(context)[0] == ui_opportunity
