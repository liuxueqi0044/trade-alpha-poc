"""Lazy loading for declared analytical runtimes.

The PoC deliberately never emulates Parquet with JSON/CSV.  Deployments must
install the declared Arrow, DuckDB, and Polars dependencies to use M04.
"""

from __future__ import annotations

import importlib
from typing import Any


class StorageDependencyError(RuntimeError):
    pass


def require_analytical_engines() -> tuple[Any, Any, Any, Any]:
    try:
        return (
            importlib.import_module("pyarrow"),
            importlib.import_module("pyarrow.parquet"),
            importlib.import_module("duckdb"),
            importlib.import_module("polars"),
        )
    except ImportError as exc:
        raise StorageDependencyError(
            "M04 requires declared dependencies pyarrow, duckdb, and polars; "
            "install project storage runtime"
        ) from exc
