"""Transparent parameter-search and revision-sensitivity records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchAttempt:
    feature_set: str
    model: str
    parameters: tuple[tuple[str, str], ...]
    metric: float


class SearchRegistry:
    def __init__(self) -> None:
        self._attempts: list[SearchAttempt] = []

    def record(self, attempt: SearchAttempt) -> None:
        self._attempts.append(attempt)

    def report(self) -> dict[str, int | str]:
        return {
            "attempt_count": len(self._attempts),
            "selection_bias_risk": "elevated" if len(self._attempts) > 1 else "baseline",
        }


def revision_sensitivity(first_vintage: float, latest_vintage: float) -> dict[str, float]:
    return {
        "first_vintage": first_vintage,
        "latest_vintage": latest_vintage,
        "difference": latest_vintage - first_vintage,
    }
