from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from trade_alpha.experiments import (
    Budget,
    Experiment,
    ExperimentRepository,
    Money,
    OpportunitySnapshot,
    RBACDenied,
)
from trade_alpha.experiments import Role as ExperimentRole
from trade_alpha.governance import (
    Action,
    AppendOnlyAuditLog,
    AuthorizationDenied,
    DataClassification,
    DataLicense,
    ExportController,
    Identity,
    LicenseRegistry,
    PolicyEngine,
    ProtectedDataGateway,
    ProtectedResource,
    Role,
    SourceGovernance,
)
from trade_alpha.ingestion import (
    EvidenceStore,
    IngestionService,
    PageResponse,
    SourceRecord,
    SourceRegistry,
)
from trade_alpha.ingestion.models import RatePolicy

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def governed_identity(role: Role, grants: frozenset[str] = frozenset()) -> Identity:
    return Identity(
        actor_id=f"actor:{role.value}",
        roles=frozenset({role}),
        grants=grants,
        client_identity="integration-test",
    )


def test_x_ac_009_license_revocation_blocks_collection_read_and_export(tmp_path: Path) -> None:
    ingestion_record = SourceRecord(
        source_id="revoked_source",
        owner="fixture",
        source_type="api",
        base_url="https://fixture.invalid/api",
        access_method="api",
        rate_policy=RatePolicy(max_requests_per_window=10, window_seconds=1),
        robots_policy="not_applicable",
        terms_url="https://fixture.invalid/terms",
        data_license="data-license-v2",
        commercial_use="approved",
        redistribution="approved",
        retention_policy="365 days",
        allowed_content_types=("application/json",),
        parser_profile="fixture",
    )
    license_record = SourceGovernance(
        source_id="revoked_source",
        enabled=True,
        api_terms_url="https://fixture.invalid/terms",
        api_use_status="approved",
        caching_policy="30 days",
        retention_policy="365 days",
        redistribution_status="prohibited",
        data=DataLicense(
            owner="fixture",
            license_id="data-license-v2",
            attribution="required",
            commercial_use="prohibited",
            derivatives="prohibited",
            redistribution="prohibited",
            region_restrictions="none",
        ),
    )
    licenses = LicenseRegistry((license_record,))
    ingestion = IngestionService(
        SourceRegistry((ingestion_record,), policy_hooks=(licenses.source_policy,)),
        EvidenceStore(tmp_path),
        code_sha="git:test",
    )
    calls = 0

    def connector(_cursor: str | None, _page: int) -> PageResponse:
        nonlocal calls
        calls += 1
        return PageResponse(url="https://fixture.invalid/api", status_code=200)

    manifest = ingestion.run_paginated(
        "revoked_source", connector, connector_version="fixture/1"
    )
    assert manifest.status == "blocked_policy" and calls == 0

    resource = ProtectedResource(
        resource_id="m04:historical-snapshot",
        classification=DataClassification.RESTRICTED,
        owner="M04",
        source_id="revoked_source",
    )
    reviewer = governed_identity(Role.REVIEWER, frozenset({"read:restricted"}))
    gateway = ProtectedDataGateway(PolicyEngine(), licenses)
    gateway.seed(resource, {"historical": "preserved but policy restricted"})
    with pytest.raises(AuthorizationDenied, match="source_policy_blocked"):
        gateway.read(reviewer, resource.resource_id, NOW, raw=True)

    exporter = ExportController(PolicyEngine(), AppendOnlyAuditLog(), licenses)
    export_reviewer = reviewer.model_copy(
        update={"grants": frozenset({"export:restricted"})}
    )
    with pytest.raises(AuthorizationDenied, match="source_policy_blocked"):
        exporter.export_csv(
            identity=export_reviewer,
            resource=resource,
            rows=({"historical": "must not leave"},),
            snapshot_id="snapshot-before-revocation",
            filters={},
            at=NOW,
            correlation_id="corr-license",
        )

    audit = AppendOnlyAuditLog()
    audit.append(
        identity=governed_identity(Role.ADMIN),
        action="source.license_changed",
        resource="source:revoked_source",
        before={"commercial_use": "allowed"},
        after={"commercial_use": "prohibited"},
        occurred_at=NOW,
        reason="license review",
        correlation_id="corr-license",
    )
    assert audit.verify().valid and audit.events[0].before_hash != audit.events[0].after_hash


def test_x_ac_012_admin_cannot_bypass_governance_or_experiment_approval() -> None:
    resource = ProtectedResource(
        resource_id="experiment:exp-cross",
        classification=DataClassification.INTERNAL,
        owner="M09",
    )
    admin = governed_identity(Role.ADMIN)
    assert not PolicyEngine().decide(admin, Action.APPROVE_EXPERIMENT, resource, NOW).allowed

    experiment = Experiment(
        experiment_id="exp-cross",
        experiment_type="buyer_outreach",
        opportunity_snapshot=OpportunitySnapshot(
            opportunity_snapshot_id="opp-1",
            snapshot_hash="sha256:frozen",
            frozen_at=NOW,
        ),
        mandate_version="1.0.0",
        strategy_version="1.0.0",
        owner="analyst-1",
        approver="approver-1",
        budget=Budget(
            research_hours=Decimal("2"),
            outreach_count=1,
            cash_max=Money(amount=Decimal("20"), currency="CNY"),
            max_loss=Money(amount=Decimal("10"), currency="CNY"),
        ),
        start_at=NOW,
        end_at=NOW + timedelta(days=7),
        success_definition="qualified response",
        stop_conditions=("budget", "human"),
    )
    repository = ExperimentRepository()
    repository.propose(experiment, "analyst-1", ExperimentRole.ANALYST, NOW)
    with pytest.raises(RBACDenied):
        repository.approve_experiment("exp-cross", "admin-1", ExperimentRole.ADMIN, NOW)
