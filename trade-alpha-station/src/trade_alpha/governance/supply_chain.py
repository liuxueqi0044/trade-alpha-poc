"""Deterministic SBOM, license, and vulnerability policy evaluation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata

from .models import (
    LicenseWaiver,
    SBOMComponent,
    Vulnerability,
    VulnerabilityWaiver,
)


@dataclass(frozen=True, slots=True)
class SupplyChainReport:
    allowed: bool
    blocked_items: tuple[str, ...]


def build_sbom(
    installed: Iterable[metadata.Distribution] | None = None,
    *,
    direct_names: frozenset[str] = frozenset(),
) -> tuple[SBOMComponent, ...]:
    distributions = installed if installed is not None else metadata.distributions()
    components: list[SBOMComponent] = []
    for distribution in distributions:
        name = distribution.metadata.get("Name") or "unknown-package"
        version = distribution.version or "unknown-version"
        license_id = (
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or "UNKNOWN"
        ).strip()
        components.append(
            SBOMComponent(
                name=name,
                version=version,
                license_id=license_id or "UNKNOWN",
                direct=name.casefold() in {item.casefold() for item in direct_names},
            )
        )
    return tuple(sorted(components, key=lambda item: item.name.casefold()))


def evaluate_licenses(
    components: Iterable[SBOMComponent],
    waivers: Iterable[LicenseWaiver],
    at: datetime,
) -> SupplyChainReport:
    active_waivers = {
        waiver.component.casefold(): waiver
        for waiver in waivers
        if waiver.expires_at >= at and waiver.adr_ref.strip()
    }
    blocked: list[str] = []
    for component in components:
        license_id = component.license_id.casefold()
        risky = (
            license_id in {"unknown", "unlicensed", "none", ""}
            or "agpl" in license_id
            or "commons clause" in license_id
            or "non-commercial" in license_id
        )
        if risky and component.name.casefold() not in active_waivers:
            blocked.append(component.name)
    return SupplyChainReport(allowed=not blocked, blocked_items=tuple(sorted(blocked)))


def evaluate_vulnerabilities(
    vulnerabilities: Iterable[Vulnerability],
    waivers: Iterable[VulnerabilityWaiver],
    at: datetime,
) -> SupplyChainReport:
    active_waivers = {
        waiver.vulnerability_id: waiver
        for waiver in waivers
        if waiver.expires_at >= at and waiver.adr_ref.strip()
    }
    blocked = sorted(
        vulnerability.vulnerability_id
        for vulnerability in vulnerabilities
        if vulnerability.severity.casefold() == "critical"
        and vulnerability.vulnerability_id not in active_waivers
    )
    return SupplyChainReport(allowed=not blocked, blocked_items=tuple(blocked))
