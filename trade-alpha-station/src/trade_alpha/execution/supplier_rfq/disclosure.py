"""Build the least-privilege supplier view for an RFQ package."""

from __future__ import annotations

# ruff: noqa: E501
from collections.abc import Iterable
from typing import Any

from trade_alpha.execution.specifications.models import FieldState, ProductSpecVersion

from .models import (
    AnswerScope,
    AttachmentManifestEntry,
    DisclosurePolicyVersion,
    OpenSpecificationQuestion,
    RequestedCommercialTerm,
    RequirementValue,
    StructuredRequest,
    SupplierRfqCampaign,
    SupplierRfqLine,
)

DEFAULT_COMMERCIAL_TERMS: tuple[RequestedCommercialTerm, ...] = (
    RequestedCommercialTerm(key="unit_price", prompt_key="commercial.unit_price", expected_value_type="money"),
    RequestedCommercialTerm(key="price_quantity_basis", prompt_key="commercial.price_quantity_basis", expected_value_type="quantity_basis"),
    RequestedCommercialTerm(key="moq", prompt_key="commercial.moq", expected_value_type="quantity", not_applicable_allowed=True),
    RequestedCommercialTerm(key="quantity_breaks", prompt_key="commercial.quantity_breaks", expected_value_type="quantity_price_table", required=False, optional=True),
    RequestedCommercialTerm(key="currency", prompt_key="commercial.currency", expected_value_type="currency", currency_required=True),
    RequestedCommercialTerm(key="incoterm", prompt_key="commercial.incoterm", expected_value_type="incoterm"),
    RequestedCommercialTerm(key="packing", prompt_key="commercial.packing", expected_value_type="inclusion_and_description", not_applicable_allowed=True),
    RequestedCommercialTerm(key="domestic_freight", prompt_key="commercial.domestic_freight", expected_value_type="money_or_included"),
    RequestedCommercialTerm(key="export_clearance", prompt_key="commercial.export_clearance", expected_value_type="money_or_included"),
    RequestedCommercialTerm(key="international_freight", prompt_key="commercial.international_freight", expected_value_type="money_or_included"),
    RequestedCommercialTerm(key="insurance", prompt_key="commercial.insurance", expected_value_type="money_or_included", not_applicable_allowed=True),
    RequestedCommercialTerm(key="tax_treatment", prompt_key="commercial.tax_treatment", expected_value_type="text"),
    RequestedCommercialTerm(key="sample_fee", prompt_key="commercial.sample_fee", expected_value_type="money_or_free", not_applicable_allowed=True),
    RequestedCommercialTerm(key="sample_lead_time", prompt_key="commercial.sample_lead_time", expected_value_type="duration", not_applicable_allowed=True),
    RequestedCommercialTerm(key="production_lead_time", prompt_key="commercial.production_lead_time", expected_value_type="duration"),
    RequestedCommercialTerm(key="quote_validity", prompt_key="commercial.quote_validity", expected_value_type="duration"),
    RequestedCommercialTerm(key="payment_terms", prompt_key="commercial.payment_terms", expected_value_type="text"),
    RequestedCommercialTerm(key="tooling_nre", prompt_key="commercial.tooling_nre", expected_value_type="money_or_free", not_applicable_allowed=True),
    RequestedCommercialTerm(key="inspection_test_document_fees", prompt_key="commercial.inspection_test_document_fees", expected_value_type="money_or_free", not_applicable_allowed=True),
    RequestedCommercialTerm(key="exclusions", prompt_key="commercial.exclusions", expected_value_type="text", required=False, optional=True),
    RequestedCommercialTerm(key="assumptions", prompt_key="commercial.assumptions", expected_value_type="text", required=False, optional=True),
    RequestedCommercialTerm(key="deviations", prompt_key="commercial.deviations", expected_value_type="text", required=False, optional=True),
)


def _field_value(field: Any) -> Any:
    """Select only the already-projected value; never infer values in disclosure."""

    value = getattr(field, "canonical_value", None)
    if value is None:
        value = getattr(field, "typed_value", None)
    if value is None:
        value = getattr(field, "display_value", None)
    return value


def open_questions_from_spec(spec: ProductSpecVersion) -> tuple[OpenSpecificationQuestion, ...]:
    questions: list[OpenSpecificationQuestion] = []
    for field in sorted(spec.fields, key=lambda item: item.field_key):
        if field.state is FieldState.OPEN_TO_SUPPLIER_PROPOSAL:
            questions.append(
                OpenSpecificationQuestion(
                    question_id=f"question:{spec.spec_version_id}:{field.field_key}",
                    source_spec_field=field.field_key,
                    prompt_key=f"spec.{field.field_key}",
                    expected_value_type="typed_value",
                    allowed_units=(),
                    required_for_quote=True,
                    answer_scope=AnswerScope.LINE,
                    reason_code="open_to_supplier_proposal",
                    provenance_ref=(field.selected_claim_id or field.field_key),
                )
            )
    return tuple(questions)


def requirements_from_spec(spec: ProductSpecVersion) -> tuple[RequirementValue, ...]:
    requirements: list[RequirementValue] = []
    for field in sorted(spec.fields, key=lambda item: item.field_key):
        if field.state is FieldState.CONFIRMED:
            value = _field_value(field)
            if value is not None:
                requirements.append(RequirementValue(field_key=field.field_key, value=value))
        elif field.state is FieldState.NOT_APPLICABLE:
            requirements.append(
                RequirementValue(field_key=field.field_key, status="not_applicable")
            )
    return tuple(requirements)


def build_supplier_view(
    campaign: SupplierRfqCampaign,
    *,
    supplier_id: str,
    spec: ProductSpecVersion,
    policy: DisclosurePolicyVersion,
    commercial_terms: tuple[RequestedCommercialTerm, ...] = DEFAULT_COMMERCIAL_TERMS,
    attachments: Iterable[AttachmentManifestEntry] = (),
) -> tuple[StructuredRequest, tuple[AttachmentManifestEntry, ...], tuple[OpenSpecificationQuestion, ...]]:
    """Project a package view while explicitly excluding buyer/internal data.

    This function accepts no buyer contact, target price, margin or competing
    supplier argument.  Consequently those values cannot accidentally leak into
    a rendered view.
    """

    if campaign.product_spec_version_id != spec.spec_version_id:
        raise ValueError("campaign must reference the exact ProductSpecVersion")
    spec_state = getattr(getattr(spec, "state", None), "value", getattr(spec, "state", None))
    if spec_state != "frozen_for_supplier_rfq":
        raise ValueError("ProductSpecVersion is not frozen_for_supplier_rfq")
    allowed = set(policy.artifact_whitelist)
    all_attachments = tuple(attachments)
    selected = tuple(item for item in all_attachments if item.artifact_id in allowed)
    if len(selected) != len(all_attachments):
        raise ValueError("attachment is not in disclosure whitelist")
    questions = open_questions_from_spec(spec)
    lines = tuple(
        SupplierRfqLine(
            line_id=f"line:{campaign.campaign_id}:{index}",
            product_spec_version_id=spec.spec_version_id,
            quantity_tier=tier,
            requested_unit=tier.unit,
            requirements=requirements_from_spec(spec),
            drawing_disclosure_refs=tuple(item.artifact_id for item in selected),
        )
        for index, tier in enumerate(campaign.requested_quantity_tiers, 1)
    )
    request = StructuredRequest(
        campaign_id=campaign.campaign_id,
        supplier_id=supplier_id,
        buyer_alias=policy.buyer_alias,
        product_spec_version_id=spec.spec_version_id,
        lines=lines,
        open_questions=questions,
        commercial_terms=tuple(sorted(commercial_terms, key=lambda item: item.key)),
        response_deadline=campaign.response_deadline,
        target_delivery_place=campaign.target_delivery_place,
        requested_incoterms=tuple(sorted(campaign.requested_incoterm_options, key=lambda item: item.code)),
        requested_currencies=tuple(sorted(set(campaign.requested_quote_currencies))),
    )
    return request, tuple(sorted(selected, key=lambda item: item.artifact_id)), questions
