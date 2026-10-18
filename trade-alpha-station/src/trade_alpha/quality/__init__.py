"""M05 deterministic contracts and quality publication gates."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


def _utc(v: datetime) -> datetime:
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return v.astimezone(UTC)


class QualityStatus(StrEnum):
    RECEIVED = "received"
    CONTRACT_VALID = "contract_valid"
    PUBLISHABLE = "publishable"
    PUBLISHED = "published"
    REJECTED_SCHEMA = "rejected_schema"
    QUARANTINED_QUALITY = "quarantined_quality"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class QualityIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    issue_id: str = Field(default_factory=lambda: str(uuid4()))
    asset_key: str
    snapshot_id: str
    rule_id: str
    severity: Severity
    record_locator: dict[str, Any] = {}
    observed_value: Any = None
    expected: Any = None
    detected_at: datetime
    status: str = "open"
    owner: str | None = None
    waiver_expires_at: datetime | None = None

    @field_validator("detected_at", "waiver_expires_at")
    @classmethod
    def aware(cls, v: Any) -> Any:
        return _utc(v) if v else v


class Waiver(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    waiver_id: str = Field(default_factory=lambda: str(uuid4()))
    rule_id: str
    owner: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware(cls, v: datetime) -> datetime:
        return _utc(v)

    @field_validator("owner", "reason")
    @classmethod
    def non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("waiver owner/reason cannot be blank")
        return v


class HsClass(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    classification_system: str
    version: str
    level: int = Field(ge=2)
    code: str = Field(min_length=1)


class ConcordanceRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source: HsClass
    target: HsClass
    concordance_version: str
    weight: float = Field(gt=0, le=1)
    weight_source: str


class TradeObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    observation_id: str
    source_snapshot_id: str
    country_code: str = Field(pattern=r"^[A-Z]{2,3}$")
    hs: HsClass
    observed_at: datetime
    value: float | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    quantity: float | None = None
    quantity_unit: str | None = None
    completeness_status: str = "complete"
    raw_values: dict[str, Any] = {}

    @field_validator("observed_at")
    @classmethod
    def aware(cls, v: datetime) -> datetime:
        return _utc(v)

    @model_validator(mode="after")
    def consistent(self) -> TradeObservation:
        if self.value is not None and self.currency is None:
            raise ValueError("currency required")
        if self.quantity is not None and self.quantity_unit is None:
            raise ValueError("quantity_unit required")
        return self


class FxRate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    from_currency: str
    to_currency: str
    rate: float = Field(gt=0)
    rate_version: str
    observed_at: datetime
    source: str

    @field_validator("observed_at")
    @classmethod
    def aware(cls, v: datetime) -> datetime:
        return _utc(v)


class UnitValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: float | None
    value_currency: str | None
    denominator: float | None
    denominator_unit: str | None
    is_proxy: bool = True
    anomaly_flag: bool = False
    missing_reason: str | None = None


class Lineage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    snapshot_id: str
    input_snapshot_ids: tuple[str, ...]
    model_name: str
    model_version: str
    code_sha: str
    output_hash: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def aware(cls, v: datetime) -> datetime:
        return _utc(v)


class QualityReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    snapshot_id: str
    status: QualityStatus
    accepted: tuple[TradeObservation, ...] = ()
    quarantined: tuple[dict[str, Any], ...] = ()
    issues: tuple[QualityIssue, ...] = ()
    lineage: Lineage | None = None


def validate_contract(rows: Iterable[Mapping[str, Any]], *, snapshot_id: str) -> QualityReport:
    good = []
    bad = []
    issues = []
    now = datetime.now(UTC)
    for i, raw in enumerate(rows):
        try:
            good.append(TradeObservation.model_validate(dict(raw)))
        except (ValidationError, ValueError, TypeError) as e:
            row = dict(raw)
            row["_quality_error"] = str(e)
            bad.append(row)
            issues.append(
                QualityIssue(
                    asset_key="trade_observation",
                    snapshot_id=snapshot_id,
                    rule_id="TRADE-SCHEMA-001",
                    severity=Severity.ERROR,
                    record_locator={"index": i},
                    observed_value=row,
                    detected_at=now,
                )
            )
    return QualityReport(
        snapshot_id=snapshot_id,
        status=QualityStatus.CONTRACT_VALID if not bad else QualityStatus.REJECTED_SCHEMA,
        accepted=tuple(good),
        quarantined=tuple(bad),
        issues=tuple(issues),
    )


def check_keys_and_relations(
    rows: Iterable[TradeObservation], *, known_countries: set[str]
) -> tuple[QualityIssue, ...]:
    seen = set()
    out = []
    now = datetime.now(UTC)
    for r in rows:
        if r.observation_id in seen:
            out.append(
                QualityIssue(
                    asset_key="trade_observation",
                    snapshot_id=r.source_snapshot_id,
                    rule_id="TRADE-PK-001",
                    severity=Severity.ERROR,
                    record_locator={"observation_id": r.observation_id},
                    detected_at=now,
                )
            )
        seen.add(r.observation_id)
        if r.country_code not in known_countries:
            out.append(
                QualityIssue(
                    asset_key="trade_observation",
                    snapshot_id=r.source_snapshot_id,
                    rule_id="TRADE-FK-COUNTRY-001",
                    severity=Severity.ERROR,
                    record_locator={"observation_id": r.observation_id},
                    observed_value=r.country_code,
                    detected_at=now,
                )
            )
    return tuple(out)


def apply_concordance(
    hs: HsClass, rows: Iterable[ConcordanceRow], *, concordance_version: str
) -> tuple[tuple[HsClass, float], ...]:
    return tuple(
        (r.target, r.weight)
        for r in rows
        if r.source == hs and r.concordance_version == concordance_version
    )


def point_in_time(
    rows: Iterable[TradeObservation], *, as_of: datetime
) -> tuple[TradeObservation, ...]:
    cutoff = _utc(as_of)
    return tuple(r for r in rows if r.observed_at <= cutoff)


def compute_unit_value(
    row: TradeObservation, *, denominator: str = "quantity", extreme_threshold: float | None = None
) -> UnitValue:
    d = getattr(row, denominator, None)
    u = getattr(row, f"{denominator}_unit", None)
    reason = (
        "value_missing"
        if row.value is None
        else "denominator_missing"
        if d is None
        else "zero_denominator"
        if d == 0
        else "invalid_denominator"
        if d < 0 or not math.isfinite(d)
        else "incompatible_unit"
        if u not in {"kg", "g", "unit", "units", "piece", "pieces"}
        else None
    )
    if reason:
        return UnitValue(
            value=None,
            value_currency=row.currency,
            denominator=d,
            denominator_unit=u,
            missing_reason=reason,
        )
    assert row.value is not None and isinstance(d, (int, float))
    value = row.value / d
    return UnitValue(
        value=value,
        value_currency=row.currency,
        denominator=d,
        denominator_unit=u,
        anomaly_flag=extreme_threshold is not None and abs(value) > extreme_threshold,
    )


def convert_currency(
    amount: float, currency: str, fx: FxRate, *, to_currency: str
) -> tuple[float, FxRate]:
    if currency != fx.from_currency or to_currency != fx.to_currency:
        raise ValueError("FX direction mismatch")
    return amount * fx.rate, fx


def completeness_gate(status: str, *, snapshot_id: str) -> QualityIssue | None:
    return (
        None
        if status == "complete"
        else QualityIssue(
            asset_key="gold_snapshot",
            snapshot_id=snapshot_id,
            rule_id="TRADE-BATCH-COMPLETE-001",
            severity=Severity.ERROR,
            observed_value=status,
            expected="complete",
            detected_at=datetime.now(UTC),
        )
    )


def waiver_active(w: Waiver | None, *, rule_id: str, at: datetime | None = None) -> bool:
    return bool(w and w.rule_id == rule_id and _utc(at or datetime.now(UTC)) < w.expires_at)


def publish_gate(
    *,
    issues: Iterable[QualityIssue],
    completeness_status: str,
    waivers: Iterable[Waiver] = (),
    evaluated_at: datetime,
) -> QualityStatus:
    if completeness_status != "complete":
        return QualityStatus.QUARANTINED_QUALITY
    return (
        QualityStatus.PUBLISHABLE
        if not [
            i
            for i in issues
            if i.severity == Severity.ERROR
            and not any(waiver_active(w, rule_id=i.rule_id, at=evaluated_at) for w in waivers)
        ]
        else QualityStatus.QUARANTINED_QUALITY
    )


def publish_gold(
    report: QualityReport,
    *,
    issues: Iterable[QualityIssue],
    completeness_status: str,
    lineage: Lineage,
    known_countries: set[str],
    waivers: Iterable[Waiver] = (),
    evaluated_at: datetime,
) -> QualityReport:
    """The only release entry point; rejected or incomplete data cannot publish."""
    if (
        not lineage.snapshot_id
        or not lineage.input_snapshot_ids
        or not lineage.model_version
        or not lineage.code_sha
    ):
        raise ValueError("complete lineage is required for release")
    if lineage.snapshot_id != report.snapshot_id:
        raise ValueError("lineage snapshot must match report snapshot")
    current = _utc(evaluated_at)
    active = tuple(waivers)
    relation_issues = check_keys_and_relations(report.accepted, known_countries=known_countries)
    blocking = tuple(
        issue
        for issue in (*report.issues, *issues, *relation_issues)
        if issue.severity == Severity.ERROR
        and not any(waiver_active(waiver, rule_id=issue.rule_id, at=current) for waiver in active)
    )
    if (
        report.status == QualityStatus.REJECTED_SCHEMA
        or completeness_status != "complete"
        or blocking
    ):
        return report.model_copy(
            update={"status": QualityStatus.QUARANTINED_QUALITY, "lineage": lineage}
        )
    return report.model_copy(update={"status": QualityStatus.PUBLISHED, "lineage": lineage})


def pandera_validate_rows(rows: Iterable[Mapping[str, Any]]) -> bool:
    """Execute a real Pandera dataframe contract when the optional dependency is installed."""
    import pandera.polars as pa
    import polars as pl

    frame = pl.DataFrame([dict(row) for row in rows])
    schema = pa.DataFrameSchema(
        {
            "observation_id": pa.Column(str, nullable=False),
            "country_code": pa.Column(
                str, checks=pa.Check.str_matches(r"^[A-Z]{2,3}$"), nullable=False
            ),
        },
        strict=False,
    )
    schema.validate(frame)
    return True


def frictionless_validate_file(path: str, *, expected_sha256: str, license_name: str) -> bool:
    """Validate a resource through Frictionless and verify license plus content hash."""
    from frictionless import Package

    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    if digest != expected_sha256:
        return False
    location = Path(path)
    package = Package(
        {
            "profile": "data-package",
            "licenses": [{"name": license_name}],
            "resources": [
                {
                    "name": "quality_input",
                    "path": location.name,
                    "profile": "tabular-data-resource",
                    "schema": {
                        "fields": [
                            {"name": "observation_id", "type": "string"},
                            {"name": "country_code", "type": "string"},
                        ]
                    },
                }
            ],
        },
        basepath=str(location.parent),
    )
    report = package.validate()
    return bool(report.valid and package.licenses)


def duckdb_point_in_time(
    rows: Iterable[TradeObservation], *, as_of: datetime
) -> tuple[TradeObservation, ...]:
    """Run PIT selection in DuckDB, retaining the Python contracts at the boundary."""
    import duckdb

    values = tuple(rows)
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "create table observations (ordinal integer, observation_id varchar, "
            "observed_at timestamptz)"
        )
        connection.executemany(
            "insert into observations values (?, ?, ?)",
            [(ordinal, row.observation_id, row.observed_at) for ordinal, row in enumerate(values)],
        )
        ordinals = {
            item[0]
            for item in connection.execute(
                "select ordinal from observations where observed_at <= ? order by ordinal",
                [_utc(as_of)],
            ).fetchall()
        }
        return tuple(row for ordinal, row in enumerate(values) if ordinal in ordinals)
    finally:
        connection.close()


def duckdb_replay_hash(rows: Iterable[TradeObservation], *, as_of: datetime) -> str:
    selected = duckdb_point_in_time(rows, as_of=as_of)
    return stable_output_hash([row.model_dump(mode="json") for row in selected])


def stable_output_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            sorted((dict(r) for r in rows), key=lambda r: str(r.get("observation_id", r))),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def build_lineage(
    *,
    snapshot_id: str,
    input_snapshot_ids: Iterable[str],
    model_name: str,
    model_version: str,
    code_sha: str,
    rows: Iterable[Mapping[str, Any]],
    created_at: datetime | None = None,
) -> Lineage:
    return Lineage(
        snapshot_id=snapshot_id,
        input_snapshot_ids=tuple(input_snapshot_ids),
        model_name=model_name,
        model_version=model_version,
        code_sha=code_sha,
        output_hash=stable_output_hash(rows),
        created_at=created_at or datetime.now(UTC),
    )


def detect_drift(
    values: list[float],
    baseline: list[float],
    *,
    threshold: float = 0.2,
    snapshot_id: str = "drift",
) -> QualityIssue | None:
    if not baseline:
        return None
    delta = abs((sum(values) / len(values) if values else 0) - sum(baseline) / len(baseline)) / max(
        abs(sum(baseline) / len(baseline)), 1e-12
    )
    return (
        None
        if delta <= threshold
        else QualityIssue(
            asset_key="quality",
            snapshot_id=snapshot_id,
            rule_id="TRADE-DRIFT-001",
            severity=Severity.WARNING,
            observed_value={"delta": delta},
            expected={"max_delta": threshold},
            detected_at=datetime.now(UTC),
        )
    )


def validate_dbt_metadata(root: str) -> tuple[str, ...]:
    """Static dbt docs/dependency validator when dbt itself is not installed."""
    from pathlib import Path

    import yaml  # type: ignore[import-untyped]

    base = Path(root)
    errors: list[str] = []
    schema_path = base / "models" / "schema.yml"
    if not schema_path.exists():
        return ("missing_models_schema",)
    document = yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}
    sources = {source.get("name") for source in document.get("sources", [])}
    source_tables = {
        (source.get("name"), table.get("name"))
        for source in document.get("sources", [])
        for table in source.get("tables", [])
    }
    for model in document.get("models", []):
        name = model.get("name", "<unnamed>")
        if not model.get("description"):
            errors.append(f"{name}:missing_description")
        metadata = model.get("meta") or {}
        for key in ("owner", "grain", "model_version"):
            if not str(metadata.get(key, "")).strip():
                errors.append(f"{name}:missing_{key}")
        for column in model.get("columns", []):
            column_name = column.get("name", "<unnamed>")
            if not column.get("description"):
                errors.append(f"{name}.{column_name}:missing_description")
            if not column.get("tests"):
                errors.append(f"{name}.{column_name}:missing_tests")
    for sql_path in (base / "models").rglob("*.sql"):
        sql_text = sql_path.read_text(encoding="utf-8")
        import re

        for source_name, table_name in re.findall(
            r"source\(['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)", sql_text
        ):
            if source_name not in sources or (source_name, table_name) not in source_tables:
                errors.append(f"{sql_path.name}:unknown_source:{source_name}.{table_name}")
    return tuple(errors)
