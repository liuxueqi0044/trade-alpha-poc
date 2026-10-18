"""Executable validation for the minimum recovery-runbook contract."""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import build_asset_graph

_REQUIRED_SECTIONS = (
    "Common failures",
    "Automatic retry",
    "Safe rerun",
    "Input completeness",
    "Rollback publication alias",
    "Rebuild lineage",
    "Escalate to human approval",
)


def validate_runbooks(path: Path) -> tuple[str, ...]:
    """Return missing requirements, suitable for CI and drill gates."""
    text = path.read_text(encoding="utf-8")
    errors = [
        f"missing section: {section}" for section in _REQUIRED_SECTIONS if section not in text
    ]
    for asset_key in build_asset_graph():
        if f"### `{asset_key}`" not in text:
            errors.append(f"missing asset runbook: {asset_key}")
    if "-m pytest tests/m06" not in text:
        errors.append("missing executable recovery command")
    try:
        checklist = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return (*errors, "missing or invalid structured runbook checklist")
    assets = checklist.get("assets", {})
    for asset_key in build_asset_graph():
        entry = assets.get(asset_key, {})
        for field in ("failure_scenarios", "retry", "command", "rollback", "lineage", "escalation"):
            if not entry.get(field):
                errors.append(f"incomplete executable checklist: {asset_key}.{field}")
        if "-m pytest tests/m06" not in str(entry.get("command", "")):
            errors.append(f"non-executable recovery command: {asset_key}")
    return tuple(errors)


def rehearse_runbook(path: Path, asset_key: str, failure_scenario: str) -> str:
    """Select a concrete, validated recovery command for a drill scenario."""
    errors = validate_runbooks(path)
    if errors:
        raise ValueError(f"invalid runbook: {errors}")
    checklist = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    entry = checklist["assets"].get(asset_key)
    if entry is None or failure_scenario not in entry["failure_scenarios"]:
        raise KeyError(f"no rehearsal for {asset_key}/{failure_scenario}")
    return str(entry["command"])
