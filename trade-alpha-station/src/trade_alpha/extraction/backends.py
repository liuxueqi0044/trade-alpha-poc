"""Real lightweight backends plus explicit seams for heavyweight parsers."""

from __future__ import annotations

import io
import re
from collections.abc import Callable
from html.parser import HTMLParser
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol, cast

from .models import DocumentFormat, ParsedBlock


class BackendUnavailable(RuntimeError):
    pass


class ParserBackend(Protocol):
    name: str
    version: str
    formats: frozenset[DocumentFormat]

    def available(self) -> bool: ...

    def parse(self, data: bytes, document_format: DocumentFormat) -> tuple[ParsedBlock, ...]: ...


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unavailable"


class _SemanticHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []
        self._tag: str | None = None
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript", "template"}:
            self._skip_depth += 1
        if (
            tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "caption"}
            and self._skip_depth == 0
        ):
            self._tag = tag
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._tag and self._skip_depth == 0:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._tag:
            text = re.sub(r"\s+", " ", "".join(self._parts)).strip()
            if text:
                kind = "heading" if tag.startswith("h") else "paragraph"
                self.items.append((kind, text))
            self._tag = None
            self._parts = []
        if tag in {"script", "style", "noscript", "template"}:
            self._skip_depth = max(0, self._skip_depth - 1)


class HtmlFallbackBackend:
    name = "html-fallback"
    version = "1.0.0"
    formats = frozenset({DocumentFormat.HTML})

    def available(self) -> bool:
        return True

    def parse(self, data: bytes, document_format: DocumentFormat) -> tuple[ParsedBlock, ...]:
        if document_format not in self.formats:
            raise ValueError(f"{self.name} does not support {document_format}")
        parser = _SemanticHTMLParser()
        parser.feed(data.decode("utf-8", errors="replace"))
        return tuple(
            ParsedBlock(local_id=f"html-{index}", block_type=cast(Any, kind), text=text)
            for index, (kind, text) in enumerate(parser.items)
        )


class TrafilaturaBackend:
    name = "trafilatura"
    version = _package_version("trafilatura")
    formats = frozenset({DocumentFormat.HTML})

    def available(self) -> bool:
        return self.version != "unavailable"

    def parse(self, data: bytes, document_format: DocumentFormat) -> tuple[ParsedBlock, ...]:
        if document_format not in self.formats:
            raise ValueError(f"{self.name} does not support {document_format}")
        if not self.available():
            raise BackendUnavailable("trafilatura is not installed")
        import trafilatura

        extracted = trafilatura.extract(
            data.decode("utf-8", errors="replace"),
            output_format="txt",
            include_tables=True,
            favor_precision=True,
        )
        if not extracted or not extracted.strip():
            raise BackendUnavailable("trafilatura returned no content")
        paragraphs = tuple(
            value.strip() for value in re.split(r"\n{2,}|\n", extracted) if value.strip()
        )
        return tuple(
            ParsedBlock(local_id=f"trafilatura-{index}", block_type="paragraph", text=text)
            for index, text in enumerate(paragraphs)
        )


def _normalized_bbox(
    bbox: tuple[float, float, float, float], width: float, height: float
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = bbox
    epsilon = 1e-9
    return (
        max(0.0, min(1.0 - epsilon, x0 / width)),
        max(0.0, min(1.0 - epsilon, y0 / height)),
        max(epsilon, min(1.0, x1 / width)),
        max(epsilon, min(1.0, y1 / height)),
    )


class PdfPlumberBackend:
    """Born-digital PDF fallback with page locators and table structure."""

    name = "pdfplumber"
    version = _package_version("pdfplumber")
    formats = frozenset({DocumentFormat.PDF})

    def available(self) -> bool:
        return self.version != "unavailable"

    def parse(self, data: bytes, document_format: DocumentFormat) -> tuple[ParsedBlock, ...]:
        if document_format not in self.formats:
            raise ValueError(f"{self.name} does not support {document_format}")
        if not self.available():
            raise BackendUnavailable("pdfplumber is not installed")
        import pdfplumber

        blocks: list[ParsedBlock] = []
        with pdfplumber.open(io.BytesIO(data)) as document:
            for page_number, page in enumerate(document.pages, start=1):
                width = float(page.width)
                height = float(page.height)
                lines: list[dict[str, Any]] = page.extract_text_lines(
                    layout=False, strip=True, return_chars=False, use_text_flow=True
                )
                for index, line in enumerate(lines):
                    text = str(line.get("text", "")).strip()
                    if not text:
                        continue
                    bbox = _normalized_bbox(
                        (
                            float(line.get("x0", 0.0)),
                            float(line.get("top", 0.0)),
                            float(line.get("x1", width)),
                            float(line.get("bottom", height)),
                        ),
                        width,
                        height,
                    )
                    blocks.append(
                        ParsedBlock(
                            local_id=f"page-{page_number}-line-{index}",
                            block_type="paragraph",
                            text=text,
                            page_number=page_number,
                            bbox=bbox,
                        )
                    )
                for table_index, table in enumerate(page.find_tables()):
                    table_local_id = f"page-{page_number}-table-{table_index}"
                    raw_bbox = tuple(float(value) for value in table.bbox)
                    table_bbox = _normalized_bbox(
                        (raw_bbox[0], raw_bbox[1], raw_bbox[2], raw_bbox[3]), width, height
                    )
                    matrix = table.extract() or []
                    blocks.append(
                        ParsedBlock(
                            local_id=table_local_id,
                            block_type="table",
                            text="\n".join(
                                " | ".join("" if cell is None else str(cell) for cell in row)
                                for row in matrix
                            ),
                            page_number=page_number,
                            bbox=table_bbox,
                            locator_quality="coarse",
                        )
                    )
                    for row_index, row in enumerate(matrix):
                        for col_index, cell in enumerate(row):
                            if cell is None:
                                continue
                            blocks.append(
                                ParsedBlock(
                                    local_id=(
                                        f"{table_local_id}-cell-{row_index}-{col_index}"
                                    ),
                                    block_type="table_cell",
                                    text=str(cell),
                                    page_number=page_number,
                                    parent_local_id=table_local_id,
                                    bbox=table_bbox,
                                    locator_quality="coarse",
                                    table_row=row_index,
                                    table_col=col_index,
                                )
                            )
        return tuple(blocks)


class RapidOCRBackend:
    """Local OCR fallback for images and image-only PDFs."""

    name = "rapidocr"
    version = _package_version("rapidocr-onnxruntime")
    formats = frozenset({DocumentFormat.IMAGE, DocumentFormat.SCANNED_PDF})

    def __init__(self) -> None:
        self._engine: Any | None = None

    def available(self) -> bool:
        return self.version != "unavailable"

    def _get_engine(self) -> Any:
        if self._engine is None:
            if not self.available():
                raise BackendUnavailable("rapidocr-onnxruntime is not installed")
            from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-untyped]

            self._engine = RapidOCR()
        return self._engine

    @staticmethod
    def _images(data: bytes, document_format: DocumentFormat) -> tuple[Any, ...]:
        import numpy as np
        from PIL import Image

        if document_format is DocumentFormat.IMAGE:
            with Image.open(io.BytesIO(data)) as image:
                return (np.asarray(image.convert("RGB")),)
        if document_format is DocumentFormat.SCANNED_PDF:
            import pypdfium2 as pdfium  # type: ignore[import-untyped]

            document = pdfium.PdfDocument(data)
            return tuple(
                np.asarray(page.render(scale=2.2).to_pil().convert("RGB")) for page in document
            )
        raise ValueError(f"rapidocr does not support {document_format}")

    def parse(self, data: bytes, document_format: DocumentFormat) -> tuple[ParsedBlock, ...]:
        if document_format not in self.formats:
            raise ValueError(f"{self.name} does not support {document_format}")
        engine = self._get_engine()
        blocks: list[ParsedBlock] = []
        for page_number, image in enumerate(self._images(data, document_format), start=1):
            height, width = image.shape[:2]
            result, _elapsed = engine(image)
            for index, item in enumerate(result or []):
                polygon, text, confidence = item
                xs = [float(point[0]) for point in polygon]
                ys = [float(point[1]) for point in polygon]
                blocks.append(
                    ParsedBlock(
                        local_id=f"ocr-{page_number}-{index}",
                        block_type="ocr_token",
                        text=str(text),
                        page_number=page_number,
                        bbox=_normalized_bbox(
                            (min(xs), min(ys), max(xs), max(ys)), float(width), float(height)
                        ),
                        confidence=float(confidence),
                    )
                )
        return tuple(blocks)


class CallableBackend:
    """Typed adapter used by deployments and deterministic golden fixtures."""

    def __init__(
        self,
        *,
        name: str,
        version: str,
        formats: frozenset[DocumentFormat],
        parser: Callable[[bytes, DocumentFormat], tuple[ParsedBlock, ...]],
    ) -> None:
        self.name = name
        self.version = version
        self.formats = formats
        self._parser = parser

    def available(self) -> bool:
        return True

    def parse(self, data: bytes, document_format: DocumentFormat) -> tuple[ParsedBlock, ...]:
        if document_format not in self.formats:
            raise ValueError(f"{self.name} does not support {document_format}")
        return self._parser(data, document_format)


def default_backends() -> dict[str, ParserBackend]:
    values: tuple[ParserBackend, ...] = (
        TrafilaturaBackend(),
        HtmlFallbackBackend(),
        PdfPlumberBackend(),
        RapidOCRBackend(),
    )
    return {backend.name: backend for backend in values}
