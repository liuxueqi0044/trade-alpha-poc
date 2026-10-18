from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_alpha.contracts.common import EvidenceLocator, VersionRef
from trade_alpha.execution.adapters.pint_units import PintUnitConverter
from trade_alpha.execution.contracts import (
    DecisionOutcome,
    FxSnapshot,
    HumanDecision,
    Incoterm,
    MeasuredRange,
    MeasuredValue,
    MonetaryAmount,
    ProvenanceRef,
    Tolerance,
    canonical_json,
    stable_fingerprint,
)
from trade_alpha.execution.contracts.models import _UNIT_CODES

NOW = datetime(2026, 8, 26, 8, tzinfo=UTC)
VERSION = VersionRef(name="fixture", version="1.0.0")
LOCATOR = EvidenceLocator(artifact_id="artifact-1", block_ids=("block-1",))


def provenance() -> ProvenanceRef:
    return ProvenanceRef(
        source="supplier quote",
        evidence=LOCATOR,
        observed_at=NOW,
        source_version=VERSION,
    )


def public_values() -> tuple[object, ...]:
    length = MeasuredValue(amount=Decimal("25.4"), unit="mm")
    return (
        MonetaryAmount(amount=Decimal("19.99"), currency="USD"),
        length,
        MeasuredRange(minimum=MeasuredValue(amount=Decimal("20"), unit="mm"), maximum=length),
        Tolerance(absolute=MeasuredValue(amount=Decimal("0.1"), unit="mm")),
        FxSnapshot(
            from_currency="USD",
            to_currency="CNY",
            rate=Decimal("7.2"),
            observed_at=NOW,
            source="ECB",
            source_version=VERSION,
            evidence=LOCATOR,
        ),
        Incoterm(code="FOB", rules_version="Incoterms 2020", named_place="Shanghai Port"),
        provenance(),
        HumanDecision(
            decision_id="decision-1",
            outcome=DecisionOutcome.ACCEPTED,
            actor="reviewer",
            decided_at=NOW,
            rationale="evidence is sufficient",
            evidence=(provenance(),),
        ),
    )


def test_ep01_ac_001_public_values_round_trip_as_stable_json() -> None:
    for value in public_values():
        assert type(value).model_validate_json(value.model_dump_json()) == value
        assert json.loads(canonical_json(value)) == value.model_dump(mode="json")


def test_ep01_ac_002_rejects_invalid_currency_unit_dimension_and_incoterm_place() -> None:
    with pytest.raises(ValidationError):
        MonetaryAmount(amount=Decimal("1"), currency="ZZZ")
    with pytest.raises(ValidationError, match="unknown unit"):
        MeasuredValue(amount=Decimal("1"), unit="made-up-unit")
    with pytest.raises(ValueError, match="unit dimensions"):
        PintUnitConverter().convert(MeasuredValue(amount=Decimal("1"), unit="kg"), to_unit="mm")
    with pytest.raises(ValidationError, match="named place"):
        Incoterm(code="FOB", rules_version="Incoterms 2020", named_place="  ")


def test_ep01_ac_003_pint_conversion_retains_decimal_without_float() -> None:
    converted = PintUnitConverter().convert(
        MeasuredValue(amount=Decimal("25.4"), unit="mm"), to_unit="inch"
    )
    assert converted == MeasuredValue(amount=Decimal("1"), unit="inch")
    assert isinstance(converted.amount, Decimal)
    with pytest.raises(ValidationError, match="binary float"):
        MeasuredValue(amount=25.4, unit="mm")


def test_every_persisted_unit_code_round_trips_at_the_adapter_boundary() -> None:
    converter = PintUnitConverter()
    for unit in _UNIT_CODES:
        value = MeasuredValue(amount=Decimal("1"), unit=unit)
        assert converter.convert(value, to_unit=unit) == value


def test_ep01_ac_004_cross_currency_requires_explicit_fx_snapshot() -> None:
    usd = MonetaryAmount(amount=Decimal("10"), currency="USD")
    cny = MonetaryAmount(amount=Decimal("72"), currency="CNY")
    with pytest.raises(ValueError, match="FxSnapshot"):
        usd.add(cny)
    fx = FxSnapshot(
        from_currency="CNY",
        to_currency="USD",
        rate=Decimal("0.1388888888888888888888888889"),
        observed_at=NOW,
        source="ECB",
        evidence=LOCATOR,
    )
    assert usd.add(cny, fx_snapshot=fx).currency == "USD"


def test_ep01_ac_005_fingerprint_is_stable_sha256() -> None:
    value = MonetaryAmount(amount=Decimal("1.00"), currency="USD")
    first = stable_fingerprint(value)
    assert first == stable_fingerprint(value.model_dump(mode="json"))
    assert first.startswith("sha256:") and len(first) == 71


def test_monetary_amount_keeps_signed_discounts_and_refunds() -> None:
    refund = MonetaryAmount(amount=Decimal("-2.50"), currency="USD")
    assert MonetaryAmount.model_validate_json(refund.model_dump_json()) == refund
    subtotal = MonetaryAmount(amount=Decimal("10"), currency="USD").add(refund)
    assert subtotal.amount == Decimal("7.50")


def test_fingerprint_normalizes_mapping_decimal_and_datetime_like_a_model() -> None:
    model = FxSnapshot(
        from_currency="USD",
        to_currency="CNY",
        rate=Decimal("7.2"),
        observed_at=NOW,
        source="ECB",
        evidence=LOCATOR,
    )
    mapping = model.model_dump()
    assert stable_fingerprint(mapping) == stable_fingerprint(model)


def test_human_decision_can_be_artifact_free_but_fx_cannot() -> None:
    decision = HumanDecision(
        decision_id="meeting-1",
        outcome=DecisionOutcome.DEFERRED,
        actor="reviewer",
        decided_at=NOW,
        rationale="buyer interview needs follow-up",
    )
    assert not decision.evidence
    with pytest.raises(ValidationError):
        FxSnapshot(
            from_currency="USD",
            to_currency="CNY",
            rate=Decimal("7.2"),
            observed_at=NOW,
            source="ECB",
        )


def test_ep01_ac_006_contract_package_has_no_side_effect_adapters() -> None:
    forbidden = ("http", "socket", "sqlite", "duckdb", "pathlib", "requests", "rfq")
    contract_root = Path(__file__).parents[2] / "src" / "trade_alpha" / "execution" / "contracts"
    source = "\n".join(path.read_text(encoding="utf-8") for path in contract_root.glob("*.py"))
    assert not any(token in source.lower() for token in forbidden)


def test_ep01_ac_007_schema_and_frozen_extra_forbid_contract() -> None:
    schema = MonetaryAmount.model_json_schema()
    assert schema["additionalProperties"] is False
    with pytest.raises(ValidationError):
        MonetaryAmount(amount=Decimal("1"), currency="USD", unexpected=True)
    with pytest.raises(ValidationError):
        MonetaryAmount(amount=Decimal("1"), currency="USD").amount = Decimal("2")
