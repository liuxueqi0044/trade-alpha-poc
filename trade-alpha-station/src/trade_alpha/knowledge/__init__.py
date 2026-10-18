"""Versioned, evidence-first entity resolution and knowledge views."""

from .graph import GraphSnapshot, build_snapshot
from .matcher import (
    CandidatePair,
    LinkEvaluation,
    MatchDecision,
    MatchThresholds,
    RapidFuzzMatcher,
    SplinkAdapter,
    evaluate_labeled_pairs,
)
from .models import (
    Claim,
    Entity,
    EntityAlias,
    EntityKind,
    EntityLink,
    Event,
    LinkState,
    ReconciliationImpact,
    Relationship,
    ReviewStatus,
    SourceRecord,
)
from .normalize import NormalizedIdentity, blocking_keys, normalize_identity
from .privacy import Authorization, PIIAccessDenied, redact_entity
from .store import KnowledgeStore

__all__ = [
    "Authorization",
    "CandidatePair",
    "Claim",
    "Entity",
    "EntityAlias",
    "EntityKind",
    "EntityLink",
    "Event",
    "GraphSnapshot",
    "KnowledgeStore",
    "LinkEvaluation",
    "LinkState",
    "MatchDecision",
    "MatchThresholds",
    "NormalizedIdentity",
    "PIIAccessDenied",
    "RapidFuzzMatcher",
    "Relationship",
    "ReconciliationImpact",
    "ReviewStatus",
    "SplinkAdapter",
    "SourceRecord",
    "blocking_keys",
    "build_snapshot",
    "evaluate_labeled_pairs",
    "normalize_identity",
    "redact_entity",
]
