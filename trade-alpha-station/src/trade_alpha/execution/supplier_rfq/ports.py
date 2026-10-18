"""EP-05 ports.  Adapters are kept outside the supplier_rfq domain package."""

from __future__ import annotations

# ruff: noqa: E501
from datetime import datetime
from typing import Any, Protocol

from trade_alpha.execution.suppliers.models import SupplierQualificationSnapshot

from .models import (
    AttachmentManifestEntry,
    DeliveryResult,
    OutboundIntent,
    SupplierRfqCampaign,
    SupplierRfqPackageSnapshot,
)


class SupplierRfqRepository(Protocol):
    def save_campaign(self, campaign: SupplierRfqCampaign) -> SupplierRfqCampaign: ...
    def get_campaign(self, campaign_id: str, *, as_of: datetime | None = None) -> SupplierRfqCampaign | None: ...
    def save_package(self, package: SupplierRfqPackageSnapshot) -> SupplierRfqPackageSnapshot: ...
    def get_package(self, package_id: str, *, as_of: datetime | None = None) -> SupplierRfqPackageSnapshot | None: ...
    def packages_for_campaign(self, campaign_id: str, *, as_of: datetime | None = None) -> tuple[SupplierRfqPackageSnapshot, ...]: ...
    def save_decision(self, decision: Any) -> Any: ...
    def get_decision(self, decision_id: str) -> Any | None: ...
    def decision_for(self, package_id: str, *, as_of: datetime | None = None) -> Any | None: ...


class ProductSpecReader(Protocol):
    def get_version(self, spec_version_id: str) -> Any | None: ...


class SupplierQualificationReader(Protocol):
    def get_snapshot(
        self, qualification_snapshot_id: str
    ) -> SupplierQualificationSnapshot | None: ...

    def get_as_of(
        self, supplier_id: str, as_of: datetime
    ) -> SupplierQualificationSnapshot | None: ...


class SupplierRfqRendererPort(Protocol):
    def render_json(self, package: SupplierRfqPackageSnapshot) -> str: ...
    def render_markdown(self, package: SupplierRfqPackageSnapshot) -> str: ...


class AttachmentSanitizerPort(Protocol):
    def sanitize(self, attachments: tuple[AttachmentManifestEntry, ...]) -> tuple[AttachmentManifestEntry, ...]: ...


class OutboundPort(Protocol):
    @property
    def is_null_adapter(self) -> bool: ...

    @property
    def mode(self) -> str: ...

    def deliver(self, intent: OutboundIntent) -> DeliveryResult: ...


class AuthorizationPort(Protocol):
    def may_approve_dry_run(self, actor_id: str, actor_role: str) -> bool: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class NullAttachmentSanitizer:
    """Safe fixture sanitizer: metadata must already be marked clean."""

    def sanitize(self, attachments: tuple[AttachmentManifestEntry, ...]) -> tuple[AttachmentManifestEntry, ...]:
        if any(not item.metadata_sanitized for item in attachments):
            raise ValueError("attachment metadata has not been sanitized")
        return attachments


class FixedClock:
    def __init__(self, value: datetime) -> None:
        from trade_alpha.contracts.common import require_utc

        self._value = require_utc(value)

    def now(self) -> datetime:
        return self._value
