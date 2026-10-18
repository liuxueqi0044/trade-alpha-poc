"""Optional connector boundaries.  No adapter imports an optional package eagerly."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from typing import Protocol

from .models import PageResponse


class PageFetcher(Protocol):
    def __call__(self, cursor: str | None, page_number: int) -> PageResponse: ...


class OptionalAdapter:
    package_name: str

    @classmethod
    def available(cls) -> bool:
        return importlib.util.find_spec(cls.package_name) is not None


class DltApiAdapter(OptionalAdapter):
    package_name = "dlt"


class ScrapyAdapter(OptionalAdapter):
    package_name = "scrapy"


class PlaywrightAdapter(OptionalAdapter):
    package_name = "playwright"


class ChangedetectionAdapter(OptionalAdapter):
    package_name = "changedetection"

    @staticmethod
    def should_retrieve(before_hash: str | None, after_hash: str | None) -> bool:
        """A watcher only triggers one collection when the observed bytes changed."""

        return before_hash != after_hash


FixtureFetcher = Callable[[str | None, int], PageResponse]
