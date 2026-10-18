"""M03 KnowledgeStore adapter for EP-03 claim projection."""

# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime

from trade_alpha.contracts.common import require_utc
from trade_alpha.execution.rfq.models import RfqFieldCandidate
from trade_alpha.knowledge import Claim, KnowledgeStore, ReviewStatus
from trade_alpha.knowledge.models import deterministic_id


class KnowledgeClaimAdapter:
    def __init__(self, store: KnowledgeStore) -> None:
        self._store = store

    def claims_for(self, subject_ref: str, as_of: datetime) -> tuple[Claim, ...]:
        return self._store.claims_for(subject_ref, require_utc(as_of))

    def add_candidate(
        self, spec_id: str, candidate: RfqFieldCandidate, *, observed_at: datetime
    ) -> Claim:
        """Map an EP-02 candidate to an M03 pending Claim; acceptance is not confirmation."""

        if candidate.status == "rejected_schema":
            raise ValueError("rejected_schema candidates must not enter M03")
        moment = require_utc(observed_at)
        claim = Claim(
            claim_id=deterministic_id("spec-candidate", spec_id, candidate.candidate_id),
            subject_ref=f"product-spec:{spec_id}",
            predicate=f"spec.{candidate.field_name}",
            object_value={"raw": candidate.value, "typed": candidate.value, "canonical": None},
            source_id=f"{candidate.extractor_type}@{candidate.extractor_version}",
            artifact_id=candidate.artifact_id,
            evidence_block_ids=candidate.evidence_block_ids or (candidate.block_id,),
            observed_at=moment,
            confidence=candidate.confidence,
            status=ReviewStatus.PENDING,
            decision_basis="model",
            system_from=moment,
        )
        self._store.add_claim(claim)
        return claim
