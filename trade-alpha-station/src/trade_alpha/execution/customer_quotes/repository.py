"""Immutable local EP-10 storage, including local-only rendered artifacts."""

from __future__ import annotations

from datetime import datetime

from .models import (
    CustomerCommercialDecision,
    CustomerCostScenarioSelection,
    CustomerQuotePackageSnapshot,
    ExecutionChainManifest,
    ExecutionChainNode,
    InputReference,
)


class InMemoryCustomerQuoteRepository:
    def __init__(self) -> None:
        self._packages: dict[str, list[CustomerQuotePackageSnapshot]] = {}
        self._decisions: dict[str, list[CustomerCommercialDecision]] = {}
        self._selections: dict[str, CustomerCostScenarioSelection] = {}
        self._artifacts: dict[str, str] = {}
        self._manifests: dict[str, ExecutionChainManifest] = {}
        self._idempotency: dict[str, tuple[str, object]] = {}

    def packages(self, package_key: str) -> tuple[CustomerQuotePackageSnapshot, ...]:
        return tuple(self._packages.get(package_key, ()))

    def package(self, package_id: str) -> CustomerQuotePackageSnapshot | None:
        return next(
            (
                item
                for values in self._packages.values()
                for item in values
                if item.customer_quote_package_id == package_id
            ),
            None,
        )

    def save_package(self, value: CustomerQuotePackageSnapshot) -> CustomerQuotePackageSnapshot:
        values = self._packages.setdefault(value.package_key, [])
        existing = next(
            (
                item
                for item in values
                if item.customer_quote_package_id == value.customer_quote_package_id
            ),
            None,
        )
        if existing is not None:
            if existing != value:
                raise ValueError("customer quote package cannot be overwritten")
            return existing
        values.append(value)
        return value

    def save_decision(self, value: CustomerCommercialDecision) -> CustomerCommercialDecision:
        values = self._decisions.setdefault(value.package_key, [])
        existing = next((item for item in values if item.decision_id == value.decision_id), None)
        if existing is not None:
            if existing != value:
                raise ValueError("commercial decision cannot be overwritten")
            return existing
        values.append(value)
        return value

    def decisions(
        self, package_key: str, as_of: datetime
    ) -> tuple[CustomerCommercialDecision, ...]:
        return tuple(
            item for item in self._decisions.get(package_key, ()) if item.decided_at <= as_of
        )

    def save_selection(self, value: CustomerCostScenarioSelection) -> CustomerCostScenarioSelection:
        existing = self._selections.get(value.package_key)
        if existing is not None and existing != value:
            raise ValueError("cost scenario selection is append-only; use a new package key")
        self._selections[value.package_key] = value
        return value

    def selection(self, package_key: str, as_of: datetime) -> CustomerCostScenarioSelection | None:
        value = self._selections.get(package_key)
        return value if value is not None and value.decided_at <= as_of else None

    def save_artifact(self, reference: str, content: str) -> None:
        existing = self._artifacts.get(reference)
        if existing is not None and existing != content:
            raise ValueError("local artifact reference collision")
        self._artifacts[reference] = content

    def artifact(self, reference: str) -> str | None:
        return self._artifacts.get(reference)

    def save_manifest(self, value: ExecutionChainManifest) -> ExecutionChainManifest:
        existing = self._manifests.get(value.manifest_id)
        if existing is not None and existing != value:
            raise ValueError("execution chain manifest cannot be overwritten")
        self._manifests[value.manifest_id] = value
        return value

    def idempotent(self, key: str, fingerprint: str) -> object | None:
        value = self._idempotency.get(key)
        if value is None:
            return None
        if value[0] != fingerprint:
            raise ValueError("idempotency key was reused with different payload")
        return value[1]

    def save_idempotent(self, key: str, fingerprint: str, value: object) -> None:
        existing = self._idempotency.get(key)
        if existing is not None and existing != (fingerprint, value):
            raise ValueError("idempotency key was reused with different payload")
        self._idempotency[key] = (fingerprint, value)


class InMemoryExecutionChainNodeReader:
    def __init__(self, nodes: tuple[ExecutionChainNode, ...]) -> None:
        self._nodes = {(node.stage, node.snapshot_id): node for node in nodes}

    def get_saved_node(self, stage: str, snapshot_id: str) -> ExecutionChainNode | None:
        return self._nodes.get((stage, snapshot_id))


class InMemoryCustomerInputReferenceReader:
    def __init__(self, references: tuple[InputReference, ...]) -> None:
        self._references = {(item.stage, item.snapshot_id): item for item in references}
        if len(self._references) != len(references):
            raise ValueError("customer input references must have unique stage and snapshot IDs")

    def get_saved_reference(
        self, stage: str, snapshot_id: str, as_of: datetime
    ) -> InputReference | None:
        value = self._references.get((stage, snapshot_id))
        return value if value is not None and value.as_of <= as_of else None
