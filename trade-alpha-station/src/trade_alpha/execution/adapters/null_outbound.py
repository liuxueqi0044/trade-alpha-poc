"""The only EP-05 outbound adapter: it is intentionally incapable of delivery."""

from __future__ import annotations

from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.supplier_rfq.models import DeliveryResult, OutboundIntent


class NullOutboundAdapter:
    """Record a blocked attempt without network, filesystem, or provider effects."""

    is_null_adapter = True
    mode = "null"

    def __init__(self) -> None:
        self.attempt_count = 0

    def deliver(self, intent: OutboundIntent) -> DeliveryResult:
        self.attempt_count += 1
        return DeliveryResult(
            intent_fingerprint=stable_fingerprint(intent),
        )

    # Common adapter vocabulary.
    send = deliver
    attempt = deliver
