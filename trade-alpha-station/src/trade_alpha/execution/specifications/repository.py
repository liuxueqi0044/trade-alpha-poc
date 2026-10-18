"""Append-only in-memory repository for specification versions."""

from __future__ import annotations

# ruff: noqa: E501, I001

from datetime import datetime

from trade_alpha.contracts.common import require_utc

from .models import ProductSpec, ProductSpecVersion, SpecFieldDecision


class InMemoryProductSpecRepository:
    def __init__(self) -> None:
        self._specs: dict[str, ProductSpec] = {}
        self._decisions: list[SpecFieldDecision] = []
        self._versions: dict[str, list[ProductSpecVersion]] = {}

    def save_spec(self, spec: ProductSpec) -> ProductSpec:
        prior = self._specs.get(spec.spec_id)
        if prior is not None and prior != spec:
            raise ValueError("ProductSpec is immutable and conflicts with existing spec_id")
        self._specs.setdefault(spec.spec_id, spec)
        return spec

    def get_spec(self, spec_id: str) -> ProductSpec | None:
        return self._specs.get(spec_id)

    def save_decision(self, decision: SpecFieldDecision) -> SpecFieldDecision:
        prior = next(
            (item for item in self._decisions if item.decision_id == decision.decision_id), None
        )
        if prior is not None:
            if prior != decision:
                raise ValueError("SpecFieldDecision is immutable and conflicts with existing decision_id")
            return prior
        self._decisions.append(decision)
        return decision

    def decisions_for(self, spec_id: str, as_of: datetime) -> tuple[SpecFieldDecision, ...]:
        moment = require_utc(as_of)
        return tuple(
            sorted(
                (
                    item
                    for item in self._decisions
                    if item.spec_id == spec_id and item.decided_at <= moment
                ),
                key=lambda item: (item.decided_at, item.decision_id),
            )
        )

    def save_version(self, version: ProductSpecVersion) -> ProductSpecVersion:
        versions = self._versions.setdefault(version.spec_id, [])
        prior = next((item for item in versions if item.fingerprint == version.fingerprint), None)
        if prior is not None:
            return prior
        versions.append(version)
        return version

    def versions_for(self, spec_id: str) -> tuple[ProductSpecVersion, ...]:
        return tuple(self._versions.get(spec_id, ()))

    def get_as_of(self, spec_id: str, as_of: datetime) -> ProductSpecVersion | None:
        moment = require_utc(as_of)
        versions = [item for item in self.versions_for(spec_id) if item.created_at <= moment]
        return max(versions, key=lambda item: (item.created_at, item.sequence), default=None)

    def get_version(self, spec_version_id: str) -> ProductSpecVersion | None:
        return next(
            (
                item
                for versions in self._versions.values()
                for item in versions
                if item.spec_version_id == spec_version_id
            ),
            None,
        )
