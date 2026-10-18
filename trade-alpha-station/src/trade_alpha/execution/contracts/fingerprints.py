"""Stable JSON serialization and SHA-256 fingerprints for execution contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel


def _canonicalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="json", by_alias=False, exclude_none=False))
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonicalize(item) for item in value]
    return value


def canonical_json(value: BaseModel | Mapping[str, Any]) -> str:
    """Serialize a public contract deterministically, with no whitespace ambiguity."""

    payload = _canonicalize(value)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_fingerprint(value: BaseModel | Mapping[str, Any]) -> str:
    """Return a portable SHA-256 fingerprint of canonical contract JSON."""

    return f"sha256:{hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()}"


class CanonicalFingerprintPort(Protocol):
    """Port for a deterministic fingerprint implementation."""

    def fingerprint(self, value: BaseModel | Mapping[str, Any]) -> str: ...


class Sha256CanonicalFingerprinter:
    """The standard pure-Python implementation of :class:`CanonicalFingerprintPort`."""

    def fingerprint(self, value: BaseModel | Mapping[str, Any]) -> str:
        return stable_fingerprint(value)
