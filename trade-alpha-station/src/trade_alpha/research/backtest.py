"""Chronological validation, transparent baselines, and deterministic replay."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    train: tuple[int, ...]
    validation: tuple[int, ...]
    test: tuple[int, ...]


def chronological_folds(
    size: int, train_size: int, validation_size: int, test_size: int
) -> list[WalkForwardFold]:
    if min(train_size, validation_size, test_size) <= 0:
        raise ValueError("fold windows must be positive")
    folds: list[WalkForwardFold] = []
    start = 0
    while start + train_size + validation_size + test_size <= size:
        train = tuple(range(start, start + train_size))
        validation = tuple(range(train[-1] + 1, train[-1] + 1 + validation_size))
        test = tuple(range(validation[-1] + 1, validation[-1] + 1 + test_size))
        folds.append(WalkForwardFold(train, validation, test))
        start += test_size
    return folds


@dataclass(frozen=True, slots=True)
class TrainOnlyStandardizer:
    mean: float
    standard_deviation: float

    @classmethod
    def fit(cls, values: Sequence[float]) -> TrainOnlyStandardizer:
        array = np.asarray(values, dtype=float)
        return cls(float(array.mean()), float(array.std()) or 1.0)

    def transform(self, values: Sequence[float]) -> list[float]:
        return [float((value - self.mean) / self.standard_deviation) for value in values]


def seasonal_naive(history: Sequence[float], horizon: int, season_length: int) -> list[float]:
    if len(history) < season_length:
        raise ValueError("history shorter than season length")
    return [float(history[-season_length + index % season_length]) for index in range(horizon)]


def simple_trend(history: Sequence[float], horizon: int) -> list[float]:
    if len(history) < 2:
        raise ValueError("trend needs at least two observations")
    x = np.arange(len(history), dtype=float)
    slope, intercept = np.polyfit(x, np.asarray(history, dtype=float), 1)
    return [float(slope * (len(history) + index) + intercept) for index in range(horizon)]


def mean_absolute_error(actual: Sequence[float], predicted: Sequence[float]) -> float:
    return float(np.mean(np.abs(np.asarray(actual) - np.asarray(predicted))))


def baseline_report(
    actual: Sequence[float],
    seasonal: Sequence[float],
    trend: Sequence[float],
    complex_model: Sequence[float],
) -> dict[str, float | bool]:
    seasonal_mae = mean_absolute_error(actual, seasonal)
    trend_mae = mean_absolute_error(actual, trend)
    complex_mae = mean_absolute_error(actual, complex_model)
    return {
        "seasonal_naive_mae": seasonal_mae,
        "simple_trend_mae": trend_mae,
        "complex_mae": complex_mae,
        "incremental_value": complex_mae < min(seasonal_mae, trend_mae),
    }


def replay_hash(
    snapshot_id: str, code_version: str, config_version: str, seed: int, ranking: Sequence[str]
) -> str:
    payload = json.dumps(
        {
            "snapshot": snapshot_id,
            "code": code_version,
            "config": config_version,
            "seed": seed,
            "ranking": list(ranking),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def null_signal_simulation(size: int, seed: int = 7) -> dict[str, float | bool]:
    """A seeded no-signal control that must not be promoted as alpha."""

    generator = np.random.default_rng(seed)
    signal = generator.normal(size=size)
    outcome = generator.normal(size=size)
    correlation = float(np.corrcoef(signal, outcome)[0, 1])
    return {"rank_ic": correlation, "incremental_value": abs(correlation) >= 0.2}
