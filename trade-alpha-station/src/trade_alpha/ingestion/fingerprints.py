"""Canonical request identity and secret-safe logging helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SECRET_MARKERS = ("api_key", "apikey", "token", "secret", "password", "authorization", "key")


def _is_secret(name: str) -> bool:
    lowered = name.lower().replace("-", "_")
    return any(marker in lowered for marker in SECRET_MARKERS)


def redact_url(url: str) -> str:
    """Remove credential query values while retaining a useful request identity."""

    parts = urlsplit(url)
    redacted = [
        (key, "[REDACTED]" if _is_secret(key) else value)
        for key, value in parse_qsl(parts.query, True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(redacted, doseq=True), ""))


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): ("[REDACTED]" if _is_secret(str(key)) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, "[REDACTED]" if _is_secret(key) else value)
        for key, value in parse_qsl(parts.query, True)
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(sorted(query)), "")
    )


def canonical_request(
    *,
    source_id: str,
    endpoint: str,
    params: Mapping[str, Any] | None = None,
    body: Any | None = None,
    time_partition: str | None = None,
    connector_version: str,
    universe_version: str,
) -> dict[str, Any]:
    """Build the public, secret-free value hashed for idempotency."""

    return {
        "source_id": source_id,
        "endpoint": _canonical_url(endpoint),
        "params": _redact(params or {}),
        "body": _redact(body),
        "time_partition": time_partition,
        "connector_version": connector_version,
        "universe_version": universe_version,
    }


def request_fingerprint(**kwargs: Any) -> str:
    canonical = canonical_request(**kwargs)
    payload = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def headers_hash(headers: Mapping[str, str]) -> str:
    safe = _redact({key.lower(): value for key, value in headers.items()})
    payload = json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def sanitize_text(value: str) -> str:
    """Best-effort protection for connector exceptions before persistence/logging."""

    value = re.sub(
        r"(?i)(api[_-]?key|token|secret|password|authorization|key)(=|:|%3[dD])[^\s&,'\"]+",
        r"\1\2[REDACTED]",
        value,
    )
    # This additionally catches a URL embedded in an error message.
    return re.sub(r"https?://[^\s'\"]+", lambda match: redact_url(match.group(0)), value)
