import pytest

from trade_alpha.knowledge import (
    Authorization,
    Claim,
    Entity,
    EntityKind,
    EntityLink,
    KnowledgeStore,
    LinkState,
    MatchThresholds,
    PIIAccessDenied,
    RapidFuzzMatcher,
    Relationship,
    ReviewStatus,
    SourceRecord,
    blocking_keys,
    build_snapshot,
    evaluate_labeled_pairs,
)
from trade_alpha.knowledge.models import deterministic_id
from trade_alpha.knowledge.privacy import require_pii


def entity(kind: EntityKind, name: str, now, **attributes: object) -> Entity:
    key = attributes.get("hs_version", "") + ":" + str(attributes.get("hs_code", name))
    return Entity(
        entity_id=Entity.stable_id(kind, str(key)),
        entity_type=kind,
        canonical_name=name,
        attributes=attributes,
        system_from=now,
    )


def claim(claim_id: str, subject: str, value: str, now, source: str = "web") -> Claim:
    return Claim(
        claim_id=claim_id,
        subject_ref=subject,
        predicate="organization.address",
        object_value=value,
        source_id=source,
        artifact_id=f"artifact-{claim_id}",
        evidence_block_ids=(f"block-{claim_id}",),
        observed_at=now,
        confidence=0.9,
        status=ReviewStatus.ACCEPTED,
        decision_basis="rule",
        system_from=now,
    )


def source_record(record_id: str, source_id: str, original_name: str, now) -> SourceRecord:
    return SourceRecord(
        source_record_id=record_id,
        source_id=source_id,
        original_name=original_name,
        payload_ref=f"artifact:{record_id}",
        entity_type=EntityKind.ORGANIZATION,
        system_from=now,
    )


def test_stable_ids_source_preservation_and_multilingual_blocks(
    multilingual_fixture, times
) -> None:
    t0, _, _ = times
    cn_id = Entity.stable_id(EntityKind.ORGANIZATION, "CN:91440300XH001")
    assert cn_id == Entity.stable_id(EntityKind.ORGANIZATION, "CN:91440300XH001")  # M03-AC-001
    cn = multilingual_fixture[0][1]
    en = multilingual_fixture[1][1]
    assert blocking_keys(EntityKind.ORGANIZATION, cn) & blocking_keys(EntityKind.ORGANIZATION, en)
    store = KnowledgeStore()
    record = entity(
        EntityKind.ORGANIZATION,
        "Shenzhen Xinghai",
        t0,
        country="CN",
        source_record_ids=("cn-source", "en-source"),
    )
    store.add_entity(record)
    store.add_source_record(source_record("cn-source", "cn-site", "深圳星海电子有限公司", t0))
    store.add_source_record(
        source_record("en-source", "en-directory", "Shenzhen Xinghai Electronics Co., Ltd.", t0)
    )
    for source_id in ("cn-source", "en-source"):
        store.add_link(
            EntityLink(
                link_id=EntityLink.stable_id(source_id, record.entity_id, "rapidfuzz/1.0.0"),
                source_record_id=source_id,
                entity_id=record.entity_id,
                probability=1.0,
                state=LinkState.AUTO_LINKED,
                matcher_version="rapidfuzz/1.0.0",
                decision_reason="shared registration number",
                system_from=t0,
            )
        )
    store.add_claim(claim("c1", record.entity_id, "中国深圳", t0, "cn-site"))
    store.add_claim(claim("c2", record.entity_id, "Shenzhen, China", t0, "en-directory"))
    assert {item.source_id for item in store.claims_for(record.entity_id, t0)} == {
        "cn-site",
        "en-directory",
    }  # M03-AC-002
    preserved = store.source_records_for_entity(record.entity_id, t0)
    assert [(item.source_id, item.original_name, item.payload_ref) for item in preserved] == [
        ("cn-site", "深圳星海电子有限公司", "artifact:cn-source"),
        ("en-directory", "Shenzhen Xinghai Electronics Co., Ltd.", "artifact:en-source"),
    ]


def test_high_precision_auto_link_and_gray_zone(multilingual_fixture) -> None:
    matcher = RapidFuzzMatcher(MatchThresholds(review_lower=0.70, auto_link_threshold=0.985))
    cn, en, us, near = (item[1] for item in multilingual_fixture)
    assert matcher.decide(cn, en).state is LinkState.AUTO_LINKED
    assert matcher.decide(cn, us).state is LinkState.SEPARATE
    assert matcher.decide(en, near).state is LinkState.REVIEW  # M03-AC-004

    # These labels are the frozen multilingual fixture's human adjudications.
    # Negative candidates are included; only automatic decisions count as facts.
    labelled_fixture = ((cn, en, True), (cn, us, False), (en, near, False))
    report = evaluate_labeled_pairs(matcher, labelled_fixture)
    assert report.auto_link_count == 1
    assert report.false_positives == 0
    assert report.auto_link_precision >= 0.99  # M03-AC-003

    # Calibration also refuses any threshold whose labelled precision is below
    # the precision target.
    labelled = [(1.0, True)] * 99 + [(0.99, True)] + [(0.98, False)]
    threshold = RapidFuzzMatcher.calibrate(labelled)
    selected = [truth for score, truth in labelled if score >= threshold.auto_link_threshold]
    assert threshold.auto_link_threshold == 0.98  # lowest qualifying/max-coverage threshold
    assert sum(selected) / len(selected) >= 0.99

    disabled = RapidFuzzMatcher.calibrate([(1.0, False)])
    assert not disabled.auto_link_enabled
    assert RapidFuzzMatcher(disabled).decide(cn, en).state is not LinkState.AUTO_LINKED

    zero_threshold = RapidFuzzMatcher.calibrate([(0.0, True)], minimum_precision=1.0)
    assert zero_threshold.auto_link_enabled
    assert zero_threshold.auto_link_threshold == 0.0
    assert zero_threshold.review_lower == 0.0


def test_revoke_keeps_history_and_current_link_is_removed(times) -> None:
    t0, t1, _ = times
    store = KnowledgeStore()
    item = entity(EntityKind.ORGANIZATION, "A", t0)
    other = entity(EntityKind.ORGANIZATION, "B", t0)
    store.add_entity(item)
    store.add_entity(other)
    store.add_source_record(source_record("source-a", "directory", "A Limited", t0))
    store.add_claim(claim("relationship-claim", item.entity_id, "A owns B", t0))
    link = EntityLink(
        link_id="l1",
        source_record_id="source-a",
        entity_id=item.entity_id,
        probability=0.999,
        state=LinkState.AUTO_LINKED,
        matcher_version="test",
        decision_reason="matched",
        system_from=t0,
    )
    store.add_link(link)
    store.add_relationship(
        Relationship(
            relationship_id="affected-r",
            subject_entity_id=item.entity_id,
            relationship_type="owns",
            object_entity_id=other.entity_id,
            source_claim_ids=("relationship-claim",),
            confidence=0.9,
            review_status=ReviewStatus.ACCEPTED,
            system_from=t0,
        )
    )
    impact = store.revoke_link("l1", t1, "false positive")
    assert [link.state for link in store.links_at(t0)] == [LinkState.AUTO_LINKED]
    assert [link.state for link in store.links_at(t1)] == [LinkState.REVOKED]  # M03-AC-005
    assert [item.relationship_id for item in store.accepted_relationships_at(t0)] == ["affected-r"]
    assert not store.accepted_relationships_at(t1)
    assert store.relationships_at(t1)[0].review_status is ReviewStatus.PENDING
    assert impact.affected_entity_id == item.entity_id
    assert impact.affected_relationship_ids == ("affected-r",)
    assert "relationship:affected-r" in impact.downstream_invalidation_keys


def test_conflict_current_view_and_evidence_coverage(times) -> None:
    t0, _, _ = times
    store = KnowledgeStore(source_priority={"official": 10, "directory": 1})
    org = entity(EntityKind.ORGANIZATION, "A", t0)
    other = entity(EntityKind.ORGANIZATION, "B", t0)
    store.add_entity(org)
    store.add_entity(other)
    store.add_claim(claim("dir", org.entity_id, "Old Road", t0, "directory"))
    store.add_claim(claim("off", org.entity_id, "New Road", t0, "official"))
    assert len(store.conflict_claims(org.entity_id, "organization.address", t0)) == 2
    assert (
        store.current_claim_view(org.entity_id, t0)["organization.address"].object_value
        == "New Road"
    )  # M03-AC-006
    relationship = Relationship(
        relationship_id="r1",
        subject_entity_id=org.entity_id,
        relationship_type="owns",
        object_entity_id=other.entity_id,
        source_claim_ids=("off",),
        confidence=0.8,
        review_status=ReviewStatus.ACCEPTED,
        system_from=t0,
    )
    store.add_relationship(relationship)  # M03-AC-008
    with pytest.raises(ValueError, match="unknown claims"):
        store.add_relationship(
            relationship.model_copy(
                update={"relationship_id": "r2", "source_claim_ids": ("missing",)}
            )
        )


def test_point_in_time_and_reproducible_graph(times) -> None:
    t0, t1, t2 = times
    store = KnowledgeStore()
    a, b = entity(EntityKind.ORGANIZATION, "A", t0), entity(EntityKind.ORGANIZATION, "B", t0)
    store.add_entity(a)
    store.add_entity(b)
    store.add_claim(claim("c", a.entity_id, "x", t0))
    store.add_relationship(
        Relationship(
            relationship_id="r",
            subject_entity_id=a.entity_id,
            relationship_type="supplies",
            object_entity_id=b.entity_id,
            valid_from=t1,
            valid_to=t2,
            source_claim_ids=("c",),
            confidence=0.9,
            review_status=ReviewStatus.ACCEPTED,
            system_from=t0,
        )
    )
    assert not store.relationships_at(t0)
    assert len(store.relationships_at(t1)) == 1
    assert not store.relationships_at(t2)  # M03-AC-007
    assert build_snapshot(store, t1).digest == build_snapshot(store, t1).digest  # M03-AC-011


def test_hs_isolation_and_pii_authorization(times) -> None:
    t0, _, _ = times
    hs17 = entity(EntityKind.PRODUCT_CLASS, "Widgets", t0, hs_version="HS2017", hs_code="847130")
    hs22 = entity(EntityKind.PRODUCT_CLASS, "Widgets", t0, hs_version="HS2022", hs_code="847130")
    assert hs17.entity_id != hs22.entity_id  # M03-AC-009
    person = entity(EntityKind.PERSON, "Ana", t0, email="ana@example.com", phone="123456789")
    store = KnowledgeStore()
    store.add_entity(person)
    anonymous = store.entities_at(t0)[0]
    assert "email" not in anonymous.attributes and "phone" not in anonymous.attributes
    allowed = store.entities_at(t0, Authorization("analyst", pii_read=True))[0]
    assert allowed.attributes["email"] == "ana@example.com"
    with pytest.raises(PIIAccessDenied):
        require_pii(Authorization("anonymous"))  # M03-AC-012


def test_deterministic_record_and_link_ids() -> None:
    assert deterministic_id("claim", "source", "row-1") == deterministic_id(
        "claim", "source", "row-1"
    )
    assert EntityLink.stable_id("source", "entity", "rapidfuzz/1.0.0") == EntityLink.stable_id(
        "source", "entity", "rapidfuzz/1.0.0"
    )  # M03-AC-001 link-decision rerun proof
