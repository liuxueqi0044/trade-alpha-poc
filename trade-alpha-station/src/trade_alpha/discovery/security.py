"""Bounded network access for M11 catalog discovery and metadata probes."""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

Resolver = Callable[[str, int], list[tuple[Any, ...]]]


class UnsafeDiscoveryUrl(ValueError):
    pass


class DiscoveryBudgetExceeded(RuntimeError):
    pass


class ProbePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_hosts: tuple[str, ...] = Field(min_length=1)
    https_only: bool = True
    max_response_bytes: int = Field(default=2_000_000, gt=0)
    max_redirects: int = Field(default=3, ge=0, le=10)
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)


def _default_resolver(host: str, port: int) -> list[tuple[Any, ...]]:
    return list(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_public_url(
    value: str, policy: ProbePolicy, *, resolver: Resolver = _default_resolver
) -> str:
    parts = urlsplit(value)
    if policy.https_only and parts.scheme.casefold() != "https":
        raise UnsafeDiscoveryUrl("discovery URL must use HTTPS")
    if parts.scheme.casefold() not in {"http", "https"}:
        raise UnsafeDiscoveryUrl("unsupported discovery URL scheme")
    if parts.username or parts.password:
        raise UnsafeDiscoveryUrl("userinfo is forbidden in discovery URLs")
    host = (parts.hostname or "").casefold()
    if not host:
        raise UnsafeDiscoveryUrl("discovery URL requires a hostname")
    allowed = {item.casefold() for item in policy.allowed_hosts}
    if host not in allowed:
        raise UnsafeDiscoveryUrl("discovery host is not allowlisted")
    port = parts.port or (443 if parts.scheme.casefold() == "https" else 80)
    if port not in {80, 443}:
        raise UnsafeDiscoveryUrl("non-standard discovery port is forbidden")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        records = resolver(host, port)
        if not records:
            raise UnsafeDiscoveryUrl("discovery hostname did not resolve") from None
        addresses = {str(record[4][0]) for record in records}
        if not all(_public_address(address) for address in addresses):
            raise UnsafeDiscoveryUrl(
                "discovery hostname resolves to a non-public address"
            ) from None
    else:
        if not _public_address(str(literal)):
            raise UnsafeDiscoveryUrl("non-public discovery IP is forbidden")
    return value


class SafeJsonFetcher:
    """Small JSON-only client with explicit host, redirect, byte, and time budgets."""

    def __init__(
        self,
        policy: ProbePolicy,
        *,
        client: httpx.Client | None = None,
        resolver: Resolver = _default_resolver,
    ) -> None:
        self.policy = policy
        self._client = client or httpx.Client(follow_redirects=False)
        self._resolver = resolver

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_url = str(httpx.URL(url, params=params))
        redirects = 0
        while True:
            validate_public_url(request_url, self.policy, resolver=self._resolver)
            with self._client.stream(
                "GET",
                request_url,
                headers={"Accept": "application/json", "User-Agent": "TradeAlphaSourceRadar/0.1"},
                timeout=self.policy.timeout_seconds,
            ) as response:
                if response.is_redirect:
                    if redirects >= self.policy.max_redirects:
                        raise DiscoveryBudgetExceeded("discovery redirect budget exceeded")
                    location = response.headers.get("location")
                    if not location:
                        raise UnsafeDiscoveryUrl("redirect missing location")
                    request_url = urljoin(request_url, location)
                    redirects += 1
                    continue
                response.raise_for_status()
                declared_length = response.headers.get("content-length")
                if declared_length and int(declared_length) > self.policy.max_response_bytes:
                    raise DiscoveryBudgetExceeded("declared response exceeds byte budget")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.policy.max_response_bytes:
                        raise DiscoveryBudgetExceeded("response exceeds byte budget")
                    chunks.append(chunk)
            payload = json.loads(b"".join(chunks))
            if not isinstance(payload, dict):
                raise ValueError("catalog response must be a JSON object")
            return payload
