from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trade_alpha.contracts.common import EvidenceLocator, require_utc


def test_evidence_locator_rejects_invalid_bbox() -> None:
    with pytest.raises(ValidationError):
        EvidenceLocator(artifact_id="a", bbox=(0.8, 0.1, 0.2, 0.9))


def test_require_utc_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        require_utc(datetime(2026, 8, 24))


def test_require_utc_normalizes_timestamp() -> None:
    value = require_utc(datetime(2026, 8, 24, tzinfo=UTC))
    assert value.tzinfo is UTC
