"""Source registry and non-bypassable policy gate."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from .models import SourceRecord


class UnknownSourceError(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None


class DecisionLike(Protocol):
    allowed: bool
    reason: str | None


class SourcePolicyHook(Protocol):
    def __call__(self, source_id: str) -> DecisionLike: ...


class SourceRegistry:
    """An in-process registry; callers persist/version registry data outside this class."""

    def __init__(
        self,
        records: tuple[SourceRecord, ...] | list[SourceRecord] = (),
        *,
        policy_hooks: Iterable[SourcePolicyHook] = (),
    ) -> None:
        self._records: dict[str, SourceRecord] = {}
        self._policy_hooks = tuple(policy_hooks)
        for record in records:
            self.register(record)

    def register(self, record: SourceRecord) -> None:
        if record.source_id in self._records:
            raise ValueError(f"duplicate source_id: {record.source_id}")
        self._records[record.source_id] = record

    def get(self, source_id: str) -> SourceRecord:
        try:
            return self._records[source_id]
        except KeyError as exc:
            raise UnknownSourceError(source_id) from exc

    def policy_gate(self, source_id: str, *, formal_schedule: bool = True) -> PolicyDecision:
        """Return whether a source can be contacted.

        A formal schedule has a higher bar than an exploratory connector: raw
        retention and commercial-use approval must be explicitly recorded.
        This method is intentionally the only gate used by ``IngestionService``.
        """

        record = self.get(source_id)
        if record.status != "enabled":
            return PolicyDecision(False, "source_disabled")
        for hook in self._policy_hooks:
            try:
                decision = hook(source_id)
            except KeyError:
                return PolicyDecision(False, "governance_source_not_registered")
            if not decision.allowed:
                return PolicyDecision(False, f"governance:{decision.reason or 'denied'}")
        if not formal_schedule:
            return PolicyDecision(True)
        # ``software_license`` is intentionally optional (many data providers
        # publish data without distributing software), but every other policy
        # field needed to assess collection and downstream use is mandatory.
        for field in ("robots_policy", "terms_url", "data_license", "redistribution"):
            if not getattr(record, field):
                return PolicyDecision(False, f"missing_{field}")
        if not record.retention_policy:
            return PolicyDecision(False, "missing_retention_policy")
        if record.commercial_use not in {"approved", "allowed", "permitted"}:
            return PolicyDecision(False, "commercial_use_not_approved")
        return PolicyDecision(True)
