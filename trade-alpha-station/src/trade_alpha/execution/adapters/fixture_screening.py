"""Versioned, deterministic counterparty-screening fixture adapter for P0."""

from __future__ import annotations

from datetime import datetime

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.contracts import stable_fingerprint
from trade_alpha.execution.suppliers.models import (
    CounterpartyScreeningRun,
    ScreeningCandidateMatch,
    ScreeningDisposition,
    ScreeningRunStatus,
    SupplierProfileVersion,
)


class FixtureScreeningAdapter:
    """Return recorded screening results without network access or clearance semantics."""

    def __init__(
        self,
        *,
        provider: str = "fixture-screening",
        dataset_scope: str = "fixture",
        dataset_version: str = "1.0.0",
        published_at: datetime,
        records: dict[str, tuple[ScreeningCandidateMatch, ...]] | None = None,
        status: ScreeningRunStatus = ScreeningRunStatus.COMPLETED,
    ) -> None:
        self.provider = provider
        self.dataset_scope = dataset_scope
        self.dataset_version = dataset_version
        self.published_at = require_utc(published_at)
        self.records = records or {}
        self.status = status

    def screen(
        self, profile: SupplierProfileVersion, *, as_of: datetime
    ) -> CounterpartyScreeningRun:
        completed_at = require_utc(as_of)
        matches = self.records.get(profile.supplier_profile_version_id, ())
        request = stable_fingerprint(
            {
                "profile_version": profile.supplier_profile_version_id,
                "name": profile.legal_name_normalized,
                "country": profile.country_or_region,
                "identifiers": profile.identifiers,
                "dataset_version": self.dataset_version,
            }
        )
        if self.status is not ScreeningRunStatus.COMPLETED:
            disposition = ScreeningDisposition.NOT_ASSESSED
        elif matches:
            disposition = ScreeningDisposition.CANDIDATE_FOUND
        else:
            disposition = ScreeningDisposition.NO_CANDIDATE
        return CounterpartyScreeningRun(
            screening_run_id=f"screening:{request[7:31]}",
            supplier_profile_version_id=profile.supplier_profile_version_id,
            provider=self.provider,
            dataset_scope=self.dataset_scope,
            dataset_version=self.dataset_version,
            published_at=self.published_at,
            algorithm="fixture-exact-profile-key",
            algorithm_version="1.0.0",
            request_fingerprint=request,
            candidate_matches=matches,
            run_status=self.status,
            disposition=disposition,
            completed_at=completed_at,
        )
