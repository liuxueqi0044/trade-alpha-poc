"""Application services for evidence-first supplier quotation processing."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.contracts.fingerprints import stable_fingerprint

from .comparability import analyze_snapshots
from .models import (
    CandidateReviewStatus,
    ComparabilityIssue,
    ConfirmedQuoteLine,
    FxSnapshotReaderRecord,
    QuoteNormalizationPolicyVersion,
    QuoteNormalizationRun,
    QuoteTerm,
    SupplierComparisonMatrix,
    SupplierQuoteDecision,
    SupplierQuoteFieldCandidate,
    SupplierQuoteIntakeReceipt,
    SupplierQuoteLineCandidate,
    SupplierQuoteSnapshot,
    canonical_commercial_mime,
    commercial_input_fingerprint,
    commercial_quarantine_status,
)
from .normalization import normalization_fingerprint, normalize_line, policy_content_fingerprint
from .ports import (
    CommercialEvidenceIntakePort,
    FxSnapshotReader,
    ProductSpecReader,
    SupplierRfqPackageReader,
    UnitConversionPort,
)
from .repository import InMemorySupplierQuoteRepository


class _RfqLineLike(Protocol):
    line_id: str


class _StructuredRequestLike(Protocol):
    lines: tuple[_RfqLineLike, ...]


class _PackageSnapshotLike(Protocol):
    supplier_rfq_package_id: str
    supplier_id: str
    product_spec_version_id: str
    supplier_profile_version_id: str
    structured_request: _StructuredRequestLike


class FixtureSupplierRfqPackageReader:
    """Small EP-05 fixture reader using the stable IDs of its package snapshot."""

    def __init__(self, packages: tuple[_PackageSnapshotLike, ...] = ()) -> None:
        self._values: dict[str, tuple[str, str, str, tuple[str, ...]]] = {}
        for package in packages:
            self.add(package)

    def add(self, package: _PackageSnapshotLike) -> None:
        package_id = package.supplier_rfq_package_id
        supplier_id = package.supplier_id
        product_spec_version_id = package.product_spec_version_id
        profile_version_id = package.supplier_profile_version_id
        prior = self._values.get(package_id)
        rfq_line_ids = tuple(sorted({line.line_id for line in package.structured_request.lines}))
        if not rfq_line_ids:
            raise ValueError(
                "supplier RFQ package must expose at least one structured request line"
            )
        value = (supplier_id, profile_version_id, product_spec_version_id, rfq_line_ids)
        if prior is not None and prior != value:
            raise ValueError("fixture package ID conflicts with a different immutable package")
        self._values[package_id] = value

    def add_ids(
        self,
        package_id: str,
        supplier_id: str,
        product_spec_version_id: str,
        *,
        supplier_profile_version_id: str | None = None,
        rfq_line_ids: tuple[str, ...] = ("line-1",),
    ) -> None:
        self._values[package_id] = (
            supplier_id,
            supplier_profile_version_id or f"{supplier_id}-profile-v1",
            product_spec_version_id,
            tuple(sorted(set(rfq_line_ids))),
        )

    def package_exists(self, supplier_rfq_package_id: str) -> bool:
        return supplier_rfq_package_id in self._values

    def supplier_for_package(self, supplier_rfq_package_id: str) -> str | None:
        value = self._values.get(supplier_rfq_package_id)
        return value[0] if value else None

    def product_spec_for_package(self, supplier_rfq_package_id: str) -> str | None:
        value = self._values.get(supplier_rfq_package_id)
        return value[2] if value else None

    def profile_version_for_package(self, supplier_rfq_package_id: str) -> str | None:
        value = self._values.get(supplier_rfq_package_id)
        return value[1] if value else None

    def rfq_line_ids_for_package(self, supplier_rfq_package_id: str) -> tuple[str, ...]:
        value = self._values.get(supplier_rfq_package_id)
        return value[3] if value else ()


class FixtureProductSpecReader:
    def __init__(self, version_ids: tuple[str, ...] = ()) -> None:
        self.version_ids = set(version_ids)

    def version_exists(self, product_spec_version_id: str) -> bool:
        return product_spec_version_id in self.version_ids


class InMemoryFxSnapshotReader:
    def __init__(self, records: tuple[FxSnapshotReaderRecord, ...] = ()) -> None:
        self._records = {record.fx_snapshot_id: record.snapshot for record in records}

    def add(self, record: FxSnapshotReaderRecord) -> None:
        prior = self._records.get(record.fx_snapshot_id)
        if prior is not None and prior != record.snapshot:
            raise ValueError("FxSnapshot ID conflicts with a different immutable snapshot")
        self._records[record.fx_snapshot_id] = record.snapshot

    def get(self, fx_snapshot_id: str):  # type: ignore[no-untyped-def]
        return self._records.get(fx_snapshot_id)


class SupplierQuoteIntakeService:
    def __init__(
        self,
        intake: CommercialEvidenceIntakePort,
        repository: InMemorySupplierQuoteRepository,
        packages: SupplierRfqPackageReader,
    ) -> None:
        self.intake_port = intake
        self.repository = repository
        self.packages = packages

    def capture(self, **kwargs: object) -> SupplierQuoteIntakeReceipt:
        package_id = str(kwargs["supplier_rfq_package_id"])
        supplier_id = str(kwargs["supplier_id"])
        if not self.packages.package_exists(package_id):
            raise ValueError("supplier RFQ package does not exist")
        if self.packages.supplier_for_package(package_id) != supplier_id:
            raise ValueError("quote supplier does not match the precise supplier RFQ package")
        receipt = self.intake_port.capture(**kwargs)  # type: ignore[arg-type]
        if receipt.supplier_rfq_package_id != package_id or receipt.supplier_id != supplier_id:
            raise ValueError("commercial evidence adapter returned mismatched quote scope")
        content = kwargs["content"]
        filename = kwargs["filename"]
        submitted_by = kwargs["submitted_by"]
        if (
            not isinstance(content, bytes)
            or not isinstance(filename, str)
            or not isinstance(submitted_by, str)
        ):
            raise TypeError("quote capture content, filename, and submitted_by have invalid types")
        declared_mime_raw = kwargs.get("declared_mime")
        declared_mime = declared_mime_raw if isinstance(declared_mime_raw, str) else None
        received_at_raw = kwargs.get("received_at")
        if not isinstance(received_at_raw, datetime):
            raise TypeError("quote capture received_at has an invalid type")
        expected_mime = canonical_commercial_mime(filename, declared_mime)
        if (
            len(receipt.artifact_refs) != 1
            or receipt.filename != filename
            or receipt.mime_type != expected_mime
            or receipt.submitted_by != submitted_by
            or receipt.received_at != require_utc(received_at_raw)
        ):
            raise ValueError("commercial evidence adapter returned mismatched capture metadata")
        baseline_quarantine = commercial_quarantine_status(
            content, filename, expected_mime
        )
        if baseline_quarantine != "clear" and receipt.quarantine_status == "clear":
            raise ValueError(
                "commercial evidence adapter attempted to downgrade baseline quarantine"
            )
        if receipt.content_sha256 != f"sha256:{hashlib.sha256(content).hexdigest()}":
            raise ValueError("commercial evidence adapter returned a mismatched content hash")
        if receipt.artifact_refs[0] != receipt.content_sha256:
            raise ValueError(
                "commercial evidence artifact binding does not match submitted content"
            )
        raw_idempotency_key = kwargs.get("idempotency_key")
        idempotency_key = raw_idempotency_key if isinstance(raw_idempotency_key, str) else None
        raw_quote_received_at = kwargs.get("quote_received_at")
        quote_received_at = (
            raw_quote_received_at if isinstance(raw_quote_received_at, datetime) else None
        )
        if (
            receipt.idempotency_key != idempotency_key
            or receipt.quote_received_at != quote_received_at
        ):
            raise ValueError("commercial evidence adapter returned mismatched capture metadata")
        expected = commercial_input_fingerprint(
            supplier_id=supplier_id,
            supplier_rfq_package_id=package_id,
            content=content,
            filename=filename,
            mime_type=expected_mime,
            idempotency_key=idempotency_key,
            quote_received_at=quote_received_at,
            submitted_by=submitted_by,
        )
        if receipt.input_fingerprint != expected:
            raise ValueError("commercial evidence adapter returned a mismatched input fingerprint")
        return self.repository.save_receipt(receipt)

    ingest = capture


class SupplierQuoteExtractionService:
    def __init__(self, repository: InMemorySupplierQuoteRepository) -> None:
        self.repository = repository

    def project_candidates(
        self,
        quote_id: str,
        *,
        fields: tuple[SupplierQuoteFieldCandidate, ...] = (),
        lines: tuple[SupplierQuoteLineCandidate, ...] = (),
    ) -> tuple[tuple[SupplierQuoteFieldCandidate, ...], tuple[SupplierQuoteLineCandidate, ...]]:
        receipt = self.repository.receipt(quote_id)
        if receipt is None:
            raise ValueError("quote evidence has not been captured")
        if receipt.quarantine_status != "clear":
            raise ValueError("quarantined quote evidence cannot be projected")
        allowed_artifacts = set(receipt.artifact_refs)
        for field in fields:
            if (
                field.quote_id != quote_id
                or field.review_status is not CandidateReviewStatus.PENDING
            ):
                raise ValueError("only pending candidates for this quote may be projected")
            if field.provenance_ref.evidence.artifact_id not in allowed_artifacts:
                raise ValueError("field candidate evidence does not belong to this quote receipt")
            self.repository.save_field_candidate(field)
        for line in lines:
            if line.quote_id != quote_id:
                raise ValueError("line candidate belongs to a different quote")
            if any(
                ref.evidence.artifact_id not in allowed_artifacts
                for ref in line.provenance_refs
            ):
                raise ValueError("line candidate evidence does not belong to this quote receipt")
            self.repository.save_line_candidate(line)
        return self.repository.field_candidates_for(quote_id), self.repository.line_candidates_for(
            quote_id
        )


class SupplierQuoteReviewService:
    def __init__(self, repository: InMemorySupplierQuoteRepository) -> None:
        self.repository = repository

    def record_decision(self, decision: SupplierQuoteDecision) -> SupplierQuoteDecision:
        receipt = self.repository.receipt(decision.quote_id)
        if receipt is None:
            raise ValueError("quote evidence has not been captured")
        if receipt.quarantine_status != "clear":
            raise ValueError("quarantined quote evidence cannot be decided")
        if decision.provenance_ref.evidence.artifact_id not in set(receipt.artifact_refs):
            raise ValueError("decision evidence does not belong to this quote receipt")
        field_ids = {
            candidate.candidate_id
            for candidate in self.repository.field_candidates_for(decision.quote_id)
        }
        line_ids = {
            candidate.candidate_line_id
            for candidate in self.repository.line_candidates_for(decision.quote_id)
        }
        if decision.candidate_id and decision.candidate_id not in field_ids | line_ids:
            raise ValueError("decision candidate does not belong to this quote")
        existing = self.repository.decisions_for(decision.quote_id)
        prior_same_id = next(
            (item for item in existing if item.decision_id == decision.decision_id), None
        )
        if prior_same_id is not None:
            return self.repository.save_decision(decision)
        effective = _effective_decisions(existing)
        current_for_path = next(
            (item for item in effective if item.field_path == decision.field_path), None
        )
        if decision.supersedes_decision_id is None:
            if current_for_path is not None:
                raise ValueError(
                    "new decision for an existing field must supersede its current tip"
                )
        elif (
            current_for_path is None
            or current_for_path.decision_id != decision.supersedes_decision_id
        ):
            raise ValueError("decision supersedes must reference the current same-field tip")
        return self.repository.save_decision(decision)


class SupplierQuoteService:
    def __init__(
        self,
        repository: InMemorySupplierQuoteRepository,
        packages: SupplierRfqPackageReader,
        specs: ProductSpecReader | None = None,
    ) -> None:
        self.repository = repository
        self.packages = packages
        self.specs = specs

    def confirm_snapshot(
        self,
        quote_id: str,
        *,
        supplier_profile_version_id: str,
        product_spec_version_id: str,
        lines: tuple[ConfirmedQuoteLine, ...],
        terms: QuoteTerm,
        actor_id: str,
        confirmed_at: datetime | None = None,
        quote_reference_raw: str | None = None,
        deviations: tuple[str, ...] = (),
        unresolved_fields: tuple[str, ...] = (),
        supersedes: str | None = None,
    ) -> SupplierQuoteSnapshot:
        receipt = self.repository.receipt(quote_id)
        if receipt is None:
            raise ValueError("quote evidence has not been captured")
        when = require_utc(confirmed_at or datetime.now(UTC))
        evidence_times = tuple(
            item
            for item in (receipt.received_at, receipt.quote_received_at)
            if item is not None
        )
        if any(when < item for item in evidence_times):
            raise ValueError("quote confirmation cannot predate its receipt evidence")
        if receipt.quarantine_status != "clear":
            raise ValueError("quarantined quote evidence cannot be confirmed")
        package_id = receipt.supplier_rfq_package_id
        if self.packages.supplier_for_package(package_id) != receipt.supplier_id:
            raise ValueError("quote supplier no longer matches its exact RFQ package")
        if self.packages.product_spec_for_package(package_id) != product_spec_version_id:
            raise ValueError(
                "confirmed quote must reference the RFQ package's exact product spec version"
            )
        if self.packages.profile_version_for_package(package_id) != supplier_profile_version_id:
            raise ValueError(
                "confirmed quote must reference the RFQ package's exact supplier profile version"
            )
        if self.specs is not None and not self.specs.version_exists(product_spec_version_id):
            raise ValueError("product spec version does not exist")
        projected = self.repository.field_candidates_for(quote_id)
        decisions = self.repository.decisions_for(quote_id, as_of=when)
        if not decisions:
            raise ValueError("confirmation requires at least one append-only human decision")
        effective_decisions = _effective_decisions(decisions)
        decided_candidates = {
            item.candidate_id for item in effective_decisions if item.candidate_id
        }
        pending = [
            item.candidate_id for item in projected if item.candidate_id not in decided_candidates
        ]
        pending_line_mappings = [
            item.candidate_line_id
            for item in self.repository.line_candidates_for(quote_id)
            if item.candidate_line_id not in decided_candidates
        ]
        if pending or pending_line_mappings:
            raise ValueError("all projected quote candidates require an append-only human decision")
        line_candidates = self.repository.line_candidates_for(quote_id)
        if not line_candidates:
            raise ValueError("confirmation requires supplier quote line candidates")
        accepted_line_ids = {
            decision.candidate_id
            for decision in effective_decisions
            if decision.decision.value == "accept" and decision.candidate_id is not None
        }
        package_line_ids = set(self.packages.rfq_line_ids_for_package(package_id))
        field_candidates = {
            candidate.candidate_id: candidate
            for candidate in self.repository.field_candidates_for(quote_id)
        }
        for line_index, line in enumerate(lines):
            if line.rfq_line_id not in package_line_ids:
                raise ValueError(
                    "confirmed line does not belong to the precise supplier RFQ package"
                )
            matches = [
                candidate
                for candidate in line_candidates
                if candidate.candidate_line_id in accepted_line_ids
                and candidate.proposed_rfq_line_id == line.rfq_line_id
                and candidate.supplier_line_ref == line.supplier_line_ref
            ]
            if len(matches) != 1:
                raise ValueError(
                    "each confirmed line requires exactly one accepted line mapping candidate"
                )
            if sum(
                decision.candidate_id == matches[0].candidate_line_id
                and decision.field_path == f"lines[{line_index}].rfq_line_id"
                and decision.decision.value == "accept"
                for decision in effective_decisions
            ) != 1:
                raise ValueError(
                    "accepted line mapping decision path does not match confirmed line"
                )
            price_paths = {
                f"lines[{line_index}].product_price",
                f"lines[{line_index}].unit_price",
            }
            price_amount = line.product_price.amount
            price_basis = line.product_price.quantity_basis
            if price_amount is None or price_basis is None:
                raise ValueError("confirmed product price is incomplete")
            price_fact = {
                "amount": str(price_amount.amount),
                "currency": str(price_amount.currency),
                "basis": str(price_basis.amount),
            }
            if sum(
                _decision_matches_fact(
                    decision,
                    expected=price_fact,
                    allowed_paths=price_paths,
                    field_candidates=field_candidates,
                )
                for decision in effective_decisions
            ) != 1:
                raise ValueError(
                    "each confirmed product price requires one effective matching human decision"
                )
        if {line.rfq_line_id for line in lines} != package_line_ids:
            raise ValueError(
                "confirmed quote must cover every and only RFQ line in the precise package"
            )
        if (
            terms.currency is None
            or terms.incoterm is None
            or (terms.quote_valid_from is None and terms.quote_valid_until is None)
        ):
            raise ValueError("confirmed commercial terms require currency, Incoterm, and validity")
        if any(
            line.product_price.amount is None
            or str(line.product_price.amount.currency) != terms.currency
            for line in lines
        ):
            raise ValueError(
                "commercial terms currency must match every confirmed product price currency"
            )
        term_facts = _commercial_term_facts(terms)
        for field_path, expected in term_facts.items():
            if sum(
                _decision_matches_fact(
                    decision,
                    expected=expected,
                    allowed_paths={field_path},
                    field_candidates=field_candidates,
                )
                for decision in effective_decisions
            ) != 1:
                raise ValueError(
                    f"commercial term {field_path} requires one matching human decision"
                )
        allowed_artifacts = set(receipt.artifact_refs)
        provenance_refs = (
            [
                provenance
                for line in lines
                for provenance in (*line.provenance_refs, line.product_price.provenance_ref)
            ]
            + [component.provenance_ref for component in terms.components]
            + ([terms.sample_fee.provenance_ref] if terms.sample_fee is not None else [])
            + [decision.provenance_ref for decision in effective_decisions]
        )
        if any(ref.evidence.artifact_id not in allowed_artifacts for ref in provenance_refs):
            raise ValueError("confirmed values and decisions must be grounded in receipt evidence")
        if not actor_id.strip():
            raise ValueError("confirmation actor_id must not be blank")
        old = self.repository.snapshots_for_quote(quote_id)
        if supersedes is not None:
            prior_snapshot = self.repository.snapshot(supersedes)
            if prior_snapshot is None or prior_snapshot.supplier_quote_id != quote_id:
                raise ValueError("supersedes must reference an existing snapshot of the same quote")
            if old and prior_snapshot != old[-1]:
                raise ValueError("supersedes must reference the current quote lineage tip")
        version = len(old) + 1
        payload = {
            "quote_id": quote_id,
            "supplier": receipt.supplier_id,
            "supplier_profile_version_id": supplier_profile_version_id,
            "package": package_id,
            "product_spec_version_id": product_spec_version_id,
            "lines": lines,
            "terms": terms,
            "evidence": receipt.artifact_refs,
            "decisions": [x.decision_id for x in effective_decisions],
            "deviations": deviations,
            "unresolved_fields": unresolved_fields,
            "supersedes": supersedes,
        }
        fingerprint = stable_fingerprint(payload)
        prior = next((item for item in old if item.content_fingerprint == fingerprint), None)
        if prior is not None:
            return prior
        if old and supersedes is None:
            raise ValueError("a changed quote snapshot must supersede the current lineage tip")
        if terms.quote_valid_until is not None and when > terms.quote_valid_until:
            validity_status = "expired"
        elif terms.quote_valid_from is not None and when < terms.quote_valid_from:
            validity_status = "not_yet_valid"
        elif terms.quote_valid_from is None and terms.quote_valid_until is None:
            validity_status = "unknown"
        else:
            validity_status = "valid"
        snapshot = SupplierQuoteSnapshot(
            supplier_quote_snapshot_id=str(
                uuid5(NAMESPACE_URL, f"trade-alpha/ep06/snapshot/{fingerprint}")
            ),
            supplier_quote_id=quote_id,
            version=version,
            supplier_id=receipt.supplier_id,
            supplier_profile_version_id=supplier_profile_version_id,
            supplier_rfq_package_id=package_id,
            product_spec_version_id=product_spec_version_id,
            quote_reference_raw=quote_reference_raw,
            confirmed_lines=lines,
            confirmed_terms=terms,
            deviations=deviations,
            unresolved_fields=unresolved_fields,
            evidence_manifest=receipt.artifact_refs,
            effective_decision_ids=tuple(item.decision_id for item in effective_decisions),
            quote_validity_status=validity_status,
            received_at=receipt.received_at,
            confirmed_at=when,
            content_fingerprint=fingerprint,
            supersedes=supersedes,
        )
        return self.repository.save_snapshot(snapshot)

    def get_as_of(self, quote_id: str, as_of: datetime) -> SupplierQuoteSnapshot | None:
        moment = require_utc(as_of)
        values = [
            item
            for item in self.repository.snapshots_for_quote(quote_id)
            if item.confirmed_at <= moment
        ]
        return max(values, key=lambda item: (item.confirmed_at, item.version), default=None)


def _effective_decisions(
    decisions: tuple[SupplierQuoteDecision, ...],
) -> tuple[SupplierQuoteDecision, ...]:
    by_id = {decision.decision_id: decision for decision in decisions}
    superseded: set[str] = set()
    for decision in decisions:
        prior_id = decision.supersedes_decision_id
        if prior_id is None:
            continue
        prior = by_id.get(prior_id)
        if (
            prior is None
            or prior.quote_id != decision.quote_id
            or prior.field_path != decision.field_path
        ):
            raise ValueError("decision supersedes must reference the same quote field")
        superseded.add(prior_id)
    effective = tuple(item for item in decisions if item.decision_id not in superseded)
    paths = [item.field_path for item in effective]
    if len(paths) != len(set(paths)):
        raise ValueError("multiple effective decisions exist for the same quote field")
    return effective


def _decision_matches_fact(
    decision: SupplierQuoteDecision,
    *,
    expected: Any,
    allowed_paths: set[str],
    field_candidates: dict[str, SupplierQuoteFieldCandidate],
) -> bool:
    if decision.field_path not in allowed_paths:
        return False
    candidate = (
        field_candidates.get(decision.candidate_id) if decision.candidate_id is not None else None
    )
    if decision.decision.value == "enter_manual_value":
        value = decision.typed_value
    elif (
        decision.decision.value == "accept"
        and candidate is not None
        and candidate.field_path in allowed_paths
    ):
        value = candidate.proposed_typed_value
    else:
        return False
    return stable_fingerprint({"value": value}) == stable_fingerprint({"value": expected})


def _commercial_term_facts(terms: QuoteTerm) -> dict[str, Any]:
    if terms.currency is None or terms.incoterm is None:
        raise ValueError("commercial terms require currency and Incoterm")
    facts: dict[str, Any] = {
        "terms.currency": terms.currency,
        "terms.incoterm": terms.incoterm.model_dump(mode="json"),
        "terms.validity": {
            "quote_valid_from": terms.quote_valid_from.isoformat()
            if terms.quote_valid_from
            else None,
            "quote_valid_until": terms.quote_valid_until.isoformat()
            if terms.quote_valid_until
            else None,
        },
    }
    canonical = terms.model_dump(mode="json")
    optional_fields = {
        "payment_terms": terms.payment_terms,
        "sample_fee": terms.sample_fee,
        "sample_lead_time": terms.sample_lead_time,
        "production_lead_time": terms.production_lead_time,
        "production_lead_time_start_trigger": terms.production_lead_time_start_trigger,
        "assumptions": terms.assumptions or None,
        "exclusions": terms.exclusions or None,
    }
    facts.update(
        {
            f"terms.{field_name}": canonical[field_name]
            for field_name, value in optional_fields.items()
            if value is not None
        }
    )
    facts.update(
        {
            f"terms.components.{component.component_type.value}": component.model_dump(
                mode="json"
            )
            for component in terms.components
        }
    )
    return facts


class QuoteNormalizationService:
    def __init__(
        self, unit_converter: UnitConversionPort, fx_reader: FxSnapshotReader | None = None
    ) -> None:
        self.unit_converter = unit_converter
        self.fx_reader = fx_reader

    def normalize(
        self,
        snapshots: tuple[SupplierQuoteSnapshot, ...],
        *,
        policy: QuoteNormalizationPolicyVersion,
        as_of: datetime,
        fx_snapshot_id: str | None = None,
    ) -> QuoteNormalizationRun:
        moment = require_utc(as_of)
        if policy.config_fingerprint != policy_content_fingerprint(policy):
            raise ValueError(
                "normalization policy config_fingerprint does not match its canonical content"
            )
        if not snapshots:
            raise ValueError("normalization requires quote snapshots")
        if any(
            snapshot.received_at > moment or snapshot.confirmed_at > moment
            for snapshot in snapshots
        ):
            raise ValueError("normalization cannot use a quote snapshot from after as_of")
        ordered = tuple(sorted(snapshots, key=lambda item: item.supplier_quote_snapshot_id))
        normalized = []
        issues: list[str] = []
        for snapshot in ordered:
            for index, _line in enumerate(snapshot.confirmed_lines):
                for target_tier in policy.target_quantity_tiers:
                    line, line_issues = normalize_line(
                        snapshot,
                        index,
                        policy=policy,
                        unit_converter=self.unit_converter,
                        fx_reader=self.fx_reader,
                        fx_snapshot_id=fx_snapshot_id,
                        target_quantity_tier=target_tier,
                        as_of=moment,
                    )
                    normalized.append(line)
                    issues.extend(line_issues)
        input_fingerprint = normalization_fingerprint(ordered, policy, moment, fx_snapshot_id)
        result_fingerprint = stable_fingerprint(
            {"input": input_fingerprint, "lines": normalized, "issues": sorted(issues)}
        )
        return QuoteNormalizationRun(
            normalization_run_id=str(
                uuid5(NAMESPACE_URL, f"trade-alpha/ep06/normalization/{result_fingerprint}")
            ),
            quote_snapshot_ids=tuple(item.supplier_quote_snapshot_id for item in ordered),
            normalization_policy_version_id=policy.normalization_policy_version_id,
            required_price_components=policy.required_price_components,
            incoterm_comparison_policy=policy.incoterm_comparison_policy,
            validity_policy=policy.validity_policy,
            fx_snapshot_id=fx_snapshot_id,
            unit_adapter_version="unit-conversion-port/1.0.0",
            as_of=moment,
            normalized_lines=tuple(normalized),
            issues=tuple(sorted(set(issues))),
            input_fingerprint=input_fingerprint,
            result_fingerprint=result_fingerprint,
        )


class QuoteComparisonService:
    def __init__(self, repository: InMemorySupplierQuoteRepository) -> None:
        self.repository = repository

    def compare(
        self,
        snapshots: tuple[SupplierQuoteSnapshot, ...],
        *,
        normalization: QuoteNormalizationRun,
        as_of: datetime,
    ) -> tuple[SupplierComparisonMatrix, tuple[ComparabilityIssue, ...]]:
        moment = require_utc(as_of)
        if normalization.as_of != moment:
            raise ValueError("comparison as_of must equal normalization as_of")
        ids = tuple(sorted(item.supplier_quote_snapshot_id for item in snapshots))
        if ids != tuple(sorted(normalization.quote_snapshot_ids)):
            raise ValueError("normalization run does not cover exactly these quote snapshots")
        for snapshot in snapshots:
            if snapshot.received_at > moment or snapshot.confirmed_at > moment:
                raise ValueError("comparison cannot use a quote snapshot from after as_of")
            persisted = self.repository.snapshot(snapshot.supplier_quote_snapshot_id)
            if (
                persisted != snapshot
                or persisted.content_fingerprint != snapshot.content_fingerprint
            ):
                raise ValueError("comparison requires repository-saved immutable quote snapshots")
        matrix, issues = analyze_snapshots(
            tuple(sorted(snapshots, key=lambda item: item.supplier_quote_snapshot_id)),
            normalization=normalization,
            as_of=moment,
        )
        return self.repository.save_matrix(matrix), issues

    def get_as_of(self, matrix_id: str, as_of: datetime) -> SupplierComparisonMatrix | None:
        matrix = self.repository.matrix(matrix_id)
        return matrix if matrix is not None and matrix.as_of <= require_utc(as_of) else None
