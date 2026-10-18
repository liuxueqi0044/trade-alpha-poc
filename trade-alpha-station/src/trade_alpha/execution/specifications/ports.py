"""Ports used by the specification service."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trade_alpha.knowledge import Claim

from .models import ProductSpec, ProductSpecVersion, SpecFieldDecision


class ClaimRepositoryPort(Protocol):
    def claims_for(self, subject_ref: str, as_of: datetime) -> tuple[Claim, ...]: ...


class ProductSpecRepositoryPort(Protocol):
    def save_spec(self, spec: ProductSpec) -> ProductSpec: ...

    def get_spec(self, spec_id: str) -> ProductSpec | None: ...

    def save_decision(self, decision: SpecFieldDecision) -> SpecFieldDecision: ...

    def decisions_for(self, spec_id: str, as_of: datetime) -> tuple[SpecFieldDecision, ...]: ...

    def save_version(self, version: ProductSpecVersion) -> ProductSpecVersion: ...

    def versions_for(self, spec_id: str) -> tuple[ProductSpecVersion, ...]: ...

    def get_as_of(self, spec_id: str, as_of: datetime) -> ProductSpecVersion | None: ...

    def get_version(self, spec_version_id: str) -> ProductSpecVersion | None: ...


class DecisionAuthorizationPort(Protocol):
    def may_freeze(self, actor_id: str, spec_id: str) -> bool: ...
