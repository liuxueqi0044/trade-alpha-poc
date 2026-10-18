from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trade_alpha.research import (
    FeatureDefinition,
    FeatureValue,
    LiveGateError,
    Mandate,
    ModelApproval,
    Opportunity,
    ResourceRequest,
    allocate,
    build_universe,
    create_limited_live_plan,
    feature_vector,
    recompute_score,
    score_opportunity,
    select_as_of,
)
from trade_alpha.research.backtest import (
    TrainOnlyStandardizer,
    baseline_report,
    chronological_folds,
    null_signal_simulation,
    replay_hash,
    seasonal_naive,
    simple_trend,
)
from trade_alpha.research.features import FutureLeakageError, assert_no_future_feature
from trade_alpha.research.registry import SearchAttempt, SearchRegistry, revision_sensitivity

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def mandate(**overrides: object) -> Mandate:
    values: dict[str, object] = {
        "mandate_id": "fixture",
        "version": "1",
        "capital_budget": 1000,
        "research_hours_budget": 20,
        "sample_budget": 100,
        "inventory_allowed": False,
        "max_trial_loss": 50,
        "target_turnover_days_max": 60,
        "excluded_hs_chapters": ("99",),
        "excluded_regulatory_classes": ("forbidden",),
        "country_risk_max": 0.5,
        "minimum_market_value": 10,
        "minimum_data_confidence": 0.5,
        "max_country_exposure": 0.3,
        "max_hs_chapter_exposure": 0.25,
        "exploration_budget_share": 0.15,
    }
    values.update(overrides)
    return Mandate(**values)


def opportunity(identifier: str = "a", **overrides: object) -> Opportunity:
    values: dict[str, object] = {
        "opportunity_id": identifier,
        "hs_code": "1234",
        "hs_chapter": "12",
        "import_market": "DE",
        "buyer_type": "B2B",
        "market_value": 100,
        "country_risk": 0.2,
        "data_confidence": 0.9,
        "as_of": NOW,
    }
    values.update(overrides)
    return Opportunity(**values)


def definition(name: str = "demand", **overrides: object) -> FeatureDefinition:
    values: dict[str, object] = {
        "feature_name": name,
        "feature_version": "1",
        "entity_grain": "opportunity",
        "availability_lag_days": 0,
        "missing_policy": "explicit_missing_indicator",
        "expected_direction": "positive",
        "weight": 2,
    }
    values.update(overrides)
    return FeatureDefinition(**values)


def test_m07_ac_001_002_configured_universe_has_reasons_and_no_whitelist() -> None:
    decisions = build_universe(
        mandate(),
        [
            opportunity("ok"),
            opportunity("excluded", hs_chapter="99"),
            opportunity("risk", country_risk=0.9),
        ],
    )
    assert [item.included for item in decisions] == [True, False, False]
    assert decisions[1].reasons == ("excluded_hs_chapter",)
    assert decisions[0].universe_version == "1"
    assert (
        build_universe(mandate(excluded_hs_chapters=("12",)), [opportunity()])[0].included is False
    )


def test_m07_ac_003_as_of_selector_rejects_future_leakage_sentinel() -> None:
    future = FeatureValue(
        opportunity_id="a",
        feature_name="demand",
        value=999,
        observed_at=NOW + timedelta(days=1),
        available_at=NOW + timedelta(days=1),
        feature_version="1",
    )
    assert select_as_of([future], NOW, [definition()]) == {}
    with pytest.raises(FutureLeakageError):
        assert_no_future_feature(future, NOW)
    forged = FeatureValue(
        opportunity_id="a",
        feature_name="demand",
        value=1,
        observed_at=NOW - timedelta(days=10),
        available_at=NOW - timedelta(days=10),
        feature_version="1",
    )
    with pytest.raises(FutureLeakageError, match="registry lag"):
        select_as_of([forged], NOW, [definition(availability_lag_days=30)])


def test_m07_ac_004_walk_forward_is_chronological_and_preprocesses_train_only() -> None:
    folds = chronological_folds(12, 5, 2, 2)
    assert all(max(item.train) < min(item.validation) < min(item.test) for item in folds)
    scaler = TrainOnlyStandardizer.fit([1, 2, 3])
    assert scaler.mean == 2
    assert scaler.transform([100])[0] > 90


def test_m07_ac_005_006_score_recomputes_and_missing_is_explicit() -> None:
    definitions = [definition(), definition("friction", expected_direction="negative")]
    values = {
        ("a", "demand"): FeatureValue(
            opportunity_id="a",
            feature_name="demand",
            value=3,
            observed_at=NOW,
            available_at=NOW,
            feature_version="1",
        )
    }
    vector, missing = feature_vector(definitions, values, "a")
    score = score_opportunity(
        "a",
        definitions,
        vector,
        missing,
        risk_components={"risk": 1},
        confidence_components={"source": 0.8, "fresh": 0.5},
        score_version="1",
    )
    assert missing["friction__missing"] == 1 and vector["friction"] is None
    assert score.final_score == pytest.approx(recompute_score(score))


def test_m07_ac_007_baselines_are_compared() -> None:
    history = [1, 2, 1, 2, 1, 2]
    report = baseline_report(
        [1, 2], seasonal_naive(history, 2, 2), simple_trend(history, 2), [1, 2]
    )
    assert report["incremental_value"] is False
    assert "seasonal_naive_mae" in report and "simple_trend_mae" in report


def test_m07_ac_008_replay_hash_is_seeded_and_deterministic() -> None:
    assert replay_hash("s", "c", "v", 7, ["a", "b"]) == replay_hash("s", "c", "v", 7, ["a", "b"])
    assert replay_hash("s", "c", "v", 7, ["a", "b"]) != replay_hash("s", "c", "v", 8, ["a", "b"])


def test_m07_ac_009_allocation_enforces_caps_and_costs() -> None:
    result = allocate(
        mandate(sample_budget=1_000),
        [
            ResourceRequest("ok", "DE", "12", 10, 0.9, 5, 40, 40),
            ResourceRequest("loss", "FR", "13", 9, 0.9, 5, 10, 51),
            ResourceRequest("country", "DE", "14", 8, 0.9, 5, 200, 40),
        ],
    )
    assert [item.opportunity_id for item in result.selected] == ["ok"]
    assert result.rejected["loss"] == "trial_loss_exceeds_cap"
    assert result.rejected["country"] == "country_exposure"
    assert (
        result.capital_used == 80 and result.sample_cost_used == 40 and result.trial_loss_used == 40
    )


def test_allocation_rejects_total_capital_even_when_each_country_and_hs_cap_allows_it() -> None:
    result = allocate(
        mandate(
            capital_budget=100,
            sample_budget=1_000,
            max_country_exposure=1,
            max_hs_chapter_exposure=1,
        ),
        [
            ResourceRequest("one", "DE", "12", 2, 0.9, 1, 30, 30),
            ResourceRequest("two", "FR", "13", 1, 0.9, 1, 25, 25),
        ],
    )
    assert [item.opportunity_id for item in result.selected] == ["one"]
    assert result.rejected["two"] == "capital_budget"


def test_m07_ac_010_null_simulation_reports_no_alpha() -> None:
    report = null_signal_simulation(10_000, seed=7)
    assert report["incremental_value"] is False


def test_m07_ac_013_live_gate_requires_complete_paper_approval() -> None:
    with pytest.raises(LiveGateError):
        create_limited_live_plan(ModelApproval(model_id="m", state="reviewed"))
    plan = create_limited_live_plan(
        ModelApproval(
            model_id="m",
            state="paper_approved",
            owner="owner",
            max_budget=10,
            failure_conditions=("loss",),
            rollback="stop",
            frozen_report_ref="report",
            scope="DE/12",
        )
    )
    assert plan.maximum_budget == 10


def test_m07_p1_revision_sensitivity_search_and_confidence_discount() -> None:
    assert revision_sensitivity(1, 1.5)["difference"] == 0.5
    registry = SearchRegistry()
    registry.record(SearchAttempt("f", "baseline", (), 0.1))
    registry.record(SearchAttempt("f2", "trend", (("p", "1"),), 0.2))
    assert registry.report()["selection_bias_risk"] == "elevated"
    defs = [definition()]
    vector = {"demand": 2.0}
    high = score_opportunity(
        "h",
        defs,
        vector,
        {"demand__missing": 0},
        risk_components={},
        confidence_components={"coverage": 1},
        score_version="1",
    )
    low = score_opportunity(
        "l",
        defs,
        vector,
        {"demand__missing": 0},
        risk_components={},
        confidence_components={"coverage": 0.2},
        score_version="1",
    )
    assert low.final_score <= high.final_score
