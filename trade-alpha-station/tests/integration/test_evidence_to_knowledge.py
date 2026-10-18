from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trade_alpha.extraction import ExtractionService
from trade_alpha.ingestion import (
    Artifact,
    EvidenceStore,
    IngestionService,
    PageResponse,
    SourceRegistry,
)
from trade_alpha.ingestion.models import RatePolicy
from trade_alpha.ingestion.models import SourceRecord as IngestionSource
from trade_alpha.knowledge import (
    Claim,
    Entity,
    EntityKind,
    EntityLink,
    KnowledgeStore,
    LinkState,
    Relationship,
    ReviewStatus,
    build_snapshot,
)
from trade_alpha.knowledge.models import SourceRecord as KnowledgeSourceRecord


def test_m01_m02_m03_evidence_chain_and_graph_snapshot(tmp_path: Path) -> None:
    source = IngestionSource(
        source_id="fixture_notice",
        owner="Fixture Authority",
        source_type="web",
        base_url="https://fixture.invalid/notices",
        access_method="download",
        rate_policy=RatePolicy(max_requests_per_window=10, window_seconds=1),
        robots_policy="not_applicable",
        terms_url="https://fixture.invalid/terms",
        data_license="fixture",
        commercial_use="approved",
        redistribution="approved",
        retention_policy="retain_raw",
        allowed_content_types=("text/html",),
        parser_profile="notice-html",
    )
    evidence = EvidenceStore(tmp_path / "evidence")
    raw = (
        b"<html><body><h1>Example Manufacturing Ltd.</h1>"
        b"<p>Published 2026-08-24; HS 847130.</p></body></html>"
    )
    batch = IngestionService(
        SourceRegistry([source]), evidence, code_sha="integration"
    ).run_paginated(
        source.source_id,
        lambda _cursor, _page: PageResponse(
            url="https://fixture.invalid/notices/1",
            status_code=200,
            headers={"content-type": "text/html"},
            content=raw,
        ),
        connector_version="fixture/1",
    )
    assert batch.status == "complete"
    artifact_path = next((tmp_path / "evidence" / "manifests" / "artifacts").glob("*.json"))
    artifact = Artifact.model_validate_json(artifact_path.read_text(encoding="utf-8"))
    assert evidence.raw_file_for(artifact).read_bytes() == raw

    extraction = ExtractionService().extract(
        artifact.artifact_id,
        evidence.raw_file_for(artifact).read_bytes(),
        declared_mime=artifact.mime_type_detected,
    )
    hs_claim = next(claim for claim in extraction.claims if claim.predicate == "product.hs_code")
    assert hs_claim.evidence_block_ids

    now = datetime(2026, 8, 24, tzinfo=UTC)
    knowledge = KnowledgeStore()
    organization = Entity(
        entity_id=Entity.stable_id(EntityKind.ORGANIZATION, "fixture:example-manufacturing"),
        entity_type=EntityKind.ORGANIZATION,
        canonical_name="Example Manufacturing Ltd.",
        system_from=now,
    )
    product_class = Entity(
        entity_id=Entity.stable_id(EntityKind.PRODUCT_CLASS, "HS2022:847130"),
        entity_type=EntityKind.PRODUCT_CLASS,
        canonical_name="Portable digital computers",
        attributes={"hs_version": "HS2022", "hs_code": "847130"},
        system_from=now,
    )
    knowledge.add_entity(organization)
    knowledge.add_entity(product_class)
    source_record = KnowledgeSourceRecord(
        source_record_id="fixture-notice-1",
        source_id=source.source_id,
        original_name="Example Manufacturing Ltd.",
        payload_ref=artifact.artifact_id,
        entity_type=EntityKind.ORGANIZATION,
        system_from=now,
    )
    knowledge.add_source_record(source_record)
    knowledge.add_link(
        EntityLink(
            link_id=EntityLink.stable_id(
                source_record.source_record_id, organization.entity_id, "fixture/1"
            ),
            source_record_id=source_record.source_record_id,
            entity_id=organization.entity_id,
            probability=1.0,
            state=LinkState.AUTO_LINKED,
            matcher_version="fixture/1",
            decision_reason="fixture registration identifier",
            system_from=now,
        )
    )
    accepted_claim = Claim(
        claim_id=hs_claim.claim_id,
        subject_ref=organization.entity_id,
        predicate=hs_claim.predicate,
        object_ref=product_class.entity_id,
        source_id=source.source_id,
        artifact_id=hs_claim.artifact_id,
        evidence_block_ids=hs_claim.evidence_block_ids,
        observed_at=now,
        confidence=hs_claim.confidence,
        status=ReviewStatus.ACCEPTED,
        decision_basis="rule",
        system_from=now,
    )
    knowledge.add_claim(accepted_claim)
    knowledge.add_relationship(
        Relationship(
            relationship_id="relationship:classified-as",
            subject_entity_id=organization.entity_id,
            relationship_type="offers_product_class",
            object_entity_id=product_class.entity_id,
            source_claim_ids=(accepted_claim.claim_id,),
            confidence=accepted_claim.confidence,
            review_status=ReviewStatus.ACCEPTED,
            system_from=now,
        )
    )
    snapshot = build_snapshot(knowledge, now)
    assert snapshot.graph.number_of_nodes() == 2
    assert snapshot.graph.number_of_edges() == 1
    assert knowledge.current_claim_view(organization.entity_id, now)[
        "product.hs_code"
    ].artifact_id == artifact.artifact_id
