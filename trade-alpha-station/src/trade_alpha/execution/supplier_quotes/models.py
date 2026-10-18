"""Immutable, evidence-linked supplier quotation contracts (EP-06).

The types in this module deliberately stop at comparison.  They represent what
was quoted and what cannot yet be compared; they do not express a purchasing
or commercial decision.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_alpha.contracts.common import VersionRef, require_utc, utc_now
from trade_alpha.execution.contracts.models import (
    FxSnapshot,
    Incoterm,
    MonetaryAmount,
    ProvenanceRef,
    require_decimal,
)

DEFAULT_COMMERCIAL_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_COMMERCIAL_MIME_TYPES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "message/rfc822",
    "text/plain",
    "image/png",
    "image/jpeg",
)
COMMERCIAL_EXTENSION_MIME = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".eml": "message/rfc822",
    ".msg": "message/rfc822",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class FrozenQuoteModel(BaseModel):
    """Strict frozen base class for every persisted EP-06 value."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class CandidateReviewStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class QuoteDecisionAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    ENTER_MANUAL_VALUE = "enter_manual_value"
    MARK_UNKNOWN = "mark_unknown"


class InclusionStatus(StrEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    QUOTED_SEPARATELY = "quoted_separately"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    BUYER_SUPPLIED = "buyer_supplied"


class ComponentType(StrEnum):
    PRODUCT = "product"
    PACKAGING = "packaging"
    DOMESTIC_FREIGHT = "domestic_freight"
    EXPORT_CLEARANCE = "export_clearance"
    INTERNATIONAL_FREIGHT = "international_freight"
    INSURANCE = "insurance"
    TAX = "tax"
    TOOLING_OR_NRE = "tooling_or_nre"
    INSPECTION_OR_TEST = "inspection_or_test"
    SAMPLE = "sample"


class ComparabilityStatus(StrEnum):
    COMPARABLE = "comparable"
    COMPARABLE_AFTER_NORMALIZATION = "comparable_after_normalization"
    CONDITIONALLY_COMPARABLE = "conditionally_comparable"
    NOT_COMPARABLE = "not_comparable"
    UNKNOWN = "unknown"


class ComparisonDisposition(StrEnum):
    COMPARABLE = "comparable"
    CONDITIONALLY_COMPARABLE = "conditionally_comparable"
    NOT_COMPARABLE = "not_comparable"


class IssueSeverity(StrEnum):
    HARD_BLOCK = "hard_block"
    NEEDS_INPUT = "needs_input"
    WARNING = "warning"


class QuantityBasis(FrozenQuoteModel):
    """The quantity represented by a monetary expression, never an implicit 1."""

    amount: Decimal = Field(gt=0)
    unit: str = Field(min_length=1)

    @field_validator("amount", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return require_decimal(value)

    @field_validator("unit")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("quantity basis unit must not be blank")
        return value


class PriceComponent(FrozenQuoteModel):
    """One explicit cost boundary; absent amounts must remain absent."""

    component_type: ComponentType
    inclusion_status: InclusionStatus
    amount: MonetaryAmount | None = None
    quantity_basis: QuantityBasis | None = None
    applies_to_quantity_minimum: Decimal | None = Field(default=None, ge=0)
    applies_to_quantity_maximum: Decimal | None = Field(default=None, ge=0)
    tax_treatment: InclusionStatus = InclusionStatus.UNKNOWN
    provenance_ref: ProvenanceRef

    @field_validator("applies_to_quantity_minimum", "applies_to_quantity_maximum", mode="before")
    @classmethod
    def decimal_if_present(cls, value: object | None) -> Decimal | None:
        return None if value is None else require_decimal(value)

    @model_validator(mode="after")
    def quantity_range_is_ordered(self) -> PriceComponent:
        if (
            self.applies_to_quantity_minimum is not None
            and self.applies_to_quantity_maximum is not None
            and self.applies_to_quantity_minimum > self.applies_to_quantity_maximum
        ):
            raise ValueError("price component quantity range is not ordered")
        if self.amount is not None and self.quantity_basis is None:
            raise ValueError("a priced component requires an explicit quantity basis")
        if (
            self.inclusion_status
            in {
                InclusionStatus.EXCLUDED,
                InclusionStatus.UNKNOWN,
                InclusionStatus.NOT_APPLICABLE,
                InclusionStatus.BUYER_SUPPLIED,
            }
            and self.amount is not None
        ):
            raise ValueError("an excluded, unknown, N/A, or buyer-supplied component has no amount")
        return self


class SupplierQuoteFieldCandidate(FrozenQuoteModel):
    candidate_id: str = Field(min_length=1)
    quote_id: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    raw_text: str = ""
    raw_value: str | int | bool | None = None
    proposed_typed_value: Any | None = None
    provenance_ref: ProvenanceRef
    extractor_id: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    confidence: Decimal = Field(ge=0, le=1)
    review_status: CandidateReviewStatus = CandidateReviewStatus.PENDING
    issue_codes: tuple[str, ...] = ()

    @field_validator("confidence", mode="before")
    @classmethod
    def decimal_confidence(cls, value: object) -> Decimal:
        return require_decimal(value)


class SupplierQuoteLineCandidate(FrozenQuoteModel):
    candidate_line_id: str = Field(min_length=1)
    quote_id: str = Field(min_length=1)
    supplier_line_ref: str = Field(min_length=1)
    proposed_rfq_line_id: str | None = None
    mapping_confidence: Decimal = Field(ge=0, le=1)
    description_raw: str = ""
    supplier_part_number: str | None = None
    quantity_breaks: tuple[QuantityBasis, ...] = ()
    unit_price_candidate_ids: tuple[str, ...] = ()
    price_basis: QuantityBasis | None = None
    moq: QuantityBasis | None = None
    open_specification_answers: tuple[str, ...] = ()
    deviations: tuple[str, ...] = ()
    provenance_refs: tuple[ProvenanceRef, ...] = Field(min_length=1)

    @field_validator("mapping_confidence", mode="before")
    @classmethod
    def decimal_confidence(cls, value: object) -> Decimal:
        return require_decimal(value)


class SupplierQuoteDecision(FrozenQuoteModel):
    """Append-only human disposition of one proposed quote value or line mapping."""

    decision_id: str = Field(min_length=1)
    quote_id: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    candidate_id: str | None = None
    decision: QuoteDecisionAction
    typed_value: Any | None = None
    rationale: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    decided_at: datetime = Field(default_factory=utc_now)
    provenance_ref: ProvenanceRef
    supersedes_decision_id: str | None = None

    @field_validator("decided_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def audit_shape(self) -> SupplierQuoteDecision:
        if self.decision is QuoteDecisionAction.ACCEPT and not self.candidate_id:
            raise ValueError("accept requires a candidate_id")
        if self.decision is QuoteDecisionAction.ENTER_MANUAL_VALUE and self.typed_value is None:
            raise ValueError("enter_manual_value requires a typed_value")
        if self.decision is QuoteDecisionAction.MARK_UNKNOWN and self.typed_value is not None:
            raise ValueError("mark_unknown must not carry a typed_value")
        return self


class QuoteTerm(FrozenQuoteModel):
    currency: str | None = None
    incoterm: Incoterm | None = None
    quote_valid_from: datetime | None = None
    quote_valid_until: datetime | None = None
    payment_terms: str | None = None
    sample_fee: PriceComponent | None = None
    sample_lead_time: QuantityBasis | None = None
    production_lead_time: QuantityBasis | None = None
    production_lead_time_start_trigger: str | None = None
    components: tuple[PriceComponent, ...] = ()
    assumptions: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()

    @field_validator("quote_valid_from", "quote_valid_until")
    @classmethod
    def utc_if_present(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def valid_period_and_components(self) -> QuoteTerm:
        if (
            self.quote_valid_from is not None
            and self.quote_valid_until is not None
            and self.quote_valid_until < self.quote_valid_from
        ):
            raise ValueError("quote validity end precedes its start")
        types = [item.component_type for item in self.components]
        if len(types) != len(set(types)):
            raise ValueError("quote term component types must be unique")
        return self

    def component(self, component_type: ComponentType) -> PriceComponent | None:
        return next(
            (item for item in self.components if item.component_type is component_type), None
        )


class ConfirmedQuoteLine(FrozenQuoteModel):
    rfq_line_id: str = Field(min_length=1)
    supplier_line_ref: str = Field(min_length=1)
    description_raw: str = ""
    supplier_part_number: str | None = None
    quantity_basis: QuantityBasis
    product_price: PriceComponent
    moq: QuantityBasis | None = None
    deviations: tuple[str, ...] = ()
    provenance_refs: tuple[ProvenanceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def product_price_is_product(self) -> ConfirmedQuoteLine:
        if self.product_price.component_type is not ComponentType.PRODUCT:
            raise ValueError("confirmed quote line product_price must be a product component")
        if self.product_price.amount is None or self.product_price.quantity_basis is None:
            raise ValueError("confirmed quote product price requires amount and quantity basis")
        if self.product_price.inclusion_status is not InclusionStatus.INCLUDED:
            raise ValueError("confirmed quote product price must be explicitly included")
        return self


class SupplierQuoteIntakeReceipt(FrozenQuoteModel):
    intake_id: str = Field(min_length=1)
    quote_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    supplier_rfq_package_id: str = Field(min_length=1)
    idempotency_key: str | None = None
    artifact_refs: tuple[str, ...] = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    filename: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    parser_status: Literal["routed", "manual_review", "extracted", "needs_review"]
    extraction_run_refs: tuple[str, ...] = ()
    parser_versions: tuple[VersionRef, ...] = ()
    received_at: datetime
    quote_received_at: datetime | None = None
    submitted_by: str = Field(min_length=1)
    quarantine_status: str = "clear"
    input_fingerprint: str = Field(min_length=1)

    @field_validator("received_at", "quote_received_at")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def extracted_receipt_references_a_run(self) -> SupplierQuoteIntakeReceipt:
        if self.parser_status == "extracted" and not self.extraction_run_refs:
            raise ValueError("extracted commercial evidence must reference an M02 run")
        return self


def canonical_commercial_mime(filename: str, declared_mime: str | None) -> str:
    if declared_mime:
        return declared_mime.split(";", 1)[0].strip().lower()
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        return "application/pdf"
    if lowered.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if lowered.endswith((".eml", ".msg")):
        return "message/rfc822"
    if lowered.endswith((".png", ".jpg", ".jpeg")):
        return "image/png" if lowered.endswith(".png") else "image/jpeg"
    return "text/plain"


def commercial_quarantine_status(
    content: bytes,
    filename: str,
    mime_type: str,
    *,
    max_bytes: int = DEFAULT_COMMERCIAL_MAX_BYTES,
    allowed_mime_types: tuple[str, ...] = DEFAULT_COMMERCIAL_MIME_TYPES,
) -> str:
    """Pure baseline guard, recomputed on both sides of the intake port."""

    if len(content) > max_bytes:
        return "quarantined:size_limit_exceeded"
    if mime_type not in frozenset(allowed_mime_types):
        return "quarantined:mime_not_allowed"
    expected_mime = COMMERCIAL_EXTENSION_MIME.get(Path(filename).suffix.lower())
    if expected_mime is None:
        return "quarantined:extension_not_allowed"
    if mime_type != expected_mime:
        return "quarantined:extension_mime_mismatch"
    if mime_type == "application/pdf" and not content.startswith(b"%PDF"):
        return "quarantined:pdf_signature_mismatch"
    if (
        mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        and not content.startswith(b"PK")
    ):
        return "quarantined:xlsx_signature_mismatch"
    return "clear"


def commercial_input_fingerprint(
    *,
    supplier_id: str,
    supplier_rfq_package_id: str,
    content: bytes,
    filename: str,
    mime_type: str,
    idempotency_key: str | None,
    quote_received_at: datetime | None,
    submitted_by: str,
) -> str:
    """Canonical capture proof independently reproducible by adapter and service."""

    from trade_alpha.execution.contracts.fingerprints import stable_fingerprint

    return stable_fingerprint(
        {
            "supplier_id": supplier_id,
            "supplier_rfq_package_id": supplier_rfq_package_id,
            "content_sha256": f"sha256:{hashlib.sha256(content).hexdigest()}",
            "filename": filename,
            "mime_type": mime_type,
            "idempotency_key": idempotency_key,
            "quote_received_at": quote_received_at,
            "submitted_by": submitted_by,
        }
    )


class SupplierQuoteSnapshot(FrozenQuoteModel):
    supplier_quote_snapshot_id: str = Field(min_length=1)
    supplier_quote_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    supplier_id: str = Field(min_length=1)
    supplier_profile_version_id: str = Field(min_length=1)
    supplier_rfq_package_id: str = Field(min_length=1)
    product_spec_version_id: str = Field(min_length=1)
    quote_reference_raw: str | None = None
    confirmed_lines: tuple[ConfirmedQuoteLine, ...] = Field(min_length=1)
    confirmed_terms: QuoteTerm
    deviations: tuple[str, ...] = ()
    unresolved_fields: tuple[str, ...] = ()
    evidence_manifest: tuple[str, ...] = Field(min_length=1)
    effective_decision_ids: tuple[str, ...] = ()
    quote_validity_status: str = "unknown"
    received_at: datetime
    confirmed_at: datetime
    content_fingerprint: str = Field(min_length=1)
    supersedes: str | None = None

    @field_validator("received_at", "confirmed_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def distinct_rfq_lines(self) -> SupplierQuoteSnapshot:
        identifiers = [line.rfq_line_id for line in self.confirmed_lines]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("a confirmed quote snapshot maps each RFQ line at most once")
        return self

    def validity_as_of(self, as_of: datetime) -> str:
        """Evaluate time-sensitive validity without rewriting the historical snapshot."""

        moment = require_utc(as_of)
        terms = self.confirmed_terms
        if terms.quote_valid_until is not None and moment > terms.quote_valid_until:
            return "expired"
        if terms.quote_valid_from is not None and moment < terms.quote_valid_from:
            return "not_yet_valid"
        if terms.quote_valid_from is None and terms.quote_valid_until is None:
            return "unknown"
        return "valid"


class QuoteNormalizationPolicyVersion(FrozenQuoteModel):
    normalization_policy_version_id: str = Field(min_length=1)
    product_family_id: str = Field(min_length=1)
    target_currency: str = Field(min_length=3, max_length=3)
    target_measurement_unit: str = Field(min_length=1)
    target_quantity_tiers: tuple[QuantityBasis, ...] = Field(min_length=1)
    allowed_unit_conversion_dimensions: tuple[str, ...] = Field(
        default=("count",), min_length=1
    )
    rounding_display_places: int = Field(default=2, ge=0, le=12)
    required_price_components: tuple[ComponentType, ...] = (ComponentType.PRODUCT,)
    incoterm_comparison_policy: str = "same_term_and_named_place"
    allow_reciprocal_fx: bool = False
    validity_policy: str = "valid_at_as_of"
    rule_version: str = Field(min_length=1)
    config_fingerprint: str = Field(min_length=1)


class ConversionTrace(FrozenQuoteModel):
    source_unit: str
    target_unit: str
    unit_factor: Decimal
    source_currency: str
    target_currency: str
    fx_rate: Decimal | None = None
    direction: str
    rate_timestamp: datetime | None = None
    provider: str | None = None
    rule_version: str = Field(min_length=1)
    fx_snapshot_id: str | None = None

    @field_validator("unit_factor", "fx_rate", mode="before")
    @classmethod
    def decimal_values(cls, value: object | None) -> Decimal | None:
        return None if value is None else require_decimal(value)

    @field_validator("rate_timestamp")
    @classmethod
    def utc_if_present(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)


class NormalizedQuoteLine(FrozenQuoteModel):
    supplier_quote_snapshot_id: str = Field(min_length=1)
    rfq_line_id: str = Field(min_length=1)
    original_amount: MonetaryAmount | None = None
    original_quantity_basis: QuantityBasis
    unit_converted_price: MonetaryAmount | None = None
    currency_converted_price: MonetaryAmount | None = None
    conversion_trace: ConversionTrace | None = None
    quantity_tier: QuantityBasis
    component_inclusion_states: tuple[tuple[ComponentType, InclusionStatus], ...]
    spec_deviation_refs: tuple[str, ...] = ()
    dimension_comparability: tuple[tuple[str, ComparabilityStatus], ...] = ()
    calculation_value: Decimal | None = None
    display_value: str | None = None

    @field_validator("calculation_value", mode="before")
    @classmethod
    def decimal_if_present(cls, value: object | None) -> Decimal | None:
        return None if value is None else require_decimal(value)


class QuoteNormalizationRun(FrozenQuoteModel):
    normalization_run_id: str = Field(min_length=1)
    quote_snapshot_ids: tuple[str, ...] = Field(min_length=1)
    normalization_policy_version_id: str = Field(min_length=1)
    required_price_components: tuple[ComponentType, ...] = ()
    incoterm_comparison_policy: str = "same_term_and_named_place"
    validity_policy: str = "valid_at_as_of"
    fx_snapshot_id: str | None = None
    unit_adapter_version: str = Field(min_length=1)
    as_of: datetime
    normalized_lines: tuple[NormalizedQuoteLine, ...]
    issues: tuple[str, ...] = ()
    input_fingerprint: str = Field(min_length=1)
    result_fingerprint: str = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class ComparabilityIssue(FrozenQuoteModel):
    issue_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    severity: IssueSeverity
    affected_quote_snapshot_ids: tuple[str, ...] = Field(min_length=1)
    affected_line_ids: tuple[str, ...] = ()
    dimension: str = Field(min_length=1)
    message_key: str = Field(min_length=1)
    parameters: tuple[tuple[str, str], ...] = ()
    evidence_refs: tuple[ProvenanceRef, ...] = ()
    resolution_owner: str = "commercial_reviewer"
    suggested_action: str = Field(min_length=1)
    resolved_by_decision_id: str | None = None


class SupplierComparisonMatrix(FrozenQuoteModel):
    comparison_matrix_id: str = Field(min_length=1)
    quote_snapshot_ids: tuple[str, ...] = Field(min_length=1)
    target_quantity_tiers: tuple[QuantityBasis, ...] = Field(min_length=1)
    normalization_run_id: str = Field(min_length=1)
    as_of: datetime
    supplier_dimension_values: tuple[tuple[str, tuple[tuple[str, ComparabilityStatus], ...]], ...]
    issue_refs: tuple[str, ...] = ()
    unresolved_cost_components: tuple[tuple[str, ComponentType, InclusionStatus], ...] = ()
    disposition: ComparisonDisposition
    content_fingerprint: str = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class FxSnapshotReaderRecord(FrozenQuoteModel):
    """EP-06 local key for an EP-01 ``FxSnapshot``; the snapshot remains immutable."""

    fx_snapshot_id: str = Field(min_length=1)
    snapshot: FxSnapshot
