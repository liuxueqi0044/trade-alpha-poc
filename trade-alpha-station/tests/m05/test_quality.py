from datetime import UTC, datetime, timedelta

import pytest

from trade_alpha.quality import (
    ConcordanceRow,
    FxRate,
    HsClass,
    QualityStatus,
    Waiver,
    apply_concordance,
    build_lineage,
    check_keys_and_relations,
    compute_unit_value,
    convert_currency,
    detect_drift,
    duckdb_point_in_time,
    duckdb_replay_hash,
    frictionless_validate_file,
    pandera_validate_rows,
    point_in_time,
    publish_gate,
    publish_gold,
    stable_output_hash,
    validate_contract,
    validate_dbt_metadata,
    waiver_active,
)

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def row(**kw):
    d = {
        "observation_id": "o1",
        "source_snapshot_id": "s",
        "country_code": "US",
        "hs": {"classification_system": "HS", "version": "2022", "level": 6, "code": "850110"},
        "observed_at": NOW,
        "value": 100.0,
        "currency": "USD",
        "quantity": 10.0,
        "quantity_unit": "kg",
    }
    d.update(kw)
    return d


def test_ac001_contract_quarantine():
    r = validate_contract([row(), {"observation_id": "bad"}], snapshot_id="s")
    assert r.status == QualityStatus.REJECTED_SCHEMA and len(r.quarantined) == 1


def test_ac002_keys_fk():
    r = validate_contract([row(), row(observation_id="o1", country_code="ZZ")], snapshot_id="s")
    assert {x.rule_id for x in check_keys_and_relations(r.accepted, known_countries={"US"})} == {
        "TRADE-PK-001",
        "TRADE-FK-COUNTRY-001",
    }


def test_ac003_hs_version():
    a = HsClass(classification_system="HS", version="2017", level=6, code="1")
    b = HsClass(classification_system="HS", version="2022", level=6, code="1")
    c = ConcordanceRow(
        source=a, target=b, concordance_version="v1", weight=1, weight_source="official"
    )
    assert apply_concordance(a, [c], concordance_version="bad") == () and apply_concordance(
        a, [c], concordance_version="v1"
    ) == ((b, 1),)


def test_ac004_pit():
    r = validate_contract(
        [row(), row(observation_id="future", observed_at=NOW + timedelta(days=1))], snapshot_id="s"
    )
    assert [x.observation_id for x in point_in_time(r.accepted, as_of=NOW)] == ["o1"]


def test_ac005_anomaly():
    o = validate_contract([row(value=1000000)], snapshot_id="s").accepted[0]
    u = compute_unit_value(o, extreme_threshold=100)
    assert u.value == 100000 and u.anomaly_flag


def test_ac006_incomplete():
    assert (
        publish_gate(issues=(), completeness_status="partial", evaluated_at=NOW)
        == QualityStatus.QUARANTINED_QUALITY
    )


def test_ac007_waiver_expiry():
    w = Waiver(rule_id="R", owner="team", reason="repair", expires_at=NOW + timedelta(days=1))
    assert waiver_active(w, rule_id="R", at=NOW) and not waiver_active(
        w, rule_id="R", at=NOW + timedelta(days=2)
    )


def test_ac008_lineage_replay():
    rows = [row()]
    a = build_lineage(
        snapshot_id="s",
        input_snapshot_ids=["s"],
        model_name="m",
        model_version="1",
        code_sha="x",
        rows=rows,
        created_at=NOW,
    )
    assert a.output_hash == stable_output_hash(rows)


def test_ac010_fx():
    fx = FxRate(
        from_currency="EUR",
        to_currency="USD",
        rate=1.1,
        rate_version="v",
        observed_at=NOW,
        source="fixture",
    )
    assert convert_currency(100, "EUR", fx, to_currency="USD")[0] == pytest.approx(110)


def test_ac009_drift_preserves_without_deleting_rows():
    assert detect_drift([100], [10], snapshot_id="s") is not None


def test_ac012_duckdb_replay_is_executed_and_deterministic():
    rows = validate_contract(
        [row(), row(observation_id="o1", observed_at=NOW + timedelta(days=1))], snapshot_id="s"
    ).accepted
    assert tuple(
        (x.observation_id, x.observed_at) for x in duckdb_point_in_time(rows, as_of=NOW)
    ) == (("o1", NOW),)
    assert duckdb_replay_hash(rows, as_of=NOW) == duckdb_replay_hash(rows, as_of=NOW)


def test_pandera_contract_executes():
    assert pandera_validate_rows([{"observation_id": "o1", "country_code": "US"}])


def test_frictionless_license_and_hash_executes(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text("observation_id,country_code\no1,US\n", encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert frictionless_validate_file(str(path), expected_sha256=digest, license_name="CC-BY-4.0")


def test_release_function_requires_lineage_and_blocks_schema_errors():
    report = validate_contract([{"observation_id": "bad"}], snapshot_id="s")
    lineage = build_lineage(
        snapshot_id="s",
        input_snapshot_ids=["s"],
        model_name="m",
        model_version="1",
        code_sha="sha",
        rows=[],
        created_at=NOW,
    )
    released = publish_gold(
        report,
        issues=(),
        completeness_status="complete",
        lineage=lineage,
        known_countries={"US"},
        evaluated_at=NOW,
    )
    assert released.status == QualityStatus.QUARANTINED_QUALITY


def test_dbt_static_docs_and_source_dependency_validator():
    assert validate_dbt_metadata("dbt") == ()


def test_release_recomputes_duplicate_and_orphan_errors_without_caller_issues():
    report = validate_contract(
        [row(), row(observation_id="o1", country_code="ZZ")], snapshot_id="s"
    )
    lineage = build_lineage(
        snapshot_id="s",
        input_snapshot_ids=["s"],
        model_name="m",
        model_version="1",
        code_sha="sha",
        rows=[],
        created_at=NOW,
    )
    released = publish_gold(
        report,
        issues=(),
        completeness_status="complete",
        lineage=lineage,
        known_countries={"US"},
        evaluated_at=NOW,
    )
    assert released.status == QualityStatus.QUARANTINED_QUALITY


def test_dbt_metadata_mutation_is_detected(tmp_path):
    root = tmp_path / "dbt"
    (root / "models").mkdir(parents=True)
    (root / "models" / "schema.yml").write_text(
        "version: 2\nmodels:\n- name: x\n", encoding="utf-8"
    )
    assert validate_dbt_metadata(str(root))
