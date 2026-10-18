"""Append-only candidate revisions and controlled M11 state transitions."""

from __future__ import annotations

from trade_alpha.contracts.common import utc_now

from .models import (
    CandidateState,
    SourceCandidate,
    candidate_metadata,
    metadata_hash,
)

_ALLOWED_TRANSITIONS: dict[CandidateState, frozenset[CandidateState]] = {
    CandidateState.DISCOVERED: frozenset(
        {CandidateState.PROBED, CandidateState.QUARANTINED, CandidateState.STALE}
    ),
    CandidateState.PROBED: frozenset(
        {CandidateState.POLICY_PENDING, CandidateState.QUARANTINED, CandidateState.STALE}
    ),
    CandidateState.POLICY_PENDING: frozenset(
        {CandidateState.APPROVED, CandidateState.REJECTED, CandidateState.QUARANTINED}
    ),
    CandidateState.APPROVED: frozenset(
        {CandidateState.CONNECTOR_DRAFTED, CandidateState.POLICY_PENDING, CandidateState.REJECTED}
    ),
    CandidateState.CONNECTOR_DRAFTED: frozenset(
        {CandidateState.GOLDEN_VALIDATED, CandidateState.POLICY_PENDING, CandidateState.QUARANTINED}
    ),
    CandidateState.GOLDEN_VALIDATED: frozenset(
        {CandidateState.ENABLED, CandidateState.POLICY_PENDING, CandidateState.QUARANTINED}
    ),
    CandidateState.ENABLED: frozenset(
        {CandidateState.POLICY_PENDING, CandidateState.STALE, CandidateState.QUARANTINED}
    ),
    CandidateState.REJECTED: frozenset(),
    CandidateState.QUARANTINED: frozenset({CandidateState.PROBED, CandidateState.REJECTED}),
    CandidateState.STALE: frozenset({CandidateState.PROBED, CandidateState.REJECTED}),
}


def transition(candidate: SourceCandidate, target: CandidateState) -> SourceCandidate:
    if target not in _ALLOWED_TRANSITIONS[candidate.state]:
        raise ValueError(f"illegal candidate transition: {candidate.state}->{target}")
    return candidate.model_copy(
        update={"state": target, "revision": candidate.revision + 1, "last_seen_at": utc_now()}
    )


class CandidateRegistry:
    def __init__(self) -> None:
        self._revisions: dict[str, list[SourceCandidate]] = {}
        self._failures: dict[str, int] = {}

    def upsert(self, candidate: SourceCandidate) -> SourceCandidate:
        revisions = self._revisions.setdefault(candidate.candidate_id, [])
        if not revisions:
            revisions.append(candidate)
            return candidate
        previous = revisions[-1]
        evidence = tuple(
            {
                item.model_dump_json(): item
                for item in (*previous.discovery_evidence, *candidate.discovery_evidence)
            }.values()
        )
        distributions = tuple(
            {
                item.model_dump_json(): item
                for item in (*previous.distributions, *candidate.distributions)
            }.values()
        )
        merged = candidate.model_copy(
            update={
                "candidate_id": previous.candidate_id,
                "first_seen_at": previous.first_seen_at,
                "last_seen_at": candidate.last_seen_at,
                "discovery_evidence": evidence,
                "distributions": distributions,
                "state": previous.state,
                "revision": previous.revision + 1,
            }
        )
        new_hash = metadata_hash(candidate_metadata(merged))
        material_change = new_hash != previous.metadata_hash
        if material_change and previous.state in {
            CandidateState.APPROVED,
            CandidateState.CONNECTOR_DRAFTED,
            CandidateState.GOLDEN_VALIDATED,
            CandidateState.ENABLED,
        }:
            merged = merged.model_copy(update={"state": CandidateState.POLICY_PENDING})
        merged = merged.model_copy(update={"metadata_hash": new_hash})
        revisions.append(merged)
        return merged

    def latest(self, candidate_id: str) -> SourceCandidate:
        try:
            return self._revisions[candidate_id][-1]
        except KeyError as error:
            raise KeyError(f"unknown source candidate: {candidate_id}") from error

    def history(self, candidate_id: str) -> tuple[SourceCandidate, ...]:
        try:
            return tuple(self._revisions[candidate_id])
        except KeyError as error:
            raise KeyError(f"unknown source candidate: {candidate_id}") from error

    def move(self, candidate_id: str, target: CandidateState) -> SourceCandidate:
        updated = transition(self.latest(candidate_id), target)
        self._revisions[candidate_id].append(updated)
        if target is CandidateState.PROBED:
            self._failures[candidate_id] = 0
        return updated

    def record_probe_failure(self, candidate_id: str, *, stale_after: int = 3) -> SourceCandidate:
        if stale_after < 1:
            raise ValueError("stale_after must be positive")
        candidate = self.latest(candidate_id)
        failures = self._failures.get(candidate_id, 0) + 1
        self._failures[candidate_id] = failures
        if failures < stale_after:
            return candidate
        if CandidateState.STALE not in _ALLOWED_TRANSITIONS[candidate.state]:
            raise ValueError(f"candidate cannot become stale from {candidate.state}")
        return self.move(candidate_id, CandidateState.STALE)
