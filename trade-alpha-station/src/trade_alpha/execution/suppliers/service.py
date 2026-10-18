"""Application services for versioned supplier identity and dry-run qualification."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rapidfuzz import fuzz

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.contracts import stable_fingerprint
from trade_alpha.execution.specifications.models import ProductSpecVersion, SpecState

from .models import (
    AuthenticityStatus,
    ClaimReviewStatus,
    CounterpartyScreeningRun,
    DocumentReviewStatus,
    DuplicateCandidate,
    ExpiryStatus,
    IdentityDecisionAction,
    MachineDisposition,
    QualificationDecisionAction,
    QualificationDisposition,
    QualificationPolicyVersion,
    QualificationPurpose,
    RequirementResult,
    RequirementStatus,
    ScreeningDisposition,
    ScreeningRunStatus,
    SiteType,
    SupplierCapabilityClaim,
    SupplierDocumentClaim,
    SupplierIdentityDecision,
    SupplierLifecycleStatus,
    SupplierProfile,
    SupplierProfileVersion,
    SupplierQualificationAssessment,
    SupplierQualificationDecision,
    SupplierQualificationSnapshot,
    qualification_snapshot_content_fingerprint,
)
from .ports import (
    BusinessIdentifierValidationPort,
    CounterpartyScreeningPort,
    SupplierQualificationRepositoryPort,
    SupplierRepositoryPort,
)
from .repository import InMemorySupplierQualificationRepository, InMemorySupplierRepository


class SupplierIdentityService:
    """Capture versions and propose, but never automatically apply, identity links."""

    def __init__(
        self,
        repository: SupplierRepositoryPort | None = None,
        identifier_validator: BusinessIdentifierValidationPort | None = None,
    ) -> None:
        self.repository = repository or InMemorySupplierRepository()
        self.identifier_validator = identifier_validator
        self._identity_decisions: dict[str, SupplierIdentityDecision] = {}

    def capture_profile(
        self,
        profile: SupplierProfile,
        profile_version: SupplierProfileVersion,
        *,
        idempotency_key: str | None = None,
    ) -> SupplierProfileVersion:
        """Persist a profile version, optionally validating identifier syntax only."""

        if profile.supplier_id != profile_version.supplier_id:
            raise ValueError("profile and profile_version supplier_id must match")
        normalized_profile_payload = profile_version.model_dump(
            mode="json", exclude={"content_fingerprint"}
        )
        normalized_version = profile_version.model_copy(
            update={"content_fingerprint": stable_fingerprint(normalized_profile_payload)}
        )
        input_fingerprint = stable_fingerprint(
            {"profile": profile, "profile_version": normalized_version}
        )
        if idempotency_key is not None:
            normalized_key = idempotency_key.strip()
            if not normalized_key:
                raise ValueError("idempotency_key must not be blank")
            prior_idempotency = self.repository.capture_idempotency(normalized_key)
            if prior_idempotency is not None:
                prior_fingerprint, prior_version_id = prior_idempotency
                if prior_fingerprint != input_fingerprint:
                    raise ValueError(
                        "idempotency key is already associated with different supplier input"
                    )
                prior_version = self.repository.get_profile_version(prior_version_id)
                if prior_version is None:
                    raise RuntimeError(
                        "idempotency record refers to missing supplier profile version"
                    )
                return prior_version
        validated = tuple(
            self.identifier_validator.validate(item) if self.identifier_validator else item
            for item in normalized_version.identifiers
        )
        version = normalized_version.model_copy(update={"identifiers": validated})
        prior = self.repository.get_profile(profile.supplier_id)
        if prior is None:
            self.repository.save_profile(profile)
        elif prior.created_at != profile.created_at:
            raise ValueError("SupplierProfile conflicts with existing supplier_id")
        saved = self.repository.save_profile_version(version)
        if saved is version:
            current = (prior or profile).model_copy(
                update={"current_version_id": saved.supplier_profile_version_id}
            )
            self.repository.save_profile(current)
        if idempotency_key is not None:
            self.repository.save_capture_idempotency(
                idempotency_key.strip(), input_fingerprint, saved.supplier_profile_version_id
            )
        return saved

    def propose_duplicate_links(
        self, supplier_id: str, *, as_of: datetime, minimum_score: float = 80
    ) -> tuple[DuplicateCandidate, ...]:
        profile = self.repository.profile_as_of(supplier_id, as_of)
        if profile is None:
            raise KeyError(f"unknown supplier or no profile at as_of: {supplier_id}")
        results: list[DuplicateCandidate] = []
        for other in self._all_other_current(supplier_id, as_of):
            name_score = fuzz.token_sort_ratio(
                profile.legal_name_normalized, other.legal_name_normalized
            )
            fields = ["legal_name"]
            common_identifiers = {
                (item.country_or_region, item.identifier_type, item.compact_value)
                for item in profile.identifiers
            } & {
                (item.country_or_region, item.identifier_type, item.compact_value)
                for item in other.identifiers
            }
            if common_identifiers:
                name_score = max(name_score, 100.0)
                fields.append("business_identifier")
            if name_score >= minimum_score:
                results.append(
                    self.repository.save_duplicate_candidate(
                        DuplicateCandidate(
                            candidate_id=_id(
                                "duplicate", supplier_id, other.supplier_id, str(name_score)
                            ),
                            source_supplier_id=supplier_id,
                            target_supplier_id=other.supplier_id,
                            score=name_score,
                            matching_fields=tuple(fields),
                            proposed_at=require_utc(as_of),
                        )
                    )
                )
        return tuple(sorted(results, key=lambda item: (-item.score, item.target_supplier_id)))

    def record_identity_decision(
        self, decision: SupplierIdentityDecision
    ) -> SupplierIdentityDecision:
        old = self._identity_decisions.get(decision.decision_id)
        if old is not None:
            if old != decision:
                raise ValueError(
                    "SupplierIdentityDecision is immutable and conflicts with existing id"
                )
            return old
        if decision.action is IdentityDecisionAction.MERGE:
            source = self._require_profile(decision.source_supplier_id)
            target = self._require_profile(decision.target_supplier_id or "")
            candidate = self.repository.get_duplicate_candidate(decision.candidate_id or "")
            if (
                candidate is None
                or candidate.source_supplier_id != source.supplier_id
                or candidate.target_supplier_id != target.supplier_id
            ):
                raise ValueError("merge requires a matching generated duplicate candidate")
            if target.lifecycle_status is SupplierLifecycleStatus.MERGED:
                raise ValueError("cannot merge into a supplier already merged elsewhere")
            self._identity_decisions[decision.decision_id] = decision
            self.repository.save_profile(
                source.model_copy(
                    update={
                        "lifecycle_status": SupplierLifecycleStatus.MERGED,
                        "merged_into_supplier_id": target.supplier_id,
                    }
                )
            )
        self._identity_decisions.setdefault(decision.decision_id, decision)
        return self._identity_decisions[decision.decision_id]

    def _all_other_current(
        self, supplier_id: str, as_of: datetime
    ) -> tuple[SupplierProfileVersion, ...]:
        return tuple(
            item
            for other_id in self.repository.supplier_ids()
            if other_id != supplier_id
            if (item := self.repository.profile_as_of(other_id, as_of)) is not None
        )

    def _require_profile(self, supplier_id: str) -> SupplierProfile:
        profile = self.repository.get_profile(supplier_id)
        if profile is None:
            raise KeyError(f"unknown supplier {supplier_id}")
        return profile


class SupplierCapabilityService:
    def __init__(self, repository: SupplierRepositoryPort | None = None) -> None:
        self.repository = repository or InMemorySupplierRepository()

    def record_claims(
        self,
        capabilities: tuple[SupplierCapabilityClaim, ...] = (),
        documents: tuple[SupplierDocumentClaim, ...] = (),
    ) -> tuple[SupplierCapabilityClaim, ...]:
        for claim in capabilities:
            if self.repository.get_profile(claim.supplier_id) is None:
                raise KeyError(f"unknown supplier {claim.supplier_id}")
            site_ids = {
                site.site_id
                for version in self.repository.profile_versions_for(claim.supplier_id)
                for site in version.sites
            }
            if claim.site_id not in site_ids:
                raise ValueError("capability site_id must belong to a supplier profile site")
            self.repository.save_capability(claim)
        for document in documents:
            if self.repository.get_profile(document.supplier_id) is None:
                raise KeyError(f"unknown supplier {document.supplier_id}")
            self.repository.save_document(document)
        return capabilities


class SupplierScreeningService:
    def __init__(
        self,
        repository: SupplierRepositoryPort | None = None,
        screening: CounterpartyScreeningPort | None = None,
    ) -> None:
        self.repository = repository or InMemorySupplierRepository()
        self.screening = screening

    def run(self, supplier_profile_version_id: str, *, as_of: datetime) -> CounterpartyScreeningRun:
        if self.screening is None:
            raise RuntimeError("CounterpartyScreeningPort is required to run screening")
        profile = self.repository.get_profile_version(supplier_profile_version_id)
        if profile is None:
            raise KeyError(f"unknown supplier profile version {supplier_profile_version_id}")
        result = self.screening.screen(profile, as_of=require_utc(as_of))
        if result.supplier_profile_version_id != profile.supplier_profile_version_id:
            raise ValueError("screening result must refer to the screened profile version")
        return self.repository.save_screening(result)


class SupplierQualificationService:
    """Evaluate a frozen EP-03 version, then turn explicit human decisions into snapshots."""

    def __init__(
        self,
        suppliers: SupplierRepositoryPort | None = None,
        qualifications: SupplierQualificationRepositoryPort | None = None,
    ) -> None:
        self.suppliers = suppliers or InMemorySupplierRepository()
        self.qualifications = qualifications or InMemorySupplierQualificationRepository()

    def assess(
        self,
        supplier_profile_version_id: str,
        product_spec_version: ProductSpecVersion,
        policy: QualificationPolicyVersion,
        *,
        as_of: datetime,
    ) -> SupplierQualificationAssessment:
        moment = require_utc(as_of)
        if product_spec_version.state is not SpecState.FROZEN_FOR_SUPPLIER_RFQ:
            raise ValueError("supplier qualification requires a frozen ProductSpecVersion")
        if policy.purpose is not QualificationPurpose.DRY_RUN:
            raise ValueError("P0 only supports dry_run qualification policies")
        if policy.product_family_id != product_spec_version.profile_ref.name:
            raise ValueError("qualification policy product family must match ProductSpecVersion")
        profile = self._profile_version(supplier_profile_version_id)
        if (
            profile.system_time > moment
            or profile.valid_from > moment
            or (profile.valid_until is not None and profile.valid_until < moment)
        ):
            raise ValueError("supplier profile version is not valid and known at as_of")
        if not (
            policy.effective_from <= moment
            and (policy.effective_until is None or moment <= policy.effective_until)
        ):
            raise ValueError("qualification policy is not effective at as_of")
        results = self._evaluate(profile, product_spec_version, policy, moment)
        missing = tuple(
            item.requirement_id for item in results if item.status is RequirementStatus.MISSING
        )
        unassessed = tuple(
            item.requirement_id for item in results if item.status is RequirementStatus.NOT_ASSESSED
        )
        warnings = tuple(
            item.reason_code
            for item in results
            if item.status in {RequirementStatus.WARNING, RequirementStatus.NOT_ASSESSED}
        )
        blocks = tuple(
            item.reason_code for item in results if item.status is RequirementStatus.BLOCKED
        )
        machine = (
            MachineDisposition.BLOCKED
            if blocks
            else MachineDisposition.EVIDENCE_INCOMPLETE
            if missing or unassessed
            else MachineDisposition.REVIEW_REQUIRED
        )
        input_value: dict[str, Any] = {
            "profile_version": profile,
            "spec_version_id": product_spec_version.spec_version_id,
            "policy": policy,
            "as_of": moment,
            "capabilities": self.suppliers.capabilities_for(profile.supplier_id, moment),
            "documents": self.suppliers.documents_for(profile.supplier_id, moment),
            "screenings": self.suppliers.screenings_for(
                profile.supplier_profile_version_id, moment
            ),
        }
        input_fingerprint = stable_fingerprint(input_value)
        assessment_id = _id("assessment", input_fingerprint)
        draft: dict[str, Any] = {
            "assessment_id": assessment_id,
            "supplier_id": profile.supplier_id,
            "supplier_profile_version_id": profile.supplier_profile_version_id,
            "product_family_id": policy.product_family_id,
            "product_spec_version_id": product_spec_version.spec_version_id,
            "qualification_policy_version_id": f"{policy.policy_id}@{policy.policy_version}",
            "policy_config_fingerprint": _policy_config_fingerprint(policy),
            "as_of": moment,
            "requirement_results": results,
            "missing_evidence": missing,
            "warnings": warnings,
            "hard_blocks": blocks,
            "machine_disposition": machine,
            "valid_until": self._assessment_valid_until(profile, policy, moment),
            "input_fingerprint": input_fingerprint,
            "result_fingerprint": "pending",
            "created_at": moment,
        }
        result_fingerprint = stable_fingerprint(draft)
        return self.qualifications.save_assessment(
            SupplierQualificationAssessment(**(draft | {"result_fingerprint": result_fingerprint}))
        )

    def decide(
        self, decision: SupplierQualificationDecision, *, policy: QualificationPolicyVersion
    ) -> SupplierQualificationDecision:
        assessment = self.qualifications.get_assessment(decision.assessment_id)
        if assessment is None:
            raise KeyError(f"unknown qualification assessment {decision.assessment_id}")
        self._require_assessment_policy(assessment, policy)
        if decision.actor_role not in policy.allowed_human_roles:
            raise PermissionError("actor role is not allowed by qualification policy")
        return self.qualifications.save_decision(decision)

    def freeze_snapshot(
        self,
        assessment_id: str,
        policy: QualificationPolicyVersion,
        *,
        as_of: datetime,
    ) -> SupplierQualificationSnapshot:
        moment = require_utc(as_of)
        assessment = self.qualifications.get_assessment(assessment_id)
        if assessment is None:
            raise KeyError(f"unknown qualification assessment {assessment_id}")
        self._require_assessment_policy(assessment, policy)
        decisions = self.qualifications.decisions_for(assessment_id, moment)
        decision = decisions[-1] if decisions else None
        if decision is not None and decision.actor_role not in policy.allowed_human_roles:
            raise PermissionError("actor role is not allowed by qualification policy")
        disposition, conditions, valid_until = self._snapshot_status(assessment, decision, moment)
        runs = self.suppliers.screenings_for(assessment.supplier_profile_version_id, moment)
        evidence = tuple(
            sorted({ref for item in assessment.requirement_results for ref in item.evidence_refs})
        )
        snapshot_draft: dict[str, Any] = {
            "qualification_snapshot_id": _id(
                "qualification-snapshot",
                assessment.assessment_id,
                decision.decision_id if decision else "none",
                moment.isoformat(),
            ),
            "supplier_id": assessment.supplier_id,
            "supplier_profile_version_id": assessment.supplier_profile_version_id,
            "product_family_id": assessment.product_family_id,
            "product_spec_version_id": assessment.product_spec_version_id,
            "assessment_id": assessment.assessment_id,
            "effective_decision_id": decision.decision_id if decision else None,
            "disposition": disposition,
            "conditions": conditions,
            "screening_run_refs": tuple(item.screening_run_id for item in runs),
            "evidence_manifest": evidence,
            "as_of": moment,
            "valid_until": valid_until,
            "policy_version": assessment.qualification_policy_version_id,
            "content_fingerprint": "pending",
        }
        pending = SupplierQualificationSnapshot(**snapshot_draft)
        snapshot = pending.model_copy(
            update={
                "content_fingerprint": qualification_snapshot_content_fingerprint(pending)
            }
        )
        return self.qualifications.save_snapshot(snapshot)

    def get_as_of(
        self, supplier_id: str, product_spec_version_id: str, *, as_of: datetime
    ) -> SupplierQualificationSnapshot | None:
        return self.qualifications.snapshot_as_of(supplier_id, product_spec_version_id, as_of)

    def _evaluate(
        self,
        profile: SupplierProfileVersion,
        spec: ProductSpecVersion,
        policy: QualificationPolicyVersion,
        as_of: datetime,
    ) -> tuple[RequirementResult, ...]:
        capabilities = self.suppliers.capabilities_for(profile.supplier_id, as_of)
        documents = self.suppliers.documents_for(profile.supplier_id, as_of)
        screenings = self.suppliers.screenings_for(profile.supplier_profile_version_id, as_of)
        result: list[RequirementResult] = []
        result.append(
            RequirementResult(
                requirement_id="identity",
                status=RequirementStatus.SATISFIED,
                reason_code="IDENTITY_EVIDENCE_PRESENT",
                evidence_refs=tuple(ref.evidence.artifact_id for ref in profile.provenance_refs),
            )
        )
        current_sites = tuple(
            site
            for site in profile.sites
            if site.valid_from <= as_of and (site.valid_until is None or site.valid_until >= as_of)
        )
        site_type = {site.site_type for site in current_sites}
        if SiteType.FACTORY in site_type:
            result.append(
                RequirementResult(
                    requirement_id="production_site",
                    status=RequirementStatus.SATISFIED,
                    reason_code="FACTORY_SITE_CLAIMED",
                    evidence_refs=tuple(
                        site.site_id for site in current_sites if site.site_type is SiteType.FACTORY
                    ),
                )
            )
        elif SiteType.UNKNOWN in site_type:
            result.append(
                RequirementResult(
                    requirement_id="production_site",
                    status=RequirementStatus.WARNING,
                    reason_code="FACTORY_SITE_UNKNOWN",
                    evidence_refs=tuple(site.site_id for site in current_sites),
                )
            )
        else:
            result.append(
                RequirementResult(
                    requirement_id="production_site",
                    status=RequirementStatus.MISSING,
                    reason_code="FACTORY_SITE_MISSING",
                )
            )
        hard_blocks = tuple(policy.hard_blocks)
        for predicate in policy.required_capability_predicates:
            matching = [
                item
                for item in capabilities
                if item.product_family_id == predicate.product_family_id
                and item.capability_type in predicate.capability_types
                and item.review_status is ClaimReviewStatus.REVIEWED
            ]
            requested_tokens = {token.lower() for token in predicate.required_scope_tokens}
            if requested_tokens:
                matching = [
                    item
                    for item in matching
                    if requested_tokens.issubset(_tokens(item.claim_value) | set(item.spec_scope))
                ]
            if not matching:
                result.append(
                    RequirementResult(
                        requirement_id=predicate.requirement_id,
                        status=RequirementStatus.MISSING,
                        reason_code="CAPABILITY_MISSING",
                    )
                )
            else:
                result.append(
                    RequirementResult(
                        requirement_id=predicate.requirement_id,
                        status=RequirementStatus.SATISFIED,
                        reason_code="CAPABILITY_DECLARED",
                        evidence_refs=tuple(item.capability_claim_id for item in matching),
                    )
                )
        # Configuration-defined exclusions are never counterbalanced by unrelated positive evidence.
        for claim in capabilities:
            if claim.review_status is not ClaimReviewStatus.REVIEWED:
                continue
            if any(
                _contains_term(claim.claim_value, hard_block)
                or any(_contains_term(scope, hard_block) for scope in claim.spec_scope)
                or _contains_term(spec.model_dump(mode="json"), hard_block)
                for hard_block in hard_blocks
            ):
                result.append(
                    RequirementResult(
                        requirement_id="scope_exclusion",
                        status=RequirementStatus.BLOCKED,
                        reason_code="HARD_SCOPE_CONFLICT",
                        evidence_refs=(claim.capability_claim_id,),
                    )
                )
                break
        for document_type in policy.required_evidence_types:
            matches = [item for item in documents if item.document_type == document_type]
            current = [
                item
                for item in matches
                if item.human_review_status is DocumentReviewStatus.REVIEWED
                and item.authenticity_status is AuthenticityStatus.VERIFIED_BY_HUMAN
                and (item.valid_until is None or item.valid_until >= as_of)
                and item.expiry_status is not ExpiryStatus.EXPIRED
            ]
            result.append(
                RequirementResult(
                    requirement_id=f"evidence:{document_type}",
                    status=RequirementStatus.SATISFIED if current else RequirementStatus.WARNING,
                    reason_code="DOCUMENT_CURRENT" if current else "DOCUMENT_MISSING_OR_EXPIRED",
                    evidence_refs=tuple(item.document_claim_id for item in current),
                )
            )
        screening = screenings[-1] if screenings else None
        if screening is None:
            result.append(
                RequirementResult(
                    requirement_id="screening",
                    status=RequirementStatus.NOT_ASSESSED,
                    reason_code="SCREENING_NOT_ASSESSED",
                )
            )
        elif (
            screening.run_status is not ScreeningRunStatus.COMPLETED
            or as_of - screening.completed_at > policy.maximum_screening_age
        ):
            result.append(
                RequirementResult(
                    requirement_id="screening",
                    status=RequirementStatus.NOT_ASSESSED,
                    reason_code="SCREENING_STALE_OR_ERROR",
                    evidence_refs=(screening.screening_run_id,),
                )
            )
        elif screening.disposition in {
            ScreeningDisposition.CANDIDATE_FOUND,
            ScreeningDisposition.REVIEW_REQUIRED,
        }:
            result.append(
                RequirementResult(
                    requirement_id="screening",
                    status=RequirementStatus.WARNING,
                    reason_code="SCREENING_CANDIDATE_REVIEW_REQUIRED",
                    evidence_refs=(screening.screening_run_id,),
                )
            )
        else:
            result.append(
                RequirementResult(
                    requirement_id="screening",
                    status=RequirementStatus.SATISFIED,
                    reason_code="SCREENING_NO_CANDIDATE",
                    evidence_refs=(screening.screening_run_id,),
                )
            )
        return tuple(result)

    def _snapshot_status(
        self,
        assessment: SupplierQualificationAssessment,
        decision: SupplierQualificationDecision | None,
        as_of: datetime,
    ) -> tuple[QualificationDisposition, tuple[str, ...], datetime | None]:
        if assessment.valid_until < as_of:
            return QualificationDisposition.EXPIRED, (), assessment.valid_until
        if assessment.machine_disposition is MachineDisposition.BLOCKED:
            return QualificationDisposition.BLOCKED, (), assessment.valid_until
        if assessment.machine_disposition is MachineDisposition.EVIDENCE_INCOMPLETE:
            return QualificationDisposition.EVIDENCE_INCOMPLETE, (), assessment.valid_until
        if decision is None:
            return QualificationDisposition.REVIEW_REQUIRED, (), assessment.valid_until
        if decision.expires_at is not None and decision.expires_at < as_of:
            return QualificationDisposition.EXPIRED, (), decision.expires_at
        if decision.decision is QualificationDecisionAction.BLOCK:
            return (
                QualificationDisposition.BLOCKED,
                (),
                _earlier(decision.expires_at, assessment.valid_until),
            )
        if decision.decision is QualificationDecisionAction.REQUEST_EVIDENCE:
            return (
                QualificationDisposition.REVIEW_REQUIRED,
                (),
                _earlier(decision.expires_at, assessment.valid_until),
            )
        if decision.decision is QualificationDecisionAction.APPROVE_WITH_CONDITIONS:
            return (
                QualificationDisposition.DRY_RUN_ELIGIBLE_WITH_CONDITIONS,
                decision.condition_codes,
                _earlier(decision.expires_at, assessment.valid_until),
            )
        return (
            QualificationDisposition.DRY_RUN_ELIGIBLE,
            (),
            _earlier(decision.expires_at, assessment.valid_until),
        )

    def _assessment_valid_until(
        self, profile: SupplierProfileVersion, policy: QualificationPolicyVersion, as_of: datetime
    ) -> datetime:
        """Bound qualification by the earliest fact whose freshness can expire."""

        expiries: list[datetime] = []
        if profile.valid_until is not None:
            expiries.append(profile.valid_until)
        if policy.effective_until is not None:
            expiries.append(policy.effective_until)
        capabilities = self.suppliers.capabilities_for(profile.supplier_id, as_of)
        expiries.extend(
            item.valid_until
            for item in capabilities
            if item.review_status is ClaimReviewStatus.REVIEWED and item.valid_until is not None
        )
        expiries.extend(
            item.valid_until
            for item in self.suppliers.documents_for(profile.supplier_id, as_of)
            if item.valid_until is not None
        )
        screenings = self.suppliers.screenings_for(profile.supplier_profile_version_id, as_of)
        if screenings:
            expiries.append(screenings[-1].completed_at + policy.maximum_screening_age)
        else:
            # An assessment with no screening may still be reviewed for dry-run conditions, but
            # cannot retain eligibility beyond the moment it was assessed.
            expiries.append(as_of)
        return min(expiries)

    def _profile_version(self, profile_version_id: str) -> SupplierProfileVersion:
        profile = self.suppliers.get_profile_version(profile_version_id)
        if profile is None:
            raise KeyError(f"unknown supplier profile version {profile_version_id}")
        return profile

    @staticmethod
    def _require_assessment_policy(
        assessment: SupplierQualificationAssessment, policy: QualificationPolicyVersion
    ) -> None:
        if (
            assessment.qualification_policy_version_id
            != f"{policy.policy_id}@{policy.policy_version}"
        ):
            raise ValueError("policy must exactly match the assessment policy version")
        if assessment.policy_config_fingerprint != _policy_config_fingerprint(policy):
            raise ValueError("policy configuration does not match the assessed canonical policy")


def _id(prefix: str, *parts: str) -> str:
    """Give deterministic P0 records a stable identifier derived from their immutable inputs."""

    return f"{prefix}:{stable_fingerprint({'parts': parts})[7:31]}"


def _tokens(value: Any) -> set[str]:
    """Conservatively derive normalized tokens for policy conflict checks."""

    if isinstance(value, str):
        return {item.lower() for item in value.replace("_", " ").replace("-", " ").split()}
    if isinstance(value, dict):
        return set().union(*(_tokens(item) for item in value.values())) if value else set()
    if isinstance(value, (list, tuple, set)):
        return set().union(*(_tokens(item) for item in value)) if value else set()
    return {str(value).lower()}


def _contains_term(value: Any, term: str) -> bool:
    """Match an exclusion phrase, avoiding broad token collisions such as ``dust``."""

    normalized_term = " ".join(term.lower().replace("_", " ").replace("-", " ").split())
    if isinstance(value, str):
        normalized_value = " ".join(value.lower().replace("_", " ").replace("-", " ").split())
        return normalized_term in normalized_value
    if isinstance(value, dict):
        return any(_contains_term(item, term) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_term(item, term) for item in value)
    return normalized_term in str(value).lower()


def _earlier(left: datetime | None, right: datetime) -> datetime:
    return right if left is None else min(left, right)


def _policy_config_fingerprint(policy: QualificationPolicyVersion) -> str:
    """Fingerprint every policy input rather than trusting a caller-provided label."""

    return stable_fingerprint(policy.model_dump(mode="json", exclude={"config_fingerprint"}))
