"""Explicit boundary between transactional operational state and analytical Parquet."""

from __future__ import annotations

from pathlib import Path

OPERATIONAL_TABLES = frozenset(
    {"source_registry", "run_state", "review", "experiment", "permission"}
)
FORBIDDEN_OPERATIONAL_TABLES = frozenset({"raw", "observation", "claim", "feature", "signal"})


class OperationalBoundaryError(ValueError):
    pass


def assert_operational_table(name: str) -> None:
    if (
        name in FORBIDDEN_OPERATIONAL_TABLES
        or name.endswith("s")
        and name[:-1] in FORBIDDEN_OPERATIONAL_TABLES
    ):
        raise OperationalBoundaryError(
            f"historical analytical data cannot be operational state: {name}"
        )
    if name not in OPERATIONAL_TABLES:
        raise OperationalBoundaryError(f"unrecognised operational table: {name}")


def scan_gold_dependencies(source_root: str | Path) -> list[Path]:
    """Static CI check: gold code may not read transient work/tmp paths."""

    root = Path(source_root)
    violations: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".sql", ".yaml", ".yml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").replace("\\", "/")
        if "work/" in text or "tmp/" in text:
            violations.append(path)
    return violations
