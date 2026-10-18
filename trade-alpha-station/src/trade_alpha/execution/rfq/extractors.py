"""Candidate extraction extension point.

Implementations receive already parsed, untrusted evidence.  They cannot
approve candidates and the service validates every returned locator before it
is persisted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal, Protocol, cast

from trade_alpha.contracts.common import EvidenceLocator
from trade_alpha.extraction import DocumentBlock, ExtractionRun

from .models import RfqFieldCandidate, fingerprint, stable_id


class CandidateExtractor(Protocol):
    name: str
    version: str

    def extract(
        self,
        *,
        rfq_id: str,
        blocks: Sequence[DocumentBlock],
        runs: Sequence[ExtractionRun],
    ) -> Iterable[Any]: ...


class NoopCandidateExtractor:
    """Convenient default extension which contributes no extra fields."""

    name = "none"
    version = "1.0.0"

    def extract(
        self,
        *,
        rfq_id: str,
        blocks: Sequence[DocumentBlock],
        runs: Sequence[ExtractionRun],
    ) -> Iterable[Any]:
        del rfq_id, blocks, runs
        return ()


class FeDry01CandidateExtractor:
    """Deterministic FE-DRY-01 field projection from explicitly labelled text."""

    name = "fe-dry-01"
    version = "1.0.0"
    _ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("dust_toxicity", ("dust toxicity", "toxic dust")),
        ("dust_combustibility", ("dust combustibility", "combustible dust")),
        ("atex_required", ("atex", "atex required", "explosion proof", "explosion-proof")),
        ("oem_model_reference", ("oem model", "oem", "model", "mpn", "part number")),
        ("photo_reference", ("photo", "photo reference", "image reference")),
        ("drawing_reference", ("drawing", "drawing reference")),
        ("application_scope", ("application scope", "intended use", "application", "use")),
        ("dimensions", ("dimensions", "dimension")),
        ("outer_diameter", ("outer diameter", "outer dia", "od")),
        ("inner_diameter", ("inner diameter", "inner dia", "id")),
        ("length", ("length",)),
        ("first_order_quantity", ("first order quantity", "order quantity", "quantity", "qty")),
        ("tier_quantities", ("tier quantities", "tier quantity", "forecast quantity")),
        ("filter_material", ("filter material", "material", "grade", "composition")),
        (
            "performance_requirement",
            (
                "performance",
                "performance requirement",
                "voltage",
                "power",
                "capacity",
                "pressure",
                "temperature",
                "tolerance",
                "ip rating",
                "speed",
            ),
        ),
        (
            "packaging_requirement",
            ("packaging", "packaging requirement", "package", "pack", "carton", "pallet"),
        ),
    )
    _LINE = re.compile(r"^\s*([A-Za-z][A-Za-z _/-]{1,32})\s*[:=-]\s*(.*?)\s*$")
    _AMBIGUOUS = re.compile(
        r"\b(?:tbd|tbc|unknown|unspecified|unclear|approx(?:imate)?|or)\b", re.I
    )
    _HARD_CUE = re.compile(
        r"\b(?:must\s+not|not\s+allowed|no|without|exclude|excluding|forbidden|prohibited)\b",
        re.I,
    )
    _MEASURE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*(mm|millimet(?:er|re)s?|in|inch(?:es)?)\b", re.I)
    _QUANTITY = re.compile(r"([-+]?\d+(?:[,.]\d+)?)\s*([A-Za-z]+)?")
    _SCOPE_ALLOWED = re.compile(r"\bgeneral\s+industrial\s+dry\s+dust\b", re.I)
    _SCOPE_HARD = re.compile(
        r"\b(medical|food|respiratory|fire|pressure|explosion[- ]proof)\b", re.I
    )

    @classmethod
    def _field_for_label(cls, label: str) -> str | None:
        value = " ".join(label.strip().casefold().replace("-", " ").split())
        for field, aliases in cls._ALIASES:
            if value in aliases:
                return field
        return None

    @staticmethod
    def _unit(value: str) -> str:
        return {
            "pcs": "piece",
            "pieces": "piece",
            "pc": "piece",
            "ea": "piece",
            "millimeter": "mm",
            "millimeters": "mm",
            "millimetre": "mm",
            "millimetres": "mm",
            "in": "inch",
            "inches": "inch",
        }.get(value.casefold(), value)

    @classmethod
    def _values_for(cls, field: str, value: str) -> list[tuple[str, object, str, bool]]:
        if field in {"dust_toxicity", "dust_combustibility", "atex_required"}:
            normalized = value.strip().casefold()
            if normalized in {"true", "yes", "confirmed"}:
                return [(field, True, "boolean", False)]
            if normalized in {"false", "no"}:
                return [(field, False, "boolean", False)]
            # Unknown is a valid M03 boolean projection, but remains reviewable.
            return [(field, "unknown", "boolean", True)]
        if field in {"outer_diameter", "inner_diameter", "length"}:
            match = cls._MEASURE.search(value)
            if match:
                unit = cls._unit(match.group(2).casefold())
                return [(field, f"{match.group(1)} {unit}", "quantity", False)]
            return [(field, value, "quantity", True)]
        if field in {"first_order_quantity", "tier_quantities"}:
            match = cls._QUANTITY.search(value)
            if match:
                unit = cls._unit(match.group(2) or "piece")
                normalized = f"{match.group(1).replace(',', '')} {unit}"
                return [(field, normalized, "quantity", not bool(match.group(2)))]
            return [(field, value, "quantity", True)]
        if field == "application_scope":
            normalized = " ".join(value.casefold().replace("-", " ").split())
            if cls._SCOPE_ALLOWED.fullmatch(normalized):
                return [(field, "general_industrial_dry_dust", "text", False)]
            hard_match = cls._SCOPE_HARD.search(normalized)
            if hard_match:
                token = hard_match.group(1).replace(" ", "_").replace("-", "_")
                return [(field, token, "text", not bool(cls._SCOPE_HARD.fullmatch(normalized)))]
            return [(field, "unknown", "text", True)]
        return [(field, value, "text", False)]

    def extract(
        self,
        *,
        rfq_id: str,
        blocks: Sequence[DocumentBlock],
        runs: Sequence[ExtractionRun],
    ) -> Iterable[Any]:
        del runs
        extraction_fp = fingerprint(
            {
                "extractor": self.name,
                "version": self.version,
                "artifacts": sorted({b.artifact_id for b in blocks}),
            }
        )
        for block in blocks:
            for line_number, line in enumerate(block.text.splitlines() or [block.text]):
                match = self._LINE.match(line)
                label = match.group(1) if match else ""
                field = self._field_for_label(label) if match else None
                value = match.group(2).strip() if match else ""
                if field == "oem_model_reference" and label.strip().casefold() == "oem":
                    field = "oem_model_reference"
                if field is None and self._HARD_CUE.search(line):
                    continue
                if field is None:
                    continue
                # ``Dimensions: OD 10 mm, ID 5 mm`` yields three independent
                # profile keys when component labels are present.
                values = self._values_for(field, value)
                if field == "dimensions":
                    values = []
                    for subfield, aliases in self._ALIASES:
                        if subfield not in {"outer_diameter", "inner_diameter", "length"}:
                            continue
                        submatch = re.search(
                            rf"(?:{'|'.join(map(re.escape, aliases))})\s*[:=]?\s*([^,;]+)",
                            value,
                            re.I,
                        )
                        if submatch:
                            values.extend(self._values_for(subfield, submatch.group(1)))
                for emitted_field, emitted_value, value_type, ambiguous in values:
                    status: Literal["proposed", "needs_review", "rejected_schema"] = (
                        "needs_review"
                        if (
                            ambiguous
                            or block.low_confidence
                            or self._AMBIGUOUS.search(str(emitted_value))
                        )
                        else "proposed"
                    )
                    candidate_id = stable_id(
                        "fe-dry-01", rfq_id, block.block_id, emitted_field, str(line_number)
                    )
                    yield RfqFieldCandidate(
                        candidate_id=candidate_id,
                        rfq_id=rfq_id,
                        field_name=emitted_field,
                        value=cast(str | int | float | bool, emitted_value),
                        value_type=value_type,
                        artifact_id=block.artifact_id,
                        block_id=block.block_id,
                        evidence_block_ids=(block.block_id,),
                        locator=EvidenceLocator(
                            artifact_id=block.artifact_id,
                            block_ids=(block.block_id,),
                            page_number=block.page_number,
                            bbox=block.bbox,
                            start_ms=block.start_ms,
                            end_ms=block.end_ms,
                            quality=block.locator_quality,
                        ),
                        extraction_run_id=block.extraction_run_id,
                        extractor_type="rule",
                        extractor_version=self.version,
                        extraction_fingerprint=extraction_fp,
                        confidence=block.confidence,
                        status=status,
                    )
