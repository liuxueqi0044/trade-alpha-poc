"""Dependency-inversion ports for the EP-04 supplier services."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    BusinessIdentifierClaim,
    CounterpartyScreeningRun,
    DuplicateCandidate,
    SupplierCapabilityClaim,
    SupplierDocumentClaim,
    SupplierProfile,
    SupplierProfileVersion,
    SupplierQualificationAssessment,
    SupplierQualificationDecision,
    SupplierQualificationSnapshot,
)


class BusinessIdentifierValidationPort(Protocol):
    def validate(self, identifier: BusinessIdentifierClaim) -> BusinessIdentifierClaim: ...


class CounterpartyScreeningPort(Protocol):
    def screen(
        self, profile: SupplierProfileVersion, *, as_of: datetime
    ) -> CounterpartyScreeningRun: ...


class SupplierRepositoryPort(Protocol):
    def save_profile(self, profile: SupplierProfile) -> SupplierProfile: ...

    def get_profile(self, supplier_id: str) -> SupplierProfile | None: ...

    def save_profile_version(self, version: SupplierProfileVersion) -> SupplierProfileVersion: ...

    def get_profile_version(self, profile_version_id: str) -> SupplierProfileVersion | None: ...

    def profile_versions_for(self, supplier_id: str) -> tuple[SupplierProfileVersion, ...]: ...

    def supplier_ids(self) -> tuple[str, ...]: ...

    def profile_as_of(self, supplier_id: str, as_of: datetime) -> SupplierProfileVersion | None: ...

    def capture_idempotency(self, key: str) -> tuple[str, str] | None: ...

    def save_capture_idempotency(
        self, key: str, input_fingerprint: str, profile_version_id: str
    ) -> None: ...

    def save_duplicate_candidate(self, candidate: DuplicateCandidate) -> DuplicateCandidate: ...

    def get_duplicate_candidate(self, candidate_id: str) -> DuplicateCandidate | None: ...

    def save_capability(self, claim: SupplierCapabilityClaim) -> SupplierCapabilityClaim: ...

    def capabilities_for(
        self, supplier_id: str, as_of: datetime
    ) -> tuple[SupplierCapabilityClaim, ...]: ...

    def save_document(self, claim: SupplierDocumentClaim) -> SupplierDocumentClaim: ...

    def documents_for(
        self, supplier_id: str, as_of: datetime
    ) -> tuple[SupplierDocumentClaim, ...]: ...

    def save_screening(self, run: CounterpartyScreeningRun) -> CounterpartyScreeningRun: ...

    def screenings_for(
        self, profile_version_id: str, as_of: datetime
    ) -> tuple[CounterpartyScreeningRun, ...]: ...


class SupplierQualificationRepositoryPort(Protocol):
    def save_assessment(
        self, assessment: SupplierQualificationAssessment
    ) -> SupplierQualificationAssessment: ...

    def get_assessment(self, assessment_id: str) -> SupplierQualificationAssessment | None: ...

    def save_decision(
        self, decision: SupplierQualificationDecision
    ) -> SupplierQualificationDecision: ...

    def decisions_for(
        self, assessment_id: str, as_of: datetime
    ) -> tuple[SupplierQualificationDecision, ...]: ...

    def save_snapshot(
        self, snapshot: SupplierQualificationSnapshot
    ) -> SupplierQualificationSnapshot: ...

    def snapshots_for(self, supplier_id: str) -> tuple[SupplierQualificationSnapshot, ...]: ...

    def snapshot_as_of(
        self, supplier_id: str, product_spec_version_id: str, as_of: datetime
    ) -> SupplierQualificationSnapshot | None: ...
