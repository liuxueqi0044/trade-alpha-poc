from datetime import UTC, datetime, timedelta

import pytest

from trade_alpha.knowledge import EntityKind, normalize_identity

T0 = datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture
def times() -> tuple[datetime, datetime, datetime]:
    return T0, T0 + timedelta(days=10), T0 + timedelta(days=20)


@pytest.fixture
def multilingual_fixture() -> tuple[tuple[str, object], ...]:
    """Frozen labelled source identities; never generated from the matcher."""
    return (
        (
            "cn-source",
            normalize_identity(
                EntityKind.ORGANIZATION,
                "深圳星海电子有限公司",
                {"country": "CN", "registration_number": "91440300XH001", "domain": "xinghai.cn"},
            ),
        ),
        (
            "en-source",
            normalize_identity(
                EntityKind.ORGANIZATION,
                "Shenzhen Xinghai Electronics Co., Ltd.",
                {
                    "country": "CN",
                    "registration_number": "91440300XH001",
                    "domain": "xinghai.cn",
                    "transliteration": "Shenzhen Xinghai Electronics",
                },
            ),
        ),
        (
            "same-name-us",
            normalize_identity(
                EntityKind.ORGANIZATION,
                "Xinghai Electronics Inc.",
                {"country": "US", "registration_number": "US-7781"},
            ),
        ),
        (
            "near-name",
            normalize_identity(
                EntityKind.ORGANIZATION, "Shenzhen Xinghai Electrical Co Ltd", {"country": "CN"}
            ),
        ),
    )
