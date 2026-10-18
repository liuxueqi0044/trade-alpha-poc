"""Product-specification projection, review, readiness, and versioning API."""

from .models import (
    DecisionAction,
    FieldDefinition,
    FieldState,
    GateStatus,
    ProductFamilyProfile,
    ProductSpec,
    ProductSpecVersion,
    ReadinessResult,
    SpecConflict,
    SpecDiff,
    SpecFieldDecision,
    SpecFieldProjection,
    SpecState,
    ValueKind,
)
from .profiles import load_profile
from .repository import InMemoryProductSpecRepository
from .service import SpecificationService

__all__ = [
    "DecisionAction",
    "FieldDefinition",
    "FieldState",
    "GateStatus",
    "InMemoryProductSpecRepository",
    "ProductFamilyProfile",
    "ProductSpec",
    "ProductSpecVersion",
    "ReadinessResult",
    "SpecConflict",
    "SpecDiff",
    "SpecFieldDecision",
    "SpecFieldProjection",
    "SpecState",
    "SpecificationService",
    "ValueKind",
    "load_profile",
]
