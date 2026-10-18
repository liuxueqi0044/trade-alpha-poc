"""Lazy optional Docling adapter for complex Office/PDF documents.

Importing ``trade_alpha.extraction`` and the TXT/HTML/PDF lightweight paths
does not import Docling.  Deployments that install the optional dependency can
pass this backend to :class:`ExtractionService` explicitly.
"""

from __future__ import annotations

import sys
from importlib import import_module
from importlib.util import find_spec
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

from .backends import BackendUnavailable
from .models import DocumentFormat, ParsedBlock


class DoclingBackend:
    name = "docling"
    version = "optional"
    formats = frozenset({DocumentFormat.PDF, DocumentFormat.OFFICE})

    def available(self) -> bool:
        try:
            return find_spec("docling") is not None
        except ValueError:
            # Test doubles and some embedded Python environments register a
            # module without a ModuleSpec.
            return "docling" in sys.modules

    def parse(self, data: bytes, document_format: DocumentFormat) -> tuple[ParsedBlock, ...]:
        if document_format not in self.formats:
            raise ValueError(f"{self.name} does not support {document_format}")
        if not self.available():
            raise BackendUnavailable("docling is not installed; use the lightweight fallback")
        # Keep the heavy import strictly inside the call boundary.
        converter_module: Any = import_module("docling.document_converter")
        document_converter = converter_module.DocumentConverter

        try:
            base_models: Any = import_module("docling.datamodel.base_models")
            document_stream = base_models.DocumentStream
        except ImportError:
            io_types: Any = import_module("docling_core.types.io")
            document_stream = io_types.DocumentStream

        converter: Any = document_converter()
        extension = "pdf" if document_format is DocumentFormat.PDF else "docx"
        stream = document_stream(
            name=PurePosixPath(f"input.{extension}").name,
            stream=BytesIO(data),
        )
        result: Any = converter.convert(source=stream)
        markdown = result.document.export_to_markdown()
        lines = [line.strip() for line in str(markdown).splitlines() if line.strip()]
        return tuple(
            ParsedBlock(local_id=f"docling-{index}", block_type="paragraph", text=text)
            for index, text in enumerate(lines)
        )
