"""Append-only in-memory repositories for EP-04 supplier facts and decisions."""

from __future__ import annotations

from datetime import datetime

from trade_alpha.contracts.common import require_utc

from .models import (
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


class InMemorySupplierRepository:
    """A deliberately small append-only store suitable for deterministic P0 exercises."""

    def __init__(self) -> None:
        self._profiles: dict[str, SupplierProfile] = {}
        self._profile_versions: dict[str, list[SupplierProfileVersion]] = {}
        self._capture_idempotency: dict[str, tuple[str, str]] = {}
        self._duplicate_candidates: dict[str, DuplicateCandidate] = {}
        self._capabilities: dict[str, SupplierCapabilityClaim] = {}
        self._documents: dict[str, SupplierDocumentClaim] = {}
        self._screenings: dict[str, CounterpartyScreeningRun] = {}

    def save_profile(self, profile: SupplierProfile) -> SupplierProfile:
        prior = self._profiles.get(profile.supplier_id)
        # The aggregate pointer/status may only advance after a related immutable fact.
        if prior is not None and prior != profile and prior.created_at != profile.created_at:
            raise ValueError("SupplierProfile is immutable and conflicts with existing supplier_id")
        self._profiles[profile.supplier_id] = profile
        return profile

    def get_profile(self, supplier_id: str) -> SupplierProfile | None:
        return self._profiles.get(supplier_id)

    def save_profile_version(self, version: SupplierProfileVersion) -> SupplierProfileVersion:
        versions = self._profile_versions.setdefault(version.supplier_id, [])
        existing = next(
            (
                item
                for item in versions
                if item.supplier_profile_version_id == version.supplier_profile_version_id
            ),
            None,
        )
        if existing is not None:
            if existing != version:
                raise ValueError(
                    "SupplierProfileVersion is immutable and conflicts with existing id"
                )
            return existing
        same_content = next(
            (item for item in versions if item.content_fingerprint == version.content_fingerprint),
            None,
        )
        if same_content is not None:
            return same_content
        same_sequence = next((item for item in versions if item.version == version.version), None)
        if same_sequence is not None and same_sequence != version:
            raise ValueError("SupplierProfileVersion sequence is immutable")
        versions.append(version)
        return version

    def get_profile_version(self, profile_version_id: str) -> SupplierProfileVersion | None:
        return next(
            (
                item
                for versions in self._profile_versions.values()
                for item in versions
                if item.supplier_profile_version_id == profile_version_id
            ),
            None,
        )

    def profile_versions_for(self, supplier_id: str) -> tuple[SupplierProfileVersion, ...]:
        return tuple(
            sorted(
                self._profile_versions.get(supplier_id, ()),
                key=lambda item: (item.version, item.system_time, item.supplier_profile_version_id),
            )
        )

    def supplier_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def profile_as_of(self, supplier_id: str, as_of: datetime) -> SupplierProfileVersion | None:
        moment = require_utc(as_of)
        valid = [
            item
            for item in self.profile_versions_for(supplier_id)
            if item.system_time <= moment
            and item.valid_from <= moment
            and (item.valid_until is None or item.valid_until >= moment)
        ]
        return max(valid, key=lambda item: (item.version, item.system_time), default=None)

    def capture_idempotency(self, key: str) -> tuple[str, str] | None:
        return self._capture_idempotency.get(key)

    def save_capture_idempotency(
        self, key: str, input_fingerprint: str, profile_version_id: str
    ) -> None:
        prior = self._capture_idempotency.get(key)
        value = (input_fingerprint, profile_version_id)
        if prior is not None and prior != value:
            raise ValueError("idempotency key is already associated with different supplier input")
        self._capture_idempotency.setdefault(key, value)

    def save_duplicate_candidate(self, candidate: DuplicateCandidate) -> DuplicateCandidate:
        return self._save_immutable(
            self._duplicate_candidates, candidate.candidate_id, candidate, "Duplicate candidate"
        )

    def get_duplicate_candidate(self, candidate_id: str) -> DuplicateCandidate | None:
        return self._duplicate_candidates.get(candidate_id)

    def save_capability(self, claim: SupplierCapabilityClaim) -> SupplierCapabilityClaim:
        return self._save_immutable(
            self._capabilities, claim.capability_claim_id, claim, "Capability"
        )

    def capabilities_for(
        self, supplier_id: str, as_of: datetime
    ) -> tuple[SupplierCapabilityClaim, ...]:
        moment = require_utc(as_of)
        return tuple(
            sorted(
                (
                    item
                    for item in self._capabilities.values()
                    if item.supplier_id == supplier_id
                    and item.recorded_at <= moment
                    and item.valid_from <= moment
                    and (item.valid_until is None or item.valid_until >= moment)
                ),
                key=lambda item: item.capability_claim_id,
            )
        )

    def save_document(self, claim: SupplierDocumentClaim) -> SupplierDocumentClaim:
        return self._save_immutable(self._documents, claim.document_claim_id, claim, "Document")

    def documents_for(self, supplier_id: str, as_of: datetime) -> tuple[SupplierDocumentClaim, ...]:
        moment = require_utc(as_of)
        return tuple(
            sorted(
                (
                    item
                    for item in self._documents.values()
                    if item.supplier_id == supplier_id
                    and item.recorded_at <= moment
                    and (item.issued_at is None or item.issued_at <= moment)
                ),
                key=lambda item: item.document_claim_id,
            )
        )

    def save_screening(self, run: CounterpartyScreeningRun) -> CounterpartyScreeningRun:
        return self._save_immutable(self._screenings, run.screening_run_id, run, "Screening run")

    def screenings_for(
        self, profile_version_id: str, as_of: datetime
    ) -> tuple[CounterpartyScreeningRun, ...]:
        moment = require_utc(as_of)
        return tuple(
            sorted(
                (
                    item
                    for item in self._screenings.values()
                    if item.supplier_profile_version_id == profile_version_id
                    and item.completed_at <= moment
                ),
                key=lambda item: (item.completed_at, item.screening_run_id),
            )
        )

    @staticmethod
    def _save_immutable[T](store: dict[str, T], key: str, value: T, label: str) -> T:
        prior = store.get(key)
        if prior is not None:
            if prior != value:
                raise ValueError(f"{label} is immutable and conflicts with existing id")
            return prior
        store[key] = value
        return value


class InMemorySupplierQualificationRepository:
    """Append-only assessments, human decisions, and frozen downstream snapshots."""

    def __init__(self) -> None:
        self._assessments: dict[str, SupplierQualificationAssessment] = {}
        self._decisions: dict[str, SupplierQualificationDecision] = {}
        self._snapshots: dict[str, SupplierQualificationSnapshot] = {}

    def save_assessment(
        self, assessment: SupplierQualificationAssessment
    ) -> SupplierQualificationAssessment:
        return InMemorySupplierRepository._save_immutable(
            self._assessments, assessment.assessment_id, assessment, "Qualification assessment"
        )

    def get_assessment(self, assessment_id: str) -> SupplierQualificationAssessment | None:
        return self._assessments.get(assessment_id)

    def save_decision(
        self, decision: SupplierQualificationDecision
    ) -> SupplierQualificationDecision:
        return InMemorySupplierRepository._save_immutable(
            self._decisions, decision.decision_id, decision, "Qualification decision"
        )

    def decisions_for(
        self, assessment_id: str, as_of: datetime
    ) -> tuple[SupplierQualificationDecision, ...]:
        moment = require_utc(as_of)
        return tuple(
            sorted(
                (
                    item
                    for item in self._decisions.values()
                    if item.assessment_id == assessment_id and item.decided_at <= moment
                ),
                key=lambda item: (item.decided_at, item.decision_id),
            )
        )

    def save_snapshot(
        self, snapshot: SupplierQualificationSnapshot
    ) -> SupplierQualificationSnapshot:
        return InMemorySupplierRepository._save_immutable(
            self._snapshots, snapshot.qualification_snapshot_id, snapshot, "Qualification snapshot"
        )

    def snapshots_for(self, supplier_id: str) -> tuple[SupplierQualificationSnapshot, ...]:
        return tuple(
            sorted(
                (item for item in self._snapshots.values() if item.supplier_id == supplier_id),
                key=lambda item: (item.as_of, item.qualification_snapshot_id),
            )
        )

    def snapshot_as_of(
        self, supplier_id: str, product_spec_version_id: str, as_of: datetime
    ) -> SupplierQualificationSnapshot | None:
        moment = require_utc(as_of)
        candidates = [
            item
            for item in self.snapshots_for(supplier_id)
            if item.product_spec_version_id == product_spec_version_id
            and item.as_of <= moment
            and (item.valid_until is None or item.valid_until >= moment)
        ]
        return max(candidates, key=lambda item: item.as_of, default=None)
