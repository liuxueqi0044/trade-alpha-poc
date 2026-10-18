"""Deterministic claim extraction and the schema/evidence trust boundary."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import ValidationError

from .models import CandidateClaim, ClaimStatus, DocumentBlock, SchemaGateResult, stable_id

GateStatus = Literal["proposed", "validated_rule", "accepted"]

_DATE = re.compile(r"\b(?:19|20)\d{2}[-/.](?:0[1-9]|1[0-2])[-/.](?:0[1-9]|[12]\d|3[01])\b")
_PERCENTAGE = re.compile(r"\b\d+(?:\.\d+)?%")
_AMOUNT = re.compile(r"\b(?:USD|EUR|CNY|RMB|GBP)\s?\d+(?:[,.]\d+)*\b", re.I)
_HS_CODE = re.compile(r"\bHS(?:\s+code)?\s*[:#-]?\s*(\d{6})\b", re.I)


def gate_claim(
    claim: CandidateClaim,
    blocks: Iterable[DocumentBlock],
    *,
    target_status: GateStatus = "proposed",
) -> SchemaGateResult:
    """Validate every evidence reference before a claim can change state."""

    by_id = {block.block_id: block for block in blocks}
    if not claim.evidence_block_ids:
        return SchemaGateResult(accepted=False, reason="missing_evidence_block")
    referenced = [by_id.get(block_id) for block_id in claim.evidence_block_ids]
    if any(block is None for block in referenced):
        return SchemaGateResult(accepted=False, reason="evidence_block_not_found")
    concrete = [block for block in referenced if block is not None]
    if any(block.artifact_id != claim.artifact_id for block in concrete):
        return SchemaGateResult(accepted=False, reason="evidence_artifact_mismatch")
    if any(not block.has_evidence_locator() for block in concrete):
        return SchemaGateResult(accepted=False, reason="evidence_locator_invalid")
    return SchemaGateResult(
        accepted=True,
        claim=claim.model_copy(update={"status": target_status}),
        reason="schema_evidence_and_locator_valid",
    )


def parse_llm_claim(
    payload: object,
    *,
    artifact_id: str,
    blocks: Iterable[DocumentBlock],
    extractor_version: str,
    prompt_version: str,
    schema_version: str,
) -> SchemaGateResult:
    """Treat model output as hostile data and keep its state proposed."""

    if not isinstance(payload, Mapping):
        return SchemaGateResult(accepted=False, reason="llm_output_not_an_object")
    controlled: dict[str, Any] = dict(payload)
    if controlled.get("artifact_id") != artifact_id:
        return SchemaGateResult(accepted=False, reason="llm_artifact_identity_mismatch")
    if controlled.get("extractor_type") not in {None, "llm"}:
        return SchemaGateResult(accepted=False, reason="llm_extractor_identity_mismatch")
    controlled.pop("created_at", None)
    controlled.update(
        {
            "artifact_id": artifact_id,
            "extractor_type": "llm",
            "extractor_version": extractor_version,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "status": "proposed",
        }
    )
    try:
        claim = CandidateClaim.model_validate(controlled)
    except (ValidationError, TypeError, ValueError):
        return SchemaGateResult(accepted=False, reason="llm_output_rejected_schema")
    return gate_claim(claim, blocks, target_status="proposed")


def _claim(
    block: DocumentBlock,
    *,
    predicate: str,
    object_value: str,
    object_type: str,
    occurrence: int,
) -> CandidateClaim:
    return CandidateClaim(
        claim_id=stable_id(
            "rule-claim", block.block_id, predicate, object_value, str(occurrence)
        ),
        subject_mention=block.text[:160],
        predicate=predicate,
        object_value=object_value,
        object_type=object_type,
        artifact_id=block.artifact_id,
        evidence_block_ids=(block.block_id,),
        extractor_type="rule",
        extractor_version="deterministic-rules/1.0.0",
        confidence=block.confidence,
        status="proposed",
    )


def rule_extract(blocks: Iterable[DocumentBlock]) -> tuple[CandidateClaim, ...]:
    candidates: list[CandidateClaim] = []
    for block in blocks:
        patterns: tuple[tuple[re.Pattern[str], str, str], ...] = (
            (_DATE, "document.date", "date"),
            (_PERCENTAGE, "document.percentage", "percentage"),
            (_AMOUNT, "document.amount", "amount"),
            (_HS_CODE, "product.hs_code", "hs_code"),
        )
        for pattern, predicate, object_type in patterns:
            for occurrence, match in enumerate(pattern.finditer(block.text)):
                value = match.group(1) if pattern is _HS_CODE else match.group(0)
                if object_type == "date":
                    value = value.replace("/", "-").replace(".", "-")
                claim = _claim(
                    block,
                    predicate=predicate,
                    object_value=value,
                    object_type=object_type,
                    occurrence=occurrence,
                )
                gated = gate_claim(claim, (block,), target_status="validated_rule")
                if gated.accepted and gated.claim is not None:
                    candidates.append(gated.claim)
    return tuple(candidates)


def claim_status(claim: CandidateClaim) -> ClaimStatus:
    return claim.status
