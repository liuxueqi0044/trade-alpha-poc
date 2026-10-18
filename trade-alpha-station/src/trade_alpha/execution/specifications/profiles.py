"""Validated loading for versioned product-family profile YAML."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]

from .models import ProductFamilyProfile


def load_profile(path: str | Path) -> ProductFamilyProfile:
    with Path(path).open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return ProductFamilyProfile.model_validate(data)
