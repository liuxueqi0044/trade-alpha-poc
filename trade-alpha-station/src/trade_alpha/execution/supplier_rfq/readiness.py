"""Pure package readiness checks; readiness never constitutes human approval."""

from __future__ import annotations

# ruff: noqa: E501
from datetime import datetime

from trade_alpha.execution.specifications.models import FieldState, ProductSpecVersion, SpecState
from trade_alpha.execution.suppliers.models import (
    QualificationDisposition,
    SupplierQualificationSnapshot,
)

from .models import (
    DisclosurePolicyVersion,
    PackageReadinessResult,
    ReadinessStatus,
    StructuredRequest,
)


def qualification_is_eligible(
    snapshot: SupplierQualificationSnapshot | None, *, as_of: datetime
) -> tuple[bool, str | None]:
    if snapshot is None:
        return False, "qualification_snapshot_missing"
    if snapshot.disposition not in {
        QualificationDisposition.DRY_RUN_ELIGIBLE,
        QualificationDisposition.DRY_RUN_ELIGIBLE_WITH_CONDITIONS,
    }:
        return False, "qualification_not_dry_run_eligible"
    expiry = snapshot.valid_until
    if expiry is not None and expiry < as_of:
        return False, "qualification_snapshot_expired"
    return True, None


def evaluate_readiness(
    *,
    spec: ProductSpecVersion | None,
    qualification_snapshot: SupplierQualificationSnapshot | None,
    structured_request: StructuredRequest | None,
    policy: DisclosurePolicyVersion | None,
    as_of: datetime,
    attachment_error_codes: tuple[str, ...] = (),
) -> PackageReadinessResult:
    """Return deterministic hard blocks, required questions and warnings."""

    hard_blocks: list[str] = []
    required_questions: list[str] = []
    warnings: list[str] = []
    if spec is None:
        hard_blocks.append("spec_version_missing")
    else:
        if spec.state is not SpecState.FROZEN_FOR_SUPPLIER_RFQ:
            hard_blocks.append("spec_not_frozen_for_supplier_rfq")
        if not spec.spec_version_id:
            hard_blocks.append("spec_version_id_missing")
        for field in spec.fields:
            if field.state in {FieldState.MISSING, FieldState.UNKNOWN, FieldState.CONFLICT}:
                hard_blocks.append(f"spec_field_unresolved:{field.field_key}")
            if field.state is FieldState.OPEN_TO_SUPPLIER_PROPOSAL:
                required_questions.append(f"spec:{field.field_key}")
    eligible, reason = qualification_is_eligible(qualification_snapshot, as_of=as_of)
    if not eligible and reason:
        hard_blocks.append(reason)
    if policy is None:
        hard_blocks.append("disclosure_policy_missing")
    else:
        if not policy.buyer_alias.strip():
            hard_blocks.append("buyer_alias_missing")
        if not policy.disclose_safety_exclusions:
            hard_blocks.append("safety_exclusions_must_be_disclosed")
        if not policy.strip_drawing_metadata:
            hard_blocks.append("drawing_metadata_must_be_stripped")
    if structured_request is None:
        hard_blocks.append("structured_request_missing")
    else:
        if not structured_request.lines:
            hard_blocks.append("quantity_or_lines_missing")
        if not structured_request.target_delivery_place.strip():
            hard_blocks.append("target_delivery_place_missing")
        if not structured_request.requested_incoterms:
            hard_blocks.append("requested_incoterm_missing")
        if not structured_request.requested_currencies:
            hard_blocks.append("requested_currency_missing")
        required_questions.extend(
            f"question:{item.question_id}" for item in structured_request.open_questions if item.required_for_quote
        )
        if not structured_request.commercial_terms:
            hard_blocks.append("commercial_terms_missing")
    hard_blocks.extend(attachment_error_codes)
    # Stable de-duplication is part of the readiness fingerprint.
    hard_blocks = list(dict.fromkeys(sorted(hard_blocks)))
    required_questions = list(dict.fromkeys(sorted(required_questions)))
    warnings = list(dict.fromkeys(sorted(warnings)))
    return PackageReadinessResult(
        status=ReadinessStatus.BLOCKED if hard_blocks else ReadinessStatus.READY,
        hard_blocks=tuple(hard_blocks),
        required_questions=tuple(required_questions),
        warnings=tuple(warnings),
        checked_at=as_of,
    )
