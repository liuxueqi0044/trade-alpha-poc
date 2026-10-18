from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trade_alpha.storage import (
    REGION_CONTRACTS,
    MoneyObservation,
    assert_operational_table,
    scan_gold_dependencies,
)
from trade_alpha.storage.boundary import OperationalBoundaryError
from trade_alpha.storage.models import BitemporalObservation


def test_regions_are_explicitly_separated() -> None:
    assert REGION_CONTRACTS["raw"].mutable is False
    assert REGION_CONTRACTS["gold"].published is True
    assert "work" in REGION_CONTRACTS["gold"].forbidden_content


def test_m04_ac_004_static_scan_rejects_gold_work_and_tmp_dependencies(tmp_path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("SOURCE = 'silver/snapshot'", encoding="utf-8")
    assert scan_gold_dependencies(tmp_path) == []
    bad = tmp_path / "gold.py"
    bad.write_text("read_parquet('work/intermediate.parquet')", encoding="utf-8")
    assert scan_gold_dependencies(tmp_path) == [bad]


def test_m04_ac_005_operational_boundary_rejects_historical_facts() -> None:
    assert_operational_table("run_state")
    with pytest.raises(OperationalBoundaryError):
        assert_operational_table("observations")


def test_m04_ac_010_money_requires_original_currency_and_fx_provenance() -> None:
    original = MoneyObservation(original_amount=10, original_currency="usd")
    assert original.original_currency == "USD"
    with pytest.raises(ValidationError, match="converted money"):
        MoneyObservation(
            original_amount=10,
            original_currency="USD",
            converted_amount=70,
            converted_currency="CNY",
        )
    converted = MoneyObservation(
        original_amount=10,
        original_currency="USD",
        converted_amount=70,
        converted_currency="CNY",
        fx_source="ECB",
        fx_date=datetime(2026, 8, 1, tzinfo=UTC),
        fx_version="2026-08-01",
        fx_rate=7,
    )
    assert converted.fx_source == "ECB"
    with pytest.raises(ValidationError, match="times fx_rate"):
        MoneyObservation(
            original_amount=10,
            original_currency="USD",
            converted_amount=69,
            converted_currency="CNY",
            fx_source="ECB",
            fx_date=datetime(2026, 8, 1, tzinfo=UTC),
            fx_version="2026-08-01",
            fx_rate=7,
        )


def test_m04_ac_011_bitemporal_times_are_utc_and_preserve_local_time() -> None:
    observation = BitemporalObservation(
        natural_key="fixture",
        value=1,
        valid_from=datetime(2026, 5, 1, 8, tzinfo=UTC),
        observed_at=datetime(2026, 8, 1, tzinfo=UTC),
        system_from=datetime(2026, 8, 1, tzinfo=UTC),
        source_local_time="2026-05-01T16:00:00+08:00",
        source_timezone="Asia/Shanghai",
    )
    assert observation.system_from.tzinfo is UTC
    assert observation.source_local_time.endswith("+08:00")
    with pytest.raises(ValidationError, match="supplied together"):
        BitemporalObservation(
            natural_key="partial",
            value=1,
            valid_from=datetime(2026, 5, 1, tzinfo=UTC),
            observed_at=datetime(2026, 8, 1, tzinfo=UTC),
            system_from=datetime(2026, 8, 1, tzinfo=UTC),
            source_local_time="2026-08-01T08:00:00+08:00",
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        BitemporalObservation(
            natural_key="bad",
            value=1,
            valid_from=datetime(2026, 5, 1),
            observed_at=datetime(2026, 8, 1, tzinfo=UTC),
            system_from=datetime(2026, 8, 1, tzinfo=UTC),
        )
