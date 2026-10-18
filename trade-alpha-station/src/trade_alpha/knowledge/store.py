"""Append-only in-memory repository for PoC knowledge records."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from trade_alpha.contracts.common import require_utc

from .models import (
    Claim,
    Entity,
    EntityAlias,
    EntityLink,
    LinkState,
    ReconciliationImpact,
    Relationship,
    ReviewStatus,
    SourceRecord,
    VersionedModel,
)
from .privacy import Authorization, redact_entity


def _current_at(record: VersionedModel, as_of: datetime) -> bool:
    start = record.system_from
    end = record.system_to
    return start <= as_of and (end is None or as_of < end)


def _valid_at(record: Relationship, as_of: datetime) -> bool:
    return (record.valid_from is None or record.valid_from <= as_of) and (
        record.valid_to is None or as_of < record.valid_to
    )


class KnowledgeStore:
    """A deterministic repository that does not mutate source facts.

    Production storage can map these append-only methods to bitemporal tables.
    The PoC makes rewinds explicit and therefore easy to exercise in tests.
    """

    def __init__(self, source_priority: dict[str, int] | None = None) -> None:
        self.source_priority = source_priority or {}
        self._entities: list[Entity] = []
        self._source_records: list[SourceRecord] = []
        self._aliases: list[EntityAlias] = []
        self._claims: list[Claim] = []
        self._links: list[EntityLink] = []
        self._relationships: list[Relationship] = []

    def add_entity(self, entity: Entity) -> None:
        self._entities.append(entity)

    def add_source_record(self, source_record: SourceRecord) -> None:
        self._source_records.append(source_record)

    def add_alias(self, alias: EntityAlias) -> None:
        self._aliases.append(alias)

    def add_claim(self, claim: Claim) -> None:
        self._claims.append(claim)

    def add_link(self, link: EntityLink) -> None:
        if not any(
            record.source_record_id == link.source_record_id for record in self._source_records
        ):
            raise ValueError(f"link references unknown source record: {link.source_record_id}")
        self._links.append(link)

    def source_records_at(self, as_of: datetime) -> tuple[SourceRecord, ...]:
        moment = require_utc(as_of)
        return tuple(record for record in self._source_records if _current_at(record, moment))

    def source_records_for_entity(
        self, entity_id: str, as_of: datetime
    ) -> tuple[SourceRecord, ...]:
        """Return original source names/payload refs linked to an entity at a point in time."""
        moment = require_utc(as_of)
        linked_ids = {
            link.source_record_id
            for link in self.links_at(moment, (LinkState.AUTO_LINKED,))
            if link.entity_id == entity_id
        }
        return tuple(
            record
            for record in self.source_records_at(moment)
            if record.source_record_id in linked_ids
        )

    def add_relationship(self, relationship: Relationship) -> None:
        if relationship.review_status is ReviewStatus.ACCEPTED:
            known_claims = {claim.claim_id for claim in self._claims}
            missing = set(relationship.source_claim_ids) - known_claims
            if missing:
                raise ValueError(f"relationship references unknown claims: {sorted(missing)}")
        self._relationships.append(relationship)

    def entities_at(
        self, as_of: datetime, authorization: Authorization | None = None
    ) -> tuple[Entity, ...]:
        moment = require_utc(as_of)
        auth = authorization or Authorization(principal="anonymous")
        return tuple(
            redact_entity(record, auth) for record in self._entities if _current_at(record, moment)
        )

    def aliases_for(self, entity_id: str, as_of: datetime) -> tuple[EntityAlias, ...]:
        moment = require_utc(as_of)
        return tuple(
            alias
            for alias in self._aliases
            if alias.entity_id == entity_id and _current_at(alias, moment)
        )

    def claims_for(self, subject_ref: str, as_of: datetime) -> tuple[Claim, ...]:
        moment = require_utc(as_of)
        return tuple(
            claim
            for claim in self._claims
            if claim.subject_ref == subject_ref and _current_at(claim, moment)
        )

    def current_claim_view(self, subject_ref: str, as_of: datetime) -> dict[str, Claim]:
        """Select one claim per predicate while retaining all conflicts in storage."""
        selected: dict[str, Claim] = {}
        for claim in self.claims_for(subject_ref, as_of):
            if claim.status is not ReviewStatus.ACCEPTED:
                continue
            current = selected.get(claim.predicate)
            if current is None or self._claim_rank(claim) > self._claim_rank(current):
                selected[claim.predicate] = claim
        return selected

    def conflict_claims(
        self, subject_ref: str, predicate: str, as_of: datetime
    ) -> tuple[Claim, ...]:
        return tuple(
            claim
            for claim in self.claims_for(subject_ref, as_of)
            if claim.predicate == predicate and claim.status is ReviewStatus.ACCEPTED
        )

    def _claim_rank(self, claim: Claim) -> tuple[int, datetime, datetime, float, str]:
        # Explicit source priority wins, followed by source recency and confidence;
        # the claim id makes ties stable across process order.
        return (
            self.source_priority.get(claim.source_id, 0),
            claim.published_at or claim.observed_at,
            claim.observed_at,
            float(claim.confidence),
            claim.claim_id,
        )

    def links_at(
        self, as_of: datetime, states: Iterable[LinkState] | None = None
    ) -> tuple[EntityLink, ...]:
        moment = require_utc(as_of)
        wanted = set(states) if states is not None else None
        return tuple(
            link
            for link in self._links
            if _current_at(link, moment) and (wanted is None or link.state in wanted)
        )

    def relationships_at(self, as_of: datetime) -> tuple[Relationship, ...]:
        moment = require_utc(as_of)
        return tuple(
            relationship
            for relationship in self._relationships
            if _current_at(relationship, moment) and _valid_at(relationship, moment)
        )

    def accepted_relationships_at(self, as_of: datetime) -> tuple[Relationship, ...]:
        """Relationships safe for downstream facts/graph materialization."""
        return tuple(
            relationship
            for relationship in self.relationships_at(as_of)
            if relationship.review_status is ReviewStatus.ACCEPTED
        )

    def revoke_link(self, link_id: str, revoked_at: datetime, reason: str) -> ReconciliationImpact:
        """Close a decision and mark its active entity relationships for review.

        The method returns deterministic invalidation keys for downstream feature
        and signal materializers; it does not delete historical versions.
        """
        moment = require_utc(revoked_at)
        original = next(
            (link for link in self._links if link.link_id == link_id and link.system_to is None),
            None,
        )
        if original is None:
            raise KeyError(f"no active link {link_id}")
        if moment <= original.system_from:
            raise ValueError("revocation must occur after the original decision")
        self._links.remove(original)
        self._links.append(original.model_copy(update={"system_to": moment}))
        revoked = EntityLink(
            link_id=f"{original.link_id}:revoked:{moment.isoformat()}",
            source_record_id=original.source_record_id,
            entity_id=original.entity_id,
            probability=original.probability,
            state=LinkState.REVOKED,
            matcher_version=original.matcher_version,
            decision_reason=reason,
            evidence_claim_ids=original.evidence_claim_ids,
            replaces_link_id=original.link_id,
            system_from=moment,
        )
        self._links.append(revoked)
        affected = tuple(
            relationship
            for relationship in self._relationships
            if _current_at(relationship, moment)
            and relationship.review_status is ReviewStatus.ACCEPTED
            and original.entity_id
            in (relationship.subject_entity_id, relationship.object_entity_id)
        )
        for relationship in affected:
            self._relationships.remove(relationship)
            self._relationships.append(relationship.model_copy(update={"system_to": moment}))
            self._relationships.append(
                relationship.model_copy(
                    update={
                        "system_from": moment,
                        "system_to": None,
                        "review_status": ReviewStatus.PENDING,
                    }
                )
            )
        relationship_ids = tuple(sorted(item.relationship_id for item in affected))
        invalidations = tuple(
            sorted(
                {
                    f"entity:{original.entity_id}",
                    f"source-record:{original.source_record_id}",
                    f"link:{original.link_id}",
                    *(f"relationship:{relationship_id}" for relationship_id in relationship_ids),
                }
            )
        )
        return ReconciliationImpact(
            revoked_link_id=revoked.link_id,
            source_record_id=original.source_record_id,
            affected_entity_id=original.entity_id,
            affected_relationship_ids=relationship_ids,
            downstream_invalidation_keys=invalidations,
        )
