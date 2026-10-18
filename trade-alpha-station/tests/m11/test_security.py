from __future__ import annotations

from typing import Any

import httpx
import pytest

from trade_alpha.discovery import (
    DiscoveryBudgetExceeded,
    ProbePolicy,
    SafeJsonFetcher,
    UnsafeDiscoveryUrl,
    validate_public_url,
)


def public_resolver(host: str, port: int) -> list[tuple[Any, ...]]:
    del host, port
    return [(2, 1, 6, "", ("8.8.8.8", 443))]


def private_resolver(host: str, port: int) -> list[tuple[Any, ...]]:
    del host, port
    return [(2, 1, 6, "", ("127.0.0.1", 443))]


def test_url_policy_rejects_http_userinfo_private_and_non_allowlist() -> None:
    policy = ProbePolicy(allowed_hosts=("catalog.example.org",))

    with pytest.raises(UnsafeDiscoveryUrl, match="HTTPS"):
        validate_public_url("http://catalog.example.org/data", policy, resolver=public_resolver)
    with pytest.raises(UnsafeDiscoveryUrl, match="userinfo"):
        validate_public_url(
            "https://user:pass@catalog.example.org/data", policy, resolver=public_resolver
        )
    with pytest.raises(UnsafeDiscoveryUrl, match="allowlisted"):
        validate_public_url("https://evil.example.org/data", policy, resolver=public_resolver)
    with pytest.raises(UnsafeDiscoveryUrl, match="non-public"):
        validate_public_url("https://catalog.example.org/data", policy, resolver=private_resolver)


def test_safe_fetcher_revalidates_redirect_target() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example.org/catalog"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    fetcher = SafeJsonFetcher(
        ProbePolicy(allowed_hosts=("catalog.example.org",)),
        client=client,
        resolver=public_resolver,
    )

    with pytest.raises(UnsafeDiscoveryUrl, match="allowlisted"):
        fetcher.get_json("https://catalog.example.org/api")


def test_safe_fetcher_enforces_byte_budget() -> None:
    body = b'{"data":"' + b"x" * 200 + b'"}'

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    fetcher = SafeJsonFetcher(
        ProbePolicy(allowed_hosts=("catalog.example.org",), max_response_bytes=100),
        client=client,
        resolver=public_resolver,
    )

    with pytest.raises(DiscoveryBudgetExceeded, match="byte budget"):
        fetcher.get_json("https://catalog.example.org/api")


def test_safe_fetcher_parses_bounded_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"].startswith("TradeAlphaSourceRadar")
        return httpx.Response(200, json={"success": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    fetcher = SafeJsonFetcher(
        ProbePolicy(allowed_hosts=("catalog.example.org",)),
        client=client,
        resolver=public_resolver,
    )

    assert fetcher.get_json("https://catalog.example.org/api") == {"success": True}
