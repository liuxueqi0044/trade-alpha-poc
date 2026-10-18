from datetime import UTC, datetime, timedelta

import pytest

from trade_alpha.ui import (
    EvidenceChain,
    EvidenceNode,
    ExportService,
    InMemoryBackend,
    Opportunity,
    Permission,
    PerspectiveAdapter,
    ReviewConflict,
    ReviewStore,
    ReviewTask,
    Role,
    ScoreBreakdown,
    SnapshotContext,
    UserContext,
    display_status,
    freshness_status,
)

CONTEXT = SnapshotContext(
    snapshot_id="snap-1", as_of=datetime(2026, 8, 24, tzinfo=UTC), scoring_version="score-1"
)


def opportunity() -> Opportunity:
    return Opportunity(
        opportunity_id="opp-1",
        context=CONTEXT,
        title="Example",
        score=ScoreBreakdown(raw_alpha=0.8, risk_penalty=0.1, final_score=0.7, confidence=0.9),
        statuses=("accepted_claim",),
        evidence_node_ids=("score-1", "feature-1", "claim-1", "artifact-1"),
    )


def chain() -> EvidenceChain:
    return EvidenceChain(
        opportunity_id="opp-1",
        context=CONTEXT,
        nodes=(
            EvidenceNode(
                node_id="score-1",
                node_type="score",
                label="stored score",
                snapshot_id="snap-1",
                status="stored",
            ),
            EvidenceNode(
                node_id="feature-1",
                node_type="feature",
                label="feature",
                snapshot_id="snap-1",
                status="stored",
                parent_id="score-1",
            ),
            EvidenceNode(
                node_id="claim-1",
                node_type="claim",
                label="claim",
                snapshot_id="snap-1",
                status="accepted",
                parent_id="feature-1",
            ),
            EvidenceNode(
                node_id="artifact-1",
                node_type="artifact",
                label="raw artifact",
                snapshot_id="snap-1",
                status="source",
                parent_id="claim-1",
                locator={"artifact_hash": "sha256:x", "page": 1},
                restricted=True,
            ),
        ),
    )


def test_ac001_context_consistency_and_ac002_chain_walk():
    backend = InMemoryBackend((opportunity(),), (chain(),))
    assert backend.opportunities(CONTEXT)[0].context.snapshot_id == "snap-1"
    evidence = backend.evidence("opp-1", CONTEXT, UserContext(user_id="v", role=Role.VIEWER))
    evidence.validate_complete()
    assert {node.node_id for node in evidence.nodes} == {
        "score-1",
        "feature-1",
        "claim-1",
        "artifact-1",
    }
    assert evidence.nodes[-1].locator == {"restricted": True}
    privileged = UserContext(
        user_id="admin", role=Role.ADMIN, grants=frozenset({Permission.READ_RESTRICTED_RAW})
    )
    assert (
        backend.evidence("opp-1", CONTEXT, privileged).nodes[-1].locator["artifact_hash"]
        == "sha256:x"
    )


def test_ac003_states_and_stale_text():
    assert [
        display_status(item) for item in ("proposed_claim", "forecast", "missing", "stale")
    ] == ["Proposed claim", "Forecast", "Missing", "Stale"]
    assert (
        freshness_status(CONTEXT.as_of, CONTEXT.as_of - timedelta(days=10), sla=timedelta(days=1))
        == "stale"
    )


def test_ac004_ui_displays_stored_score():
    item = opportunity()
    assert item.score.final_score == 0.7 and item.score.score_status == "stored"


def test_ac005_ac006_review_audit_and_optimistic_conflict():
    reviewer = UserContext(user_id="r1", role=Role.REVIEWER)
    store = ReviewStore(
        (ReviewTask(task_id="task-1", claim_id="claim-1", snapshot_id="snap-1", version=1),)
    )
    updated, audit = store.review(
        "task-1", reviewer, decision="accepted", reason_code="verified", expected_version=1
    )
    assert updated.version == 2 and audit.reviewer_id == "r1" and store.audits[0].prior_version == 1
    assert (
        audit.snapshot_id == "snap-1"
        and audit.input_version == 1
        and audit.downstream_recompute_event_id
    )
    with pytest.raises(ReviewConflict):
        store.review(
            "task-1", reviewer, decision="rejected", reason_code="wrong", expected_version=1
        )


def test_ac007_rbac():
    viewer = UserContext(user_id="v", role=Role.VIEWER)
    assert viewer.can(Permission.VIEW) and not viewer.can(Permission.REVIEW_CLAIM)
    with pytest.raises(PermissionError):
        ReviewStore((ReviewTask(task_id="t", claim_id="c", snapshot_id="s", version=1),)).review(
            "t", viewer, decision="accepted", reason_code="x", expected_version=1
        )


def test_ac008_restricted_raw_export_and_ac011_audit():
    service = ExportService()
    user = UserContext(user_id="v", role=Role.VIEWER)
    content, audit = service.csv(
        ({"opportunity_id": "o", "public": 1, "raw_secret": "secret"},),
        user,
        CONTEXT,
        filters={"top_k": 1},
    )
    assert "secret" not in content and audit.snapshot_id == "snap-1" and audit.row_count == 1
    assert (
        service.audits[0].user_id == "v"
        and "raw_secret" in service.audits[0].restricted_fields_removed
    )


def test_broken_evidence_and_context_mismatch_are_rejected():
    broken = chain().model_copy(update={"nodes": (chain().nodes[1],)})
    with pytest.raises(ValueError):
        broken.validate_complete()
    backend = InMemoryBackend((opportunity(),), (chain(),))
    other = CONTEXT.model_copy(update={"snapshot_id": "snap-2"})
    with pytest.raises(ValueError):
        backend.evidence("opp-1", other, UserContext(user_id="v", role=Role.VIEWER))


def test_ac007_admin_does_not_approve_and_grant_is_explicit():
    admin = UserContext(user_id="a", role=Role.ADMIN)
    assert not admin.can(Permission.APPROVE_EXPERIMENT)
    assert not admin.can(Permission.READ_RESTRICTED_RAW)
    backend = InMemoryBackend((opportunity(),), (chain(),))
    assert backend.evidence("opp-1", CONTEXT, admin).nodes[-1].locator == {"restricted": True}
    granted = UserContext(
        user_id="a", role=Role.ADMIN, grants=frozenset({Permission.APPROVE_EXPERIMENT})
    )
    assert granted.can(Permission.APPROVE_EXPERIMENT)


def test_restricted_read_does_not_unlock_restricted_export():
    service = ExportService()
    reader = UserContext(
        user_id="reader", role=Role.VIEWER, grants=frozenset({Permission.READ_RESTRICTED_RAW})
    )
    content, _ = service.csv(({"public": 1, "raw_secret": "secret"},), reader, CONTEXT)
    assert "secret" not in content
    exporter = UserContext(
        user_id="exporter",
        role=Role.VIEWER,
        grants=frozenset({Permission.READ_RESTRICTED_RAW, Permission.EXPORT_RESTRICTED}),
    )
    content, _ = service.csv(({"public": 1, "raw_secret": "secret"},), exporter, CONTEXT)
    assert "secret" in content


def test_evidence_path_mutations_are_rejected():
    duplicate = chain().model_copy(
        update={
            "nodes": chain().nodes
            + (chain().nodes[-1].model_copy(update={"node_id": "score-1"}),)
        }
    )
    with pytest.raises(ValueError, match="unique"):
        duplicate.validate_complete()

    isolated = chain().model_copy(
        update={
            "nodes": chain().nodes[:-1]
            + (
                EvidenceNode(
                    node_id="extra-1",
                    node_type="feature",
                    label="isolated",
                    snapshot_id="snap-1",
                    status="stored",
                ),
                chain().nodes[-1],
            )
        }
    )
    with pytest.raises(ValueError, match="feature"):
        isolated.validate_complete()

    wrong_parent = chain().model_copy(
        update={
            "nodes": chain().nodes[:-1]
            + (chain().nodes[-1].model_copy(update={"parent_id": "score-1"}),)
        }
    )
    with pytest.raises(ValueError, match="claim/fact"):
        wrong_parent.validate_complete()

    bad_artifact = chain().model_copy(
        update={
            "nodes": chain().nodes[:-1]
            + (chain().nodes[-1].model_copy(update={"locator": {"page": 1}}),)
        }
    )
    with pytest.raises(ValueError, match="hash"):
        bad_artifact.validate_complete()


def test_review_store_rejects_duplicate_task_ids():
    task = ReviewTask(task_id="duplicate", claim_id="c", snapshot_id="s", version=1)
    with pytest.raises(ValueError, match="duplicate"):
        ReviewStore((task, task))


def test_ac006_two_threads_only_one_review_succeeds():
    import threading

    reviewer = UserContext(user_id="r", role=Role.REVIEWER)
    store = ReviewStore((ReviewTask(task_id="race", claim_id="c", snapshot_id="s", version=1),))
    results: list[str] = []

    def attempt() -> None:
        try:
            store.review(
                "race", reviewer, decision="accepted", reason_code="ok", expected_version=1
            )
            results.append("ok")
        except ReviewConflict:
            results.append("conflict")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["conflict", "ok"]


def test_perspective_adapter_executes():
    adapter = PerspectiveAdapter([{"country": "US", "score": 2}, {"country": "CA", "score": 1}])
    try:
        assert len(adapter.records(group_by=("country",))) >= 2
    finally:
        adapter.close()
