"""Ports for local EP-10 rendering and chain verification."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    CustomerQuotePackageSnapshot,
    CustomerQuoteRenderView,
    ExecutionChainNode,
    InputReference,
)


class CustomerQuoteRendererPort(Protocol):
    @property
    def template_fingerprint(self) -> str: ...
    def render_quote(self, view: CustomerQuoteRenderView) -> str: ...
    def render_communication(self, view: CustomerQuoteRenderView) -> str: ...


class ExecutionChainNodeReader(Protocol):
    def get_saved_node(self, stage: str, snapshot_id: str) -> ExecutionChainNode | None: ...


class CustomerQuotePackageReader(Protocol):
    def package(self, package_id: str) -> CustomerQuotePackageSnapshot | None: ...


class CustomerInputReferenceReader(Protocol):
    def get_saved_reference(
        self, stage: str, snapshot_id: str, as_of: datetime
    ) -> InputReference | None: ...


class AuditChainVerifierPort(Protocol):
    def verify(self) -> bool: ...
    def tip_hash(self) -> str: ...
    def verified_at(self) -> datetime: ...
