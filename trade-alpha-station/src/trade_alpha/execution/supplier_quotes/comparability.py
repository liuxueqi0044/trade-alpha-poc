"""Rules that expose quote differences instead of hiding them in a ranking."""

from __future__ import annotations

from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from trade_alpha.execution.contracts.fingerprints import stable_fingerprint

from .models import (
    ComparabilityIssue,
    ComparabilityStatus,
    ComparisonDisposition,
    ComponentType,
    InclusionStatus,
    IssueSeverity,
    QuoteNormalizationRun,
    SupplierComparisonMatrix,
    SupplierQuoteSnapshot,
)


def _issue(
    snapshot: SupplierQuoteSnapshot, code: str, severity: IssueSeverity, dimension: str, action: str
) -> ComparabilityIssue:
    return ComparabilityIssue(
        issue_id=str(
            uuid5(
                NAMESPACE_URL,
                f"trade-alpha/ep06/issue/{snapshot.supplier_quote_snapshot_id}/{code}",
            )
        ),
        code=code,
        severity=severity,
        affected_quote_snapshot_ids=(snapshot.supplier_quote_snapshot_id,),
        dimension=dimension,
        message_key=f"supplier_quote.{code.lower()}",
        suggested_action=action,
    )


def analyze_snapshots(
    snapshots: tuple[SupplierQuoteSnapshot, ...],
    *,
    normalization: QuoteNormalizationRun,
    as_of: datetime,
) -> tuple[SupplierComparisonMatrix, tuple[ComparabilityIssue, ...]]:
    issues: list[ComparabilityIssue] = []
    dimensions: list[tuple[str, tuple[tuple[str, ComparabilityStatus], ...]]] = []
    unresolved: list[tuple[str, ComponentType, InclusionStatus]] = []
    terms = [item.confirmed_terms for item in snapshots]
    baseline_term = terms[0].incoterm if terms else None
    policy_name = normalization.incoterm_comparison_policy
    if policy_name not in {"same_term_and_named_place", "same_term"}:
        raise ValueError("unknown incoterm comparison policy; comparison is fail-closed")
    validity_policy = normalization.validity_policy
    if validity_policy not in {"valid_at_as_of", "allow_unknown_with_issue"}:
        raise ValueError("unknown quote validity policy; comparison is fail-closed")
    baseline_spec = snapshots[0].product_spec_version_id if snapshots else None
    baseline_lines = (
        {line.rfq_line_id for line in snapshots[0].confirmed_lines} if snapshots else set()
    )
    duplicate_suppliers = {
        item.supplier_id
        for item in snapshots
        if sum(other.supplier_id == item.supplier_id for other in snapshots) > 1
    }
    for snapshot in snapshots:
        term = snapshot.confirmed_terms
        values: dict[str, ComparabilityStatus] = {
            "identity": ComparabilityStatus.COMPARABLE,
            "line_mapping": ComparabilityStatus.COMPARABLE,
            "scope": ComparabilityStatus.COMPARABLE
            if not snapshot.deviations
            else ComparabilityStatus.CONDITIONALLY_COMPARABLE,
            "quantity": ComparabilityStatus.COMPARABLE,
            "currency": ComparabilityStatus.COMPARABLE,
            "validity": ComparabilityStatus.COMPARABLE,
            "delivery": ComparabilityStatus.COMPARABLE,
            "completeness": ComparabilityStatus.COMPARABLE,
            "commercial_basis": ComparabilityStatus.COMPARABLE,
        }
        if snapshot.deviations or any(line.deviations for line in snapshot.confirmed_lines):
            issues.append(
                _issue(
                    snapshot,
                    "UNSUPPORTED_SPEC_DEVIATION",
                    IssueSeverity.NEEDS_INPUT,
                    "scope",
                    "review specification deviation",
                )
            )
        if snapshot.product_spec_version_id != baseline_spec:
            values["scope"] = ComparabilityStatus.NOT_COMPARABLE
            issues.append(
                _issue(
                    snapshot,
                    "SPEC_VERSION_MISMATCH",
                    IssueSeverity.HARD_BLOCK,
                    "scope",
                    "use one frozen specification version",
                )
            )
        if {line.rfq_line_id for line in snapshot.confirmed_lines} != baseline_lines:
            values["line_mapping"] = ComparabilityStatus.NOT_COMPARABLE
            issues.append(
                _issue(
                    snapshot,
                    "RFQ_LINE_SCOPE_MISMATCH",
                    IssueSeverity.HARD_BLOCK,
                    "line_mapping",
                    "compare the same RFQ line scope",
                )
            )
        if snapshot.supplier_id in duplicate_suppliers:
            values["identity"] = ComparabilityStatus.NOT_COMPARABLE
            issues.append(
                _issue(
                    snapshot,
                    "DUPLICATE_SUPPLIER_QUOTE",
                    IssueSeverity.HARD_BLOCK,
                    "identity",
                    "keep one quote per supplier",
                )
            )
        validity = snapshot.validity_as_of(as_of)
        if validity == "expired":
            values["validity"] = ComparabilityStatus.NOT_COMPARABLE
            issues.append(
                _issue(
                    snapshot,
                    "QUOTE_EXPIRED",
                    IssueSeverity.HARD_BLOCK,
                    "validity",
                    "obtain a revised quote",
                )
            )
        elif validity == "not_yet_valid":
            values["validity"] = ComparabilityStatus.NOT_COMPARABLE
            issues.append(
                _issue(
                    snapshot,
                    "QUOTE_NOT_YET_VALID",
                    IssueSeverity.HARD_BLOCK,
                    "validity",
                    "wait for the quote validity period or obtain a current quote",
                )
            )
        elif validity == "unknown":
            strict_validity = validity_policy == "valid_at_as_of"
            values["validity"] = (
                ComparabilityStatus.NOT_COMPARABLE
                if strict_validity
                else ComparabilityStatus.UNKNOWN
            )
            issues.append(
                _issue(
                    snapshot,
                    "QUOTE_VALIDITY_UNKNOWN",
                    IssueSeverity.HARD_BLOCK if strict_validity else IssueSeverity.NEEDS_INPUT,
                    "validity",
                    "record quote validity",
                )
            )
        if term.production_lead_time is not None and not term.production_lead_time_start_trigger:
            values["delivery"] = ComparabilityStatus.UNKNOWN
            issues.append(
                _issue(
                    snapshot,
                    "LEAD_TIME_TRIGGER_UNKNOWN",
                    IssueSeverity.NEEDS_INPUT,
                    "delivery",
                    "ask for lead-time trigger",
                )
            )
        if term.incoterm is None or not term.incoterm.named_place.strip():
            values["commercial_basis"] = ComparabilityStatus.NOT_COMPARABLE
            issues.append(
                _issue(
                    snapshot,
                    "NAMED_PLACE_MISSING",
                    IssueSeverity.HARD_BLOCK,
                    "commercial_basis",
                    "request Incoterm named place",
                )
            )
        elif baseline_term is not None:
            incoterm_mismatches: list[tuple[str, str]] = []
            if term.incoterm.code != baseline_term.code:
                incoterm_mismatches.append(("INCOTERM_CODE_MISMATCH", "use the same Incoterm code"))
            if term.incoterm.rules_version != baseline_term.rules_version:
                incoterm_mismatches.append(
                    ("INCOTERM_RULES_VERSION_MISMATCH", "use the same Incoterm rules version")
                )
            if (
                policy_name == "same_term_and_named_place"
                and term.incoterm.named_place != baseline_term.named_place
            ):
                incoterm_mismatches.append(
                    ("INCOTERM_NAMED_PLACE_MISMATCH", "use the same Incoterm named place")
                )
            if incoterm_mismatches:
                values["commercial_basis"] = ComparabilityStatus.NOT_COMPARABLE
                for code, action in incoterm_mismatches:
                    issues.append(
                        _issue(snapshot, code, IssueSeverity.HARD_BLOCK, "commercial_basis", action)
                    )
        component_types = {item.component_type: item for item in term.components}
        for component_type in sorted(
            set(normalization.required_price_components), key=lambda item: item.value
        ):
            component = component_types.get(component_type)
            if component_type is ComponentType.PRODUCT:
                state = InclusionStatus.INCLUDED
            else:
                state = component.inclusion_status if component else InclusionStatus.UNKNOWN
            # A separately quoted component remains a separate boundary, but
            # ceases to be unresolved once it carries an explicit amount and
            # quantity basis.  It must never be silently folded into product
            # price either way.
            unresolved_component = state is InclusionStatus.UNKNOWN or (
                state is InclusionStatus.QUOTED_SEPARATELY
                and (component is None or component.amount is None)
            )
            if unresolved_component:
                unresolved.append((snapshot.supplier_quote_snapshot_id, component_type, state))
                values["completeness"] = ComparabilityStatus.CONDITIONALLY_COMPARABLE
                code = "REQUIRED_COMPONENT_UNKNOWN"
                issues.append(
                    _issue(
                        snapshot,
                        code,
                        IssueSeverity.NEEDS_INPUT,
                        "completeness",
                        "request explicit cost boundary",
                    )
                )
            if (
                component_type is ComponentType.TAX
                and component is not None
                and component.tax_treatment is InclusionStatus.UNKNOWN
            ):
                unresolved.append(
                    (snapshot.supplier_quote_snapshot_id, component_type, InclusionStatus.UNKNOWN)
                )
                values["completeness"] = ComparabilityStatus.CONDITIONALLY_COMPARABLE
                issues.append(
                    _issue(
                        snapshot,
                        "TAX_TREATMENT_UNKNOWN",
                        IssueSeverity.NEEDS_INPUT,
                        "completeness",
                        "request tax treatment",
                    )
                )
        normalized_for_snapshot = [
            x
            for x in normalization.normalized_lines
            if x.supplier_quote_snapshot_id == snapshot.supplier_quote_snapshot_id
        ]
        if any(
            status is ComparabilityStatus.NOT_COMPARABLE
            for line in normalized_for_snapshot
            for dimension, status in line.dimension_comparability
            if dimension == "quantity"
        ):
            values["quantity"] = ComparabilityStatus.NOT_COMPARABLE
            issues.append(
                _issue(
                    snapshot,
                    "QUANTITY_NOT_COMPARABLE",
                    IssueSeverity.HARD_BLOCK,
                    "quantity",
                    "obtain a quote covering the target quantity and permitted unit basis",
                )
            )
        if any(x.currency_converted_price is None for x in normalized_for_snapshot):
            values["currency"] = ComparabilityStatus.UNKNOWN
            issues.append(
                _issue(
                    snapshot,
                    "FX_SNAPSHOT_MISSING",
                    IssueSeverity.NEEDS_INPUT,
                    "currency",
                    "supply a fixed FX snapshot",
                )
            )
        dimensions.append((snapshot.supplier_quote_snapshot_id, tuple(sorted(values.items()))))
    if any(issue.severity is IssueSeverity.HARD_BLOCK for issue in issues):
        disposition = ComparisonDisposition.NOT_COMPARABLE
    elif issues:
        disposition = ComparisonDisposition.CONDITIONALLY_COMPARABLE
    else:
        disposition = ComparisonDisposition.COMPARABLE
    fingerprint = stable_fingerprint(
        {
            "snapshots": [x.content_fingerprint for x in snapshots],
            "normalization": normalization.result_fingerprint,
            "as_of": as_of,
            "issues": [x.issue_id for x in issues],
        }
    )
    matrix = SupplierComparisonMatrix(
        comparison_matrix_id=str(uuid5(NAMESPACE_URL, f"trade-alpha/ep06/matrix/{fingerprint}")),
        quote_snapshot_ids=tuple(x.supplier_quote_snapshot_id for x in snapshots),
        target_quantity_tiers=tuple(
            {
                (line.quantity_tier.amount, line.quantity_tier.unit): line.quantity_tier
                for line in normalization.normalized_lines
            }[key]
            for key in sorted(
                {
                    (line.quantity_tier.amount, line.quantity_tier.unit)
                    for line in normalization.normalized_lines
                },
                key=lambda item: (item[1], item[0]),
            )
        ),
        normalization_run_id=normalization.normalization_run_id,
        as_of=as_of,
        supplier_dimension_values=tuple(dimensions),
        issue_refs=tuple(x.issue_id for x in issues),
        unresolved_cost_components=tuple(dict.fromkeys(unresolved)),
        disposition=disposition,
        content_fingerprint=fingerprint,
    )
    return matrix, tuple(issues)
