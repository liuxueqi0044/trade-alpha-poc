"""Dependency-inversion boundaries for EP-06."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trade_alpha.execution.contracts.models import FxSnapshot, MeasuredValue
from trade_alpha.extraction import ExtractionRun

from .models import SupplierQuoteIntakeReceipt


class CommercialEvidenceIntakePort(Protocol):
    def capture(
        self,
        *,
        supplier_id: str,
        supplier_rfq_package_id: str,
        content: bytes,
        filename: str,
        declared_mime: str | None,
        idempotency_key: str | None,
        received_at: datetime,
        quote_received_at: datetime | None,
        submitted_by: str,
    ) -> SupplierQuoteIntakeReceipt: ...


class CommercialExtractionPort(Protocol):
    """Structural seam implemented directly by M02 ``ExtractionService``."""

    def extract(
        self,
        artifact_id: str,
        data: bytes,
        *,
        declared_mime: str | None = None,
        filename: str | None = None,
        parser_profile: str | None = None,
    ) -> ExtractionRun: ...


class SupplierRfqPackageReader(Protocol):
    """Read-only EP-05 seam; EP-06 needs IDs, not the EP-05 implementation."""

    def package_exists(self, supplier_rfq_package_id: str) -> bool: ...

    def supplier_for_package(self, supplier_rfq_package_id: str) -> str | None: ...

    def product_spec_for_package(self, supplier_rfq_package_id: str) -> str | None: ...

    def profile_version_for_package(self, supplier_rfq_package_id: str) -> str | None: ...

    def rfq_line_ids_for_package(self, supplier_rfq_package_id: str) -> tuple[str, ...]: ...


class ProductSpecReader(Protocol):
    def version_exists(self, product_spec_version_id: str) -> bool: ...


class UnitConversionPort(Protocol):
    def convert(self, value: MeasuredValue, *, to_unit: str) -> MeasuredValue: ...


class FxSnapshotReader(Protocol):
    def get(self, fx_snapshot_id: str) -> FxSnapshot | None: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...
