"""Append-only in-memory EP-05 repository with point-in-time reads."""

from __future__ import annotations

# ruff: noqa: E501
from datetime import datetime

from trade_alpha.contracts.common import require_utc

from .models import InternalDryRunDecision, SupplierRfqCampaign, SupplierRfqPackageSnapshot


class InMemorySupplierRfqRepository:
    def __init__(self) -> None:
        self._campaigns: dict[str, list[SupplierRfqCampaign]] = {}
        self._packages: dict[str, list[SupplierRfqPackageSnapshot]] = {}
        self._decisions: list[InternalDryRunDecision] = []

    @staticmethod
    def _as_of(value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    def save_campaign(self, campaign: SupplierRfqCampaign) -> SupplierRfqCampaign:
        values = self._campaigns.setdefault(campaign.campaign_id, [])
        existing = next((item for item in values if item.campaign_fingerprint == campaign.campaign_fingerprint), None)
        if existing is not None:
            return existing
        if any(item.campaign_version == campaign.campaign_version for item in values):
            raise ValueError("campaign version is immutable")
        values.append(campaign)
        return campaign

    def get_campaign(self, campaign_id: str, *, as_of: datetime | None = None) -> SupplierRfqCampaign | None:
        values = self._campaigns.get(campaign_id, [])
        moment = self._as_of(as_of)
        if moment is not None:
            values = [item for item in values if item.created_at <= moment]
        return max(values, key=lambda item: (item.campaign_version, item.created_at), default=None)

    def save_package(self, package: SupplierRfqPackageSnapshot) -> SupplierRfqPackageSnapshot:
        values = self._packages.setdefault(package.supplier_rfq_package_id, [])
        existing = next((item for item in values if item.content_fingerprint == package.content_fingerprint), None)
        if existing is not None:
            return existing
        if any(item.version == package.version for item in values):
            raise ValueError("package version is immutable")
        values.append(package)
        return package

    def get_package(self, package_id: str, *, as_of: datetime | None = None) -> SupplierRfqPackageSnapshot | None:
        values = self._packages.get(package_id, [])
        moment = self._as_of(as_of)
        if moment is not None:
            values = [item for item in values if item.created_at <= moment]
        return max(values, key=lambda item: (item.version, item.created_at), default=None)

    def packages_for_campaign(self, campaign_id: str, *, as_of: datetime | None = None) -> tuple[SupplierRfqPackageSnapshot, ...]:
        moment = self._as_of(as_of)
        values = [item for packages in self._packages.values() for item in packages if item.campaign_id == campaign_id]
        if moment is not None:
            values = [item for item in values if item.created_at <= moment]
        return tuple(sorted(values, key=lambda item: (item.supplier_id, item.version, item.created_at)))

    def save_decision(self, decision: InternalDryRunDecision) -> InternalDryRunDecision:
        existing = next((item for item in self._decisions if item.decision_id == decision.decision_id), None)
        if existing is not None:
            if existing != decision:
                raise ValueError("decision is immutable")
            return existing
        self._decisions.append(decision)
        return decision

    def decision_for(self, package_id: str, *, as_of: datetime | None = None) -> InternalDryRunDecision | None:
        moment = self._as_of(as_of)
        values = [item for item in self._decisions if item.package_id == package_id]
        if moment is not None:
            values = [item for item in values if item.decided_at <= moment]
        return max(values, key=lambda item: (item.decided_at, item.decision_id), default=None)

    def get_decision(self, decision_id: str) -> InternalDryRunDecision | None:
        return next((item for item in self._decisions if item.decision_id == decision_id), None)

    # Aliases used by integration callers.
    get_as_of = get_package
