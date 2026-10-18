"""Pure security guards for untrusted content, redirects, archives, CSV, and secrets."""

from __future__ import annotations

import io
import ipaddress
import re
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


class MaliciousContentBlocked(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SecretFinding:
    source: str
    pattern_name: str
    occurrence_count: int


@dataclass(frozen=True, slots=True)
class ContentDecision:
    quarantined: bool
    reasons: tuple[str, ...]
    tool_calls: tuple[str, ...] = ()


class SecretScanner:
    """Detect common credentials without echoing matched material."""

    _PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
        ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
        ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
        ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
        (
            "assigned_secret",
            re.compile(
                r"(?i)\b(?:api[_-]?key|access[_-]?token|password|secret|sessionid)"
                r"\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{12,}"
            ),
        ),
    )

    def scan(self, texts: Mapping[str, str | bytes]) -> tuple[SecretFinding, ...]:
        findings: list[SecretFinding] = []
        for source, content in texts.items():
            value = (
                content.decode("utf-8", errors="replace")
                if isinstance(content, bytes)
                else content
            )
            for name, pattern in self._PATTERNS:
                count = len(pattern.findall(value))
                if count:
                    findings.append(SecretFinding(source, name, count))
        return tuple(findings)

    def assert_clean(self, texts: Mapping[str, str | bytes]) -> None:
        findings = self.scan(texts)
        if findings:
            locations = ",".join(sorted({finding.source for finding in findings}))
            raise MaliciousContentBlocked(f"secret material detected in: {locations}")

    def redact(self, text: str) -> str:
        result = text
        for _, pattern in self._PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        return result


_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("instruction_override", re.compile(r"(?i)ignore (?:all |the )?(?:previous|system)")),
    ("secret_request", re.compile(r"(?i)(?:reveal|print|send).{0,20}(?:secret|token|password)")),
    ("tool_request", re.compile(r"(?i)(?:call|invoke|use).{0,20}(?:tool|shell|browser)")),
)


def classify_untrusted_content(text: str) -> ContentDecision:
    reasons = tuple(name for name, pattern in _PROMPT_INJECTION_PATTERNS if pattern.search(text))
    # Content is data only: this decision type has no executable callback or tool handle.
    return ContentDecision(quarantined=bool(reasons), reasons=reasons, tool_calls=())


def inspect_zip_bytes(
    payload: bytes,
    *,
    max_files: int = 100,
    max_uncompressed_bytes: int = 10_000_000,
    max_ratio: float = 100.0,
) -> tuple[str, ...]:
    """Inspect archive metadata before extraction and reject bombs or path traversal."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise MaliciousContentBlocked("invalid zip archive") from error
    with archive:
        members = archive.infolist()
        if len(members) > max_files:
            raise MaliciousContentBlocked("archive file-count limit exceeded")
        total = 0
        safe_names: list[str] = []
        for member in members:
            normalized = member.filename.replace("\\", "/")
            parts = tuple(part for part in normalized.split("/") if part)
            if normalized.startswith("/") or ".." in parts:
                raise MaliciousContentBlocked("archive path traversal blocked")
            total += member.file_size
            if total > max_uncompressed_bytes:
                raise MaliciousContentBlocked("archive uncompressed-size limit exceeded")
            ratio = member.file_size / max(member.compress_size, 1)
            if ratio > max_ratio:
                raise MaliciousContentBlocked("archive compression-ratio limit exceeded")
            safe_names.append(normalized)
        return tuple(safe_names)


def validate_redirect_chain(urls: Sequence[str], allowed_hosts: frozenset[str]) -> tuple[str, ...]:
    if not urls:
        raise MaliciousContentBlocked("empty redirect chain")
    normalized: list[str] = []
    for url in urls:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"https", "http"} or not hostname or parsed.username:
            raise MaliciousContentBlocked("unsafe redirect URL")
        if hostname == "localhost" or hostname not in allowed_hosts:
            raise MaliciousContentBlocked("redirect host is not allowlisted")
        try:
            address = ipaddress.ip_address(hostname.strip("[]"))
        except ValueError:
            address = None
        if address and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            raise MaliciousContentBlocked("redirect to non-public address blocked")
        normalized.append(parsed.geturl())
    return tuple(normalized)


def neutralize_csv_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    candidate = value.lstrip(" \t\r")
    if candidate.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value
