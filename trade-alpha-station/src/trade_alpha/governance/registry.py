"""License registry with separate software and data rights."""

from __future__ import annotations

from dataclasses import dataclass

from .models import SourceGovernance


@dataclass(frozen=True, slots=True)
class LicenseDecision:
    allowed: bool
    reason: str


class LicenseRegistry:
    def __init__(self, records: tuple[SourceGovernance, ...] = ()) -> None:
        self._records: dict[str, SourceGovernance] = {}
        for record in records:
            self.register(record)

    def register(self, record: SourceGovernance) -> None:
        if record.source_id in self._records:
            raise ValueError(f"duplicate source governance record: {record.source_id}")
        self._records[record.source_id] = record

    def get(self, source_id: str) -> SourceGovernance:
        try:
            return self._records[source_id]
        except KeyError as error:
            raise KeyError(f"unknown source governance record: {source_id}") from error

    def source_policy(self, source_id: str) -> LicenseDecision:
        record = self.get(source_id)
        if not record.enabled:
            return LicenseDecision(False, "source_disabled")
        required = {
            "api_terms_url": record.api_terms_url,
            "api_use_status": record.api_use_status,
            "caching_policy": record.caching_policy,
            "retention_policy": record.retention_policy,
            "redistribution_status": record.redistribution_status,
            "data": record.data,
        }
        for field, value in required.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                return LicenseDecision(False, f"missing_{field}")
        if record.api_use_status not in {"allowed", "approved", "permitted"}:
            return LicenseDecision(False, "api_use_not_approved")
        if record.data is None:
            return LicenseDecision(False, "missing_data")
        data_required = {
            "data_owner": record.data.owner,
            "data_license": record.data.license_id,
            "data_commercial_use": record.data.commercial_use,
            "data_redistribution": record.data.redistribution,
        }
        for field, value in data_required.items():
            if not value:
                return LicenseDecision(False, f"missing_{field}")
        # Commercial policy deliberately reads data rights, never software SPDX.
        if record.data.commercial_use not in {"allowed", "approved", "permitted"}:
            return LicenseDecision(False, "data_commercial_use_not_approved")
        return LicenseDecision(True, "source_license_complete")

    def export_policy(self, source_id: str) -> LicenseDecision:
        record = self.get(source_id)
        source_decision = self.source_policy(source_id)
        if not source_decision.allowed:
            return source_decision
        if record.redistribution_status not in {"allowed", "approved", "permitted"}:
            return LicenseDecision(False, "source_redistribution_not_approved")
        if record.data is None or record.data.redistribution not in {
            "allowed",
            "approved",
            "permitted",
        }:
            return LicenseDecision(False, "data_redistribution_not_approved")
        return LicenseDecision(True, "source_export_rights_complete")

    def enabled_report(self) -> dict[str, LicenseDecision]:
        return {
            source_id: self.source_policy(source_id)
            for source_id, record in sorted(self._records.items())
            if record.enabled
        }

    def assert_enabled_sources_compliant(self) -> None:
        denied = {
            source_id: decision.reason
            for source_id, decision in self.enabled_report().items()
            if not decision.allowed
        }
        if denied:
            details = ",".join(f"{source_id}:{reason}" for source_id, reason in denied.items())
            raise PermissionError(f"enabled source policy blocked: {details}")
