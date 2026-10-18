"""Point-in-time feature registry and leakage-safe selection."""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import FeatureDefinition, FeatureValue


class FutureLeakageError(ValueError):
    pass


def _assert_registry_lag(definition: FeatureDefinition, value: FeatureValue) -> None:
    earliest_available = value.observed_at + timedelta(days=definition.availability_lag_days)
    if value.available_at < earliest_available:
        raise FutureLeakageError(
            f"feature {value.feature_name} violates "
            f"{definition.availability_lag_days}-day registry lag"
        )


def select_as_of(
    values: list[FeatureValue], as_of: datetime, definitions: list[FeatureDefinition]
) -> dict[tuple[str, str], FeatureValue]:
    """Select the newest feature actually observable at ``as_of``."""

    selected: dict[tuple[str, str], FeatureValue] = {}
    definitions_by_name = {definition.feature_name: definition for definition in definitions}
    for value in values:
        definition = definitions_by_name.get(value.feature_name)
        if definition is None:
            raise ValueError(f"unregistered feature: {value.feature_name}")
        _assert_registry_lag(definition, value)
        if value.observed_at > as_of or value.available_at > as_of:
            continue
        key = (value.opportunity_id, value.feature_name)
        prior = selected.get(key)
        if prior is None or (value.available_at, value.observed_at) > (
            prior.available_at,
            prior.observed_at,
        ):
            selected[key] = value
    return selected


def assert_no_future_feature(value: FeatureValue, as_of: datetime) -> None:
    if value.observed_at > as_of or value.available_at > as_of:
        raise FutureLeakageError(
            f"feature {value.feature_name} is not known as of {as_of.isoformat()}"
        )


def feature_vector(
    definitions: list[FeatureDefinition],
    values: dict[tuple[str, str], FeatureValue],
    opportunity_id: str,
) -> tuple[dict[str, float | None], dict[str, int]]:
    """Return values plus explicit missing flags; missing is never silently zero."""

    vector: dict[str, float | None] = {}
    indicators: dict[str, int] = {}
    for definition in definitions:
        value = values.get((opportunity_id, definition.feature_name))
        if value is not None:
            _assert_registry_lag(definition, value)
        missing = value is None or value.value is None
        if missing and definition.missing_policy == "reject":
            raise ValueError(f"required feature missing: {definition.feature_name}")
        if value is None or value.value is None:
            vector[definition.feature_name] = None
        else:
            vector[definition.feature_name] = value.value
        indicators[f"{definition.feature_name}__missing"] = int(missing)
    return vector, indicators
