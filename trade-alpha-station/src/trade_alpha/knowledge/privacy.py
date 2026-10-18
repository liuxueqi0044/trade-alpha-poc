"""Field-level PII projection; raw records remain untouched."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Entity, EntityKind


class PIIAccessDenied(PermissionError):
    """Raised when a caller requests PII without the explicit grant."""


@dataclass(frozen=True)
class Authorization:
    principal: str
    pii_read: bool = False


_PII_FIELDS = frozenset({"email", "phone", "personal_email", "personal_phone"})


def redact_entity(entity: Entity, authorization: Authorization) -> Entity:
    """Return a safe projection; aggregate fields can use the original elsewhere."""
    if entity.entity_type is not EntityKind.PERSON or authorization.pii_read:
        return entity
    attributes: dict[str, Any] = {
        key: value for key, value in entity.attributes.items() if key not in _PII_FIELDS
    }
    return entity.model_copy(update={"attributes": attributes})


def require_pii(authorization: Authorization) -> None:
    if not authorization.pii_read:
        raise PIIAccessDenied("PII read permission is required")
