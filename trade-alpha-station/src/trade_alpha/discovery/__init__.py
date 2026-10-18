"""M11 source discovery, prioritization, and controlled onboarding."""

from .adapters import APIsGuruAdapter, CKANAdapter, JsonFetcher
from .models import (
    CandidateScore,
    CandidateState,
    ConnectorDraft,
    DiscoveryEvidence,
    DiscoveryKind,
    GoldenValidation,
    OnboardingBundle,
    PolicyReview,
    PublisherKind,
    ScoreSignals,
    SourceCandidate,
    SourceDistribution,
    canonicalize_url,
    metadata_hash,
    stable_candidate_id,
)
from .onboarding import (
    connector_draft,
    draft_source_record,
    enable_after_golden,
    policy_approved_bundle,
)
from .registry import CandidateRegistry, transition
from .scoring import WEIGHTS, derive_signals, score_candidate
from .security import (
    DiscoveryBudgetExceeded,
    ProbePolicy,
    SafeJsonFetcher,
    UnsafeDiscoveryUrl,
    validate_public_url,
)
from .seeds import load_seed_catalog

__all__ = [
    "APIsGuruAdapter",
    "CKANAdapter",
    "CandidateRegistry",
    "CandidateScore",
    "CandidateState",
    "ConnectorDraft",
    "DiscoveryBudgetExceeded",
    "DiscoveryEvidence",
    "DiscoveryKind",
    "GoldenValidation",
    "JsonFetcher",
    "OnboardingBundle",
    "PolicyReview",
    "ProbePolicy",
    "PublisherKind",
    "SafeJsonFetcher",
    "ScoreSignals",
    "SourceCandidate",
    "SourceDistribution",
    "UnsafeDiscoveryUrl",
    "WEIGHTS",
    "canonicalize_url",
    "connector_draft",
    "derive_signals",
    "draft_source_record",
    "enable_after_golden",
    "metadata_hash",
    "load_seed_catalog",
    "policy_approved_bundle",
    "score_candidate",
    "stable_candidate_id",
    "transition",
    "validate_public_url",
]
