from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from reportlab.pdfgen.canvas import Canvas

from trade_alpha.contracts.common import EvidenceLocator, VersionRef
from trade_alpha.execution.adapters.commercial_evidence import CommercialEvidenceAdapter
from trade_alpha.execution.adapters.pint_units import PintUnitConverter
from trade_alpha.execution.contracts import FxSnapshot, Incoterm, MonetaryAmount, ProvenanceRef
from trade_alpha.execution.supplier_quotes import (
    ComponentType,
    ConfirmedQuoteLine,
    FixtureProductSpecReader,
    FixtureSupplierRfqPackageReader,
    FxSnapshotReaderRecord,
    InclusionStatus,
    InMemoryFxSnapshotReader,
    InMemorySupplierQuoteRepository,
    PriceComponent,
    QuantityBasis,
    QuoteComparisonService,
    QuoteDecisionAction,
    QuoteNormalizationPolicyVersion,
    QuoteNormalizationService,
    QuoteTerm,
    SupplierQuoteDecision,
    SupplierQuoteExtractionService,
    SupplierQuoteIntakeService,
    SupplierQuoteLineCandidate,
    SupplierQuoteReviewService,
    SupplierQuoteService,
)
from trade_alpha.execution.supplier_quotes.comparability import analyze_snapshots
from trade_alpha.execution.supplier_quotes.normalization import policy_content_fingerprint
from trade_alpha.extraction import ExtractionService
from trade_alpha.ingestion import EvidenceStore

NOW = datetime(2026, 8, 27, tzinfo=UTC)


def provenance(artifact_id: str = "artifact-fixture") -> ProvenanceRef:
    return ProvenanceRef(
        source="fixture quote",
        evidence=EvidenceLocator(artifact_id=artifact_id, block_ids=("block-1",)),
        observed_at=NOW,
        source_version=VersionRef(name="fixture", version="1.0.0"),
    )


def component(
    amount: str, currency: str = "CNY", *, basis: Decimal = Decimal("1")
) -> PriceComponent:
    return PriceComponent(
        component_type=ComponentType.PRODUCT,
        inclusion_status=InclusionStatus.INCLUDED,
        amount=MonetaryAmount(amount=Decimal(amount), currency=currency),
        quantity_basis=QuantityBasis(amount=basis, unit="piece"),
        provenance_ref=provenance(),
    )


def services(tmp_path):  # type: ignore[no-untyped-def]
    repository = InMemorySupplierQuoteRepository()
    packages = FixtureSupplierRfqPackageReader()
    packages.add_ids("package-a", "supplier-a", "spec-v1")
    packages.add_ids("package-b", "supplier-b", "spec-v1")
    intake = SupplierQuoteIntakeService(
        CommercialEvidenceAdapter(EvidenceStore(tmp_path / "evidence")), repository, packages
    )
    quotes = SupplierQuoteService(repository, packages, FixtureProductSpecReader(("spec-v1",)))
    return repository, intake, quotes


def capture(intake, supplier: str, package: str, content: bytes, filename: str):  # type: ignore[no-untyped-def]
    if filename.lower().endswith(".pdf") and not content.startswith(b"%PDF-"):
        content = b"%PDF-" + content
    if filename.lower().endswith(".xlsx") and not content.startswith(b"PK"):
        content = b"PK" + content
    return intake.capture(
        supplier_id=supplier,
        supplier_rfq_package_id=package,
        content=content,
        filename=filename,
        declared_mime=None,
        idempotency_key=f"{supplier}-{filename}",
        received_at=NOW,
        quote_received_at=NOW,
        submitted_by="operator",
    )


def snapshot(
    quotes,
    receipt,
    amount: str,
    incoterm: str,
    place: str,
    *,
    until: datetime,
    currency: str = "CNY",
    basis: Decimal = Decimal("1"),
    confirmed_at: datetime = NOW,
):  # type: ignore[no-untyped-def]
    evidence = provenance(receipt.artifact_refs[0])
    line = ConfirmedQuoteLine(
        rfq_line_id="line-1",
        supplier_line_ref="quoted-line-1",
        quantity_basis=QuantityBasis(amount=Decimal("1"), unit="piece"),
        product_price=component(amount, currency, basis=basis).model_copy(
            update={"provenance_ref": evidence}
        ),
        provenance_refs=(evidence,),
    )
    line_candidate = SupplierQuoteLineCandidate(
        candidate_line_id=f"mapping-{receipt.quote_id}",
        quote_id=receipt.quote_id,
        supplier_line_ref=line.supplier_line_ref,
        proposed_rfq_line_id=line.rfq_line_id,
        mapping_confidence=Decimal("1"),
        provenance_refs=(evidence,),
    )
    SupplierQuoteExtractionService(quotes.repository).project_candidates(
        receipt.quote_id, lines=(line_candidate,)
    )
    quotes.repository.save_decision(
        SupplierQuoteDecision(
            decision_id=f"manual-{receipt.quote_id}",
            quote_id=receipt.quote_id,
            field_path="lines[0].unit_price",
            decision=QuoteDecisionAction.ENTER_MANUAL_VALUE,
            typed_value={"amount": amount, "currency": currency, "basis": str(basis)},
            rationale="fixture quote reviewed",
            actor_id="reviewer",
            decided_at=NOW,
            provenance_ref=evidence,
        )
    )
    terms = QuoteTerm(
        currency=currency,
        incoterm=Incoterm(
            code=incoterm, rules_version="Incoterms 2020", named_place=place
        ),
        quote_valid_until=until,
        components=(
            PriceComponent(
                component_type=ComponentType.PACKAGING,
                inclusion_status=InclusionStatus.UNKNOWN,
                provenance_ref=evidence,
            ),
        ),
    )
    term_facts = {
        "terms.currency": currency,
        "terms.incoterm": terms.incoterm.model_dump(mode="json"),
        "terms.validity": {
            "quote_valid_from": None,
            "quote_valid_until": until.isoformat(),
        },
        "terms.components.packaging": terms.components[0].model_dump(mode="json"),
    }
    for field_path, typed_value in term_facts.items():
        quotes.repository.save_decision(
            SupplierQuoteDecision(
                decision_id=f"{field_path}-{receipt.quote_id}",
                quote_id=receipt.quote_id,
                field_path=field_path,
                decision=QuoteDecisionAction.ENTER_MANUAL_VALUE,
                typed_value=typed_value,
                rationale="fixture commercial term reviewed",
                actor_id="reviewer",
                decided_at=NOW,
                provenance_ref=evidence,
            )
        )
    quotes.repository.save_decision(
        SupplierQuoteDecision(
            decision_id=f"accept-{receipt.quote_id}",
            quote_id=receipt.quote_id,
            field_path="lines[0].rfq_line_id",
            candidate_id=line_candidate.candidate_line_id,
            decision=QuoteDecisionAction.ACCEPT,
            rationale="fixture line mapping reviewed",
            actor_id="reviewer",
            decided_at=NOW,
            provenance_ref=evidence,
        )
    )
    return quotes.confirm_snapshot(
        receipt.quote_id,
        supplier_profile_version_id=f"{receipt.supplier_id}-profile-v1",
        product_spec_version_id="spec-v1",
        lines=(line,),
        terms=terms,
        actor_id="reviewer",
        confirmed_at=confirmed_at,
    )


def policy() -> QuoteNormalizationPolicyVersion:
    draft = QuoteNormalizationPolicyVersion(
        normalization_policy_version_id="policy-v1",
        product_family_id="FE-DRY-01",
        target_currency="CNY",
        target_measurement_unit="piece",
        target_quantity_tiers=(QuantityBasis(amount=Decimal("100"), unit="piece"),),
        required_price_components=(ComponentType.PRODUCT, ComponentType.PACKAGING),
        rule_version="1.0.0",
        config_fingerprint="sha256:placeholder",
    )
    return draft.model_copy(update={"config_fingerprint": policy_content_fingerprint(draft)})


def test_ep06_ac_001_three_internal_formats_enter_immutable_m01_evidence(tmp_path) -> None:
    repository, intake, _quotes = services(tmp_path)
    pdf = capture(intake, "supplier-a", "package-a", b"%PDF-fixture", "quote.pdf")
    xlsx = capture(intake, "supplier-a", "package-a", b"xlsx-fixture", "quote.xlsx")
    email = capture(intake, "supplier-a", "package-a", b"price: CNY 20/piece", "quote.eml")
    assert all(len(item.artifact_refs) == 1 for item in (pdf, xlsx, email))
    assert len({item.artifact_refs[0] for item in (pdf, xlsx, email)}) == 3
    assert repository.receipt(pdf.quote_id) == pdf
    assert {item.parser_status for item in (pdf, xlsx, email)} <= {"routed", "manual_review"}


def test_ep06_injected_m02_service_produces_drillable_extraction_run(tmp_path) -> None:
    payload = BytesIO()
    canvas = Canvas(payload)
    canvas.drawString(72, 720, "Supplier quote CNY 25 per piece")
    canvas.save()
    content = payload.getvalue()

    repository = InMemorySupplierQuoteRepository()
    packages = FixtureSupplierRfqPackageReader()
    packages.add_ids("package-a", "supplier-a", "spec-v1")
    extraction = ExtractionService()
    intake = SupplierQuoteIntakeService(
        CommercialEvidenceAdapter(
            EvidenceStore(tmp_path / "m02-evidence"),
            extraction_service=extraction,
        ),
        repository,
        packages,
    )
    receipt = capture(intake, "supplier-a", "package-a", content, "real-quote.pdf")
    assert receipt.parser_status == "extracted"
    assert len(receipt.extraction_run_refs) == 1
    runs = extraction.store.runs_for(receipt.artifact_refs[0])
    assert len(runs) == 1
    assert runs[0].extraction_run_id == receipt.extraction_run_refs[0]
    assert runs[0].blocks and "CNY 25" in " ".join(block.text for block in runs[0].blocks)


def test_ep06_intake_rejects_malicious_adapter_capture_proof(tmp_path) -> None:
    repository, intake, _quotes = services(tmp_path)
    legitimate = capture(intake, "supplier-a", "package-a", b"proof", "proof.eml")

    class MaliciousAdapter:
        def capture(self, **_kwargs):  # type: ignore[no-untyped-def]
            return legitimate.model_copy(update={"content_sha256": "sha256:" + "0" * 64})

    packages = FixtureSupplierRfqPackageReader()
    packages.add_ids("package-a", "supplier-a", "spec-v1")
    malicious_service = SupplierQuoteIntakeService(MaliciousAdapter(), repository, packages)
    with pytest.raises(ValueError, match="mismatched content hash"):
        malicious_service.capture(
            supplier_id="supplier-a",
            supplier_rfq_package_id="package-a",
            content=b"proof",
            filename="proof.eml",
            declared_mime=None,
            idempotency_key="other",
            received_at=NOW,
            quote_received_at=NOW,
            submitted_by="operator",
        )


def test_ep06_intake_recomputes_baseline_quarantine_status(tmp_path) -> None:
    repository = InMemorySupplierQuoteRepository()
    packages = FixtureSupplierRfqPackageReader()
    packages.add_ids("package-a", "supplier-a", "spec-v1")
    real_adapter = CommercialEvidenceAdapter(EvidenceStore(tmp_path / "quarantine-proof"))

    class QuarantineDowngradeAdapter:
        def capture(self, **kwargs):  # type: ignore[no-untyped-def]
            receipt = real_adapter.capture(**kwargs)
            return receipt.model_copy(update={"quarantine_status": "clear"})

    intake = SupplierQuoteIntakeService(
        QuarantineDowngradeAdapter(), repository, packages
    )
    with pytest.raises(ValueError, match="downgrade baseline quarantine"):
        intake.capture(
            supplier_id="supplier-a",
            supplier_rfq_package_id="package-a",
            content=b"not an executable quote",
            filename="quote.exe",
            declared_mime="text/plain",
            idempotency_key="malicious-quarantine-downgrade",
            received_at=NOW,
            quote_received_at=NOW,
            submitted_by="operator",
        )


def test_ep06_confirmation_is_precisely_scoped_immutable_and_as_of_expiry(tmp_path) -> None:
    _repository, intake, quotes = services(tmp_path)
    receipt = capture(intake, "supplier-a", "package-a", b"quote", "quote.txt")
    first = snapshot(quotes, receipt, "250", "EXW", "Factory", until=NOW + timedelta(days=1))
    assert first.validity_as_of(NOW) == "valid"
    assert first.validity_as_of(NOW + timedelta(days=2)) == "expired"
    with pytest.raises(ValueError, match="exact product spec"):
        quotes.confirm_snapshot(
            receipt.quote_id,
            supplier_profile_version_id="supplier-a-profile-v1",
            product_spec_version_id="different-spec",
            lines=first.confirmed_lines,
            terms=first.confirmed_terms,
            actor_id="reviewer",
        )


def test_ep06_confirmed_facts_must_match_human_decisions_and_receipt_evidence(tmp_path) -> None:
    _repository, intake, quotes = services(tmp_path)
    receipt = capture(intake, "supplier-a", "package-a", b"fact binding", "binding.eml")
    confirmed = snapshot(
        quotes,
        receipt,
        "10",
        "EXW",
        "Factory",
        until=NOW + timedelta(days=1),
    )
    line = confirmed.confirmed_lines[0]
    changed_price = line.model_copy(
        update={
            "product_price": line.product_price.model_copy(
                update={"amount": MonetaryAmount(amount="99", currency="CNY")}
            )
        }
    )
    with pytest.raises(ValueError, match="matching human decision"):
        quotes.confirm_snapshot(
            receipt.quote_id,
            supplier_profile_version_id=confirmed.supplier_profile_version_id,
            product_spec_version_id=confirmed.product_spec_version_id,
            lines=(changed_price,),
            terms=confirmed.confirmed_terms,
            actor_id="reviewer",
            supersedes=confirmed.supplier_quote_snapshot_id,
        )

    changed_term = confirmed.confirmed_terms.model_copy(
        update={
            "incoterm": Incoterm(
                code="DDP", rules_version="Incoterms 2020", named_place="Buyer"
            )
        }
    )
    with pytest.raises(ValueError, match="terms.incoterm"):
        quotes.confirm_snapshot(
            receipt.quote_id,
            supplier_profile_version_id=confirmed.supplier_profile_version_id,
            product_spec_version_id=confirmed.product_spec_version_id,
            lines=confirmed.confirmed_lines,
            terms=changed_term,
            actor_id="reviewer",
            supersedes=confirmed.supplier_quote_snapshot_id,
        )

    foreign_evidence = provenance("different-quote-artifact")
    changed_evidence = line.model_copy(update={"provenance_refs": (foreign_evidence,)})
    with pytest.raises(ValueError, match="receipt evidence"):
        quotes.confirm_snapshot(
            receipt.quote_id,
            supplier_profile_version_id=confirmed.supplier_profile_version_id,
            product_spec_version_id=confirmed.product_spec_version_id,
            lines=(changed_evidence,),
            terms=confirmed.confirmed_terms,
            actor_id="reviewer",
            supersedes=confirmed.supplier_quote_snapshot_id,
        )


def test_ep06_every_commercial_term_requires_an_exact_human_decision(tmp_path) -> None:
    _repository, intake, quotes = services(tmp_path)
    receipt = capture(intake, "supplier-a", "package-a", b"all terms", "all-terms.eml")
    confirmed = snapshot(
        quotes,
        receipt,
        "10",
        "EXW",
        "Factory",
        until=NOW + timedelta(days=1),
    )
    evidence = provenance(receipt.artifact_refs[0])
    mutations = {
        "payment_terms": "NET 999 days",
        "sample_fee": PriceComponent(
            component_type=ComponentType.SAMPLE,
            inclusion_status=InclusionStatus.INCLUDED,
            amount=MonetaryAmount(amount="0", currency="CNY"),
            quantity_basis=QuantityBasis(amount="1", unit="sample"),
            provenance_ref=evidence,
        ),
        "sample_lead_time": QuantityBasis(amount="999", unit="day"),
        "production_lead_time": QuantityBasis(amount="999", unit="day"),
        "production_lead_time_start_trigger": "after arbitrary event",
        "assumptions": ("unreviewed assumption",),
        "exclusions": ("unreviewed exclusion",),
    }
    for field_name, value in mutations.items():
        changed_terms = confirmed.confirmed_terms.model_copy(update={field_name: value})
        with pytest.raises(ValueError, match=f"terms.{field_name}"):
            quotes.confirm_snapshot(
                receipt.quote_id,
                supplier_profile_version_id=confirmed.supplier_profile_version_id,
                product_spec_version_id=confirmed.product_spec_version_id,
                lines=confirmed.confirmed_lines,
                terms=changed_terms,
                actor_id="reviewer",
                confirmed_at=NOW,
                supersedes=confirmed.supplier_quote_snapshot_id,
            )


def test_ep06_confirmation_time_follows_evidence_and_human_decisions(tmp_path) -> None:
    repository, intake, quotes = services(tmp_path)
    receipt = capture(intake, "supplier-a", "package-a", b"temporal", "temporal.eml")
    with pytest.raises(ValueError, match="cannot predate its receipt evidence"):
        snapshot(
            quotes,
            receipt,
            "10",
            "EXW",
            "Factory",
            until=NOW + timedelta(days=1),
            confirmed_at=NOW - timedelta(seconds=1),
        )

    valid = snapshot(
        quotes,
        receipt,
        "10",
        "EXW",
        "Factory",
        until=NOW + timedelta(days=1),
        confirmed_at=NOW,
    )
    repository._decisions = [  # type: ignore[attr-defined]
        item.model_copy(update={"decided_at": NOW + timedelta(days=1)})
        for item in repository._decisions  # type: ignore[attr-defined]
    ]
    repository._snapshots.clear()  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="at least one append-only human decision"):
        quotes.confirm_snapshot(
            receipt.quote_id,
            supplier_profile_version_id=valid.supplier_profile_version_id,
            product_spec_version_id=valid.product_spec_version_id,
            lines=valid.confirmed_lines,
            terms=valid.confirmed_terms,
            actor_id="reviewer",
            confirmed_at=NOW,
        )

def test_ep06_file_unit_validity_fx_and_comparison_time_are_fail_closed(tmp_path) -> None:
    repository, intake, quotes = services(tmp_path)
    quarantined = intake.capture(
        supplier_id="supplier-a",
        supplier_rfq_package_id="package-a",
        content=b"not an executable quote",
        filename="quote.exe",
        declared_mime="text/plain",
        idempotency_key="bad-extension",
        received_at=NOW,
        quote_received_at=NOW,
        submitted_by="operator",
    )
    assert quarantined.quarantine_status == "quarantined:extension_not_allowed"

    usd = snapshot(
        quotes,
        capture(intake, "supplier-a", "package-a", b"future fx", "future-fx.eml"),
        "10",
        "EXW",
        "Factory",
        until=NOW + timedelta(days=1),
        currency="USD",
    )
    future_reader = InMemoryFxSnapshotReader(
        (
            FxSnapshotReaderRecord(
                fx_snapshot_id="future-fx",
                snapshot=FxSnapshot(
                    from_currency="USD",
                    to_currency="CNY",
                    rate="7.2",
                    observed_at=NOW + timedelta(days=1),
                    source="future fixture",
                    evidence=EvidenceLocator(artifact_id="future-fx"),
                ),
            ),
        )
    )
    future_run = QuoteNormalizationService(PintUnitConverter(), future_reader).normalize(
        (usd,), policy=policy(), as_of=NOW, fx_snapshot_id="future-fx"
    )
    assert "FX_SNAPSHOT_AFTER_AS_OF" in future_run.issues
    assert future_run.normalized_lines[0].currency_converted_price is None
    with pytest.raises(ValueError, match="as_of"):
        QuoteComparisonService(repository).compare(
            (usd,), normalization=future_run, as_of=NOW + timedelta(seconds=1)
        )
    forged = usd.model_copy(update={"supplier_id": "forged-supplier"})
    with pytest.raises(ValueError, match="repository-saved"):
        QuoteComparisonService(repository).compare(
            (forged,), normalization=future_run, as_of=NOW
        )

    future_snapshot = snapshot(
        quotes,
        capture(intake, "supplier-b", "package-b", b"future quote", "future.eml"),
        "20",
        "EXW",
        "Factory B",
        until=NOW + timedelta(days=3),
        confirmed_at=NOW + timedelta(days=1),
    )
    with pytest.raises(ValueError, match="after as_of"):
        QuoteNormalizationService(PintUnitConverter()).normalize(
            (future_snapshot,), policy=policy(), as_of=NOW
        )
    normalized_in_future = QuoteNormalizationService(PintUnitConverter()).normalize(
        (future_snapshot,), policy=policy(), as_of=NOW + timedelta(days=1)
    )
    forged_past_run = normalized_in_future.model_copy(update={"as_of": NOW})
    with pytest.raises(ValueError, match="after as_of"):
        QuoteComparisonService(repository).compare(
            (future_snapshot,), normalization=forged_past_run, as_of=NOW
        )


def test_ep06_unknown_boundary_and_exw_fob_are_issues_not_zero_or_a_commercial_choice(
    tmp_path,
) -> None:
    repository, intake, quotes = services(tmp_path)
    a = snapshot(
        quotes,
        capture(intake, "supplier-a", "package-a", b"A", "a.pdf"),
        "250",
        "EXW",
        "Factory A",
        until=NOW + timedelta(days=1),
    )
    b = snapshot(
        quotes,
        capture(intake, "supplier-b", "package-b", b"B", "b.eml"),
        "35",
        "FOB",
        "Shanghai",
        until=NOW + timedelta(days=1),
    )
    run = QuoteNormalizationService(PintUnitConverter()).normalize(
        (a, b), policy=policy(), as_of=NOW
    )
    matrix, issues = QuoteComparisonService(repository).compare(
        (a, b), normalization=run, as_of=NOW
    )
    assert matrix.disposition == "not_comparable"
    assert any(item.code == "INCOTERM_CODE_MISMATCH" for item in issues)
    assert any(item[1] is ComponentType.PACKAGING for item in matrix.unresolved_cost_components)
    dumped = matrix.model_dump()
    assert (
        "winner" not in dumped and "selected_supplier_id" not in dumped and "approved" not in dumped
    )


def test_ep06_decimal_normalization_divides_a_100_piece_price_basis(tmp_path) -> None:
    _repository, intake, quotes = services(tmp_path)
    value = snapshot(
        quotes,
        capture(intake, "supplier-a", "package-a", b"100 pieces", "basis.pdf"),
        "1000",
        "EXW",
        "Factory A",
        until=NOW + timedelta(days=1),
        basis=Decimal("100"),
    )
    run = QuoteNormalizationService(PintUnitConverter()).normalize(
        (value,), policy=policy(), as_of=NOW
    )
    assert run.normalized_lines[0].calculation_value == Decimal("10")
    assert run.normalized_lines[0].conversion_trace is not None
    assert run.normalized_lines[0].conversion_trace.unit_factor == Decimal("0.01")


def test_ep06_reciprocal_fx_requires_explicit_policy_and_records_trace(tmp_path) -> None:
    _repository, intake, quotes = services(tmp_path)
    value = snapshot(
        quotes,
        capture(intake, "supplier-a", "package-a", b"USD quote", "usd.eml"),
        "10",
        "EXW",
        "Factory A",
        until=NOW + timedelta(days=1),
        currency="USD",
    )
    reader = InMemoryFxSnapshotReader(
        (
            FxSnapshotReaderRecord(
                fx_snapshot_id="cny-usd",
                snapshot=FxSnapshot(
                    from_currency="CNY",
                    to_currency="USD",
                    rate=Decimal("0.14"),
                    observed_at=NOW,
                    source="fixture",
                    evidence=EvidenceLocator(artifact_id="fx", block_ids=("fx-1",)),
                ),
            ),
        )
    )
    normalizer = QuoteNormalizationService(PintUnitConverter(), reader)
    rejected = normalizer.normalize((value,), policy=policy(), as_of=NOW, fx_snapshot_id="cny-usd")
    assert rejected.normalized_lines[0].currency_converted_price is None
    reciprocal_policy = policy().model_copy(update={"allow_reciprocal_fx": True})
    reciprocal_policy = reciprocal_policy.model_copy(
        update={"config_fingerprint": policy_content_fingerprint(reciprocal_policy)}
    )
    allowed = normalizer.normalize(
        (value,),
        policy=reciprocal_policy,
        as_of=NOW,
        fx_snapshot_id="cny-usd",
    )
    trace = allowed.normalized_lines[0].conversion_trace
    assert trace is not None and trace.direction == "reciprocal"
    assert allowed.normalized_lines[0].currency_converted_price is not None


def test_ep06_ambiguous_line_mapping_requires_its_own_human_decision(tmp_path) -> None:
    repository, intake, quotes = services(tmp_path)
    receipt = capture(intake, "supplier-a", "package-a", b"ambiguous line", "line.pdf")
    evidence = provenance(receipt.artifact_refs[0])
    candidate = SupplierQuoteLineCandidate(
        candidate_line_id="line-map-1",
        quote_id=receipt.quote_id,
        supplier_line_ref="supplier-line-1",
        proposed_rfq_line_id="line-1",
        mapping_confidence=Decimal("0.4"),
        provenance_refs=(evidence,),
    )
    SupplierQuoteExtractionService(repository).project_candidates(
        receipt.quote_id, lines=(candidate,)
    )
    repository.save_decision(
        SupplierQuoteDecision(
            decision_id="manual-price",
            quote_id=receipt.quote_id,
            field_path="lines[0].unit_price",
            decision=QuoteDecisionAction.ENTER_MANUAL_VALUE,
            typed_value={"amount": "10", "currency": "CNY", "basis": "1"},
            rationale="price transcribed",
            actor_id="reviewer",
            provenance_ref=evidence,
        )
    )
    line = ConfirmedQuoteLine(
        rfq_line_id="line-1",
        supplier_line_ref="supplier-line-1",
        quantity_basis=QuantityBasis(amount=Decimal("1"), unit="piece"),
        product_price=component("10").model_copy(update={"provenance_ref": evidence}),
        provenance_refs=(evidence,),
    )
    terms = QuoteTerm(incoterm=Incoterm(code="EXW", rules_version="2020", named_place="Factory"))
    with pytest.raises(ValueError, match="all projected quote candidates"):
        quotes.confirm_snapshot(
            receipt.quote_id,
            supplier_profile_version_id="supplier-a-profile-v1",
            product_spec_version_id="spec-v1",
            lines=(line,),
            terms=terms,
            actor_id="reviewer",
        )
    SupplierQuoteReviewService(repository).record_decision(
        SupplierQuoteDecision(
            decision_id="accept-line-map",
            quote_id=receipt.quote_id,
            field_path="lines[0].rfq_line_id",
            candidate_id=candidate.candidate_line_id,
            decision=QuoteDecisionAction.ACCEPT,
            rationale="reviewer matched the supplied line",
            actor_id="reviewer",
            provenance_ref=evidence,
        )
    )
    with pytest.raises(ValueError, match="commercial terms"):
        quotes.confirm_snapshot(
            receipt.quote_id,
            supplier_profile_version_id="supplier-a-profile-v1",
            product_spec_version_id="spec-v1",
            lines=(line,),
            terms=terms,
            actor_id="reviewer",
        )


def test_ep06_separately_quoted_component_is_resolved_when_its_amount_is_explicit(tmp_path) -> None:
    repository, intake, quotes = services(tmp_path)
    base = snapshot(
        quotes,
        capture(intake, "supplier-a", "package-a", b"packing stated", "packing.pdf"),
        "10",
        "EXW",
        "Factory A",
        until=NOW + timedelta(days=1),
    )
    separately_priced = base.model_copy(
        update={
            "confirmed_terms": base.confirmed_terms.model_copy(
                update={
                    "components": (
                        PriceComponent(
                            component_type=ComponentType.PACKAGING,
                            inclusion_status=InclusionStatus.QUOTED_SEPARATELY,
                            amount=MonetaryAmount(amount=Decimal("2"), currency="CNY"),
                            quantity_basis=QuantityBasis(amount=Decimal("1"), unit="piece"),
                            provenance_ref=provenance(),
                        ),
                    )
                }
            )
        }
    )
    run = QuoteNormalizationService(PintUnitConverter()).normalize(
        (separately_priced,), policy=policy(), as_of=NOW
    )
    matrix, _issues = analyze_snapshots((separately_priced,), normalization=run, as_of=NOW)
    assert not any(
        component_type is ComponentType.PACKAGING
        for _snapshot_id, component_type, _status in matrix.unresolved_cost_components
    )


def test_ep06_separately_quoted_component_without_amount_remains_unresolved(tmp_path) -> None:
    repository, intake, quotes = services(tmp_path)
    base = snapshot(
        quotes,
        capture(intake, "supplier-a", "package-a", b"packing extra", "packing.eml"),
        "10",
        "EXW",
        "Factory A",
        until=NOW + timedelta(days=1),
    )
    amount_unknown = base.model_copy(
        update={
            "confirmed_terms": base.confirmed_terms.model_copy(
                update={
                    "components": (
                        PriceComponent(
                            component_type=ComponentType.PACKAGING,
                            inclusion_status=InclusionStatus.QUOTED_SEPARATELY,
                            provenance_ref=provenance(),
                        ),
                    )
                }
            )
        }
    )
    run = QuoteNormalizationService(PintUnitConverter()).normalize(
        (amount_unknown,), policy=policy(), as_of=NOW
    )
    matrix, _issues = analyze_snapshots((amount_unknown,), normalization=run, as_of=NOW)
    assert (
        amount_unknown.supplier_quote_snapshot_id,
        ComponentType.PACKAGING,
        InclusionStatus.QUOTED_SEPARATELY,
    ) in matrix.unresolved_cost_components


def test_ep06_incoterm_policy_controls_named_place_and_required_components(tmp_path) -> None:
    repository, intake, quotes = services(tmp_path)
    a = snapshot(
        quotes,
        capture(intake, "supplier-a", "package-a", b"a", "a.eml"),
        "10",
        "EXW",
        "Factory A",
        until=NOW + timedelta(days=1),
    )
    b = snapshot(
        quotes,
        capture(intake, "supplier-b", "package-b", b"b", "b.eml"),
        "10",
        "EXW",
        "Factory B",
        until=NOW + timedelta(days=1),
    )
    strict = policy()
    strict_run = QuoteNormalizationService(PintUnitConverter()).normalize(
        (a, b), policy=strict, as_of=NOW
    )
    _matrix, strict_issues = QuoteComparisonService(repository).compare(
        (a, b), normalization=strict_run, as_of=NOW
    )
    assert any(issue.code == "INCOTERM_NAMED_PLACE_MISMATCH" for issue in strict_issues)
    same_term = strict.model_copy(update={"incoterm_comparison_policy": "same_term"})
    same_term = same_term.model_copy(
        update={"config_fingerprint": policy_content_fingerprint(same_term)}
    )
    relaxed_run = QuoteNormalizationService(PintUnitConverter()).normalize(
        (a, b), policy=same_term, as_of=NOW
    )
    _matrix, relaxed_issues = QuoteComparisonService(repository).compare(
        (a, b), normalization=relaxed_run, as_of=NOW
    )
    assert not any(issue.code == "INCOTERM_NAMED_PLACE_MISMATCH" for issue in relaxed_issues)
