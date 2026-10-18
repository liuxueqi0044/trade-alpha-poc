"""Signature-first format detection and parser routing."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable

from .models import DocumentFormat, RoutingDecision


def _mime(value: str | None) -> str | None:
    return value.lower().split(";", 1)[0].strip() if value else None


def _pdf_has_text(data: bytes) -> bool:
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as document:
            return any((page.extract_text() or "").strip() for page in document.pages[:3])
    except Exception:
        lowered = data.lower()
        return b"/font" in lowered and (b" tj" in lowered or b" tj" in lowered)


def _zip_names(data: bytes) -> tuple[str, ...]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return tuple(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return ()


def detect_format(data: bytes, declared_mime: str | None = None) -> DocumentFormat:
    """Detect by bytes first; a MIME type only fills an otherwise unknown format."""

    prefix = data[:4096].lstrip()
    lowered = prefix.lower()
    if data.startswith(b"%PDF-"):
        return DocumentFormat.PDF if _pdf_has_text(data) else DocumentFormat.SCANNED_PDF
    if data.startswith(
        (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*")
    ):
        return DocumentFormat.IMAGE
    if data.startswith(b"PK\x03\x04"):
        names = _zip_names(data)
        if any(name.startswith(("word/", "xl/", "ppt/")) for name in names):
            return DocumentFormat.OFFICE
    if data.startswith((b"RIFF", b"OggS", b"ID3", b"\x1aE\xdf\xa3", b"fLaC")):
        return DocumentFormat.AUDIO_VIDEO
    if any(marker in lowered for marker in (b"<html", b"<!doctype html", b"<article")):
        return DocumentFormat.HTML

    mime = _mime(declared_mime)
    if mime in {"text/html", "application/xhtml+xml"}:
        return DocumentFormat.HTML
    if mime and mime.startswith(("audio/", "video/")):
        return DocumentFormat.AUDIO_VIDEO
    return DocumentFormat.UNKNOWN


def _expected_mimes(document_format: DocumentFormat) -> frozenset[str]:
    values: dict[DocumentFormat, frozenset[str]] = {
        DocumentFormat.HTML: frozenset({"text/html", "application/xhtml+xml"}),
        DocumentFormat.PDF: frozenset({"application/pdf"}),
        DocumentFormat.SCANNED_PDF: frozenset({"application/pdf"}),
        DocumentFormat.IMAGE: frozenset(
            {"image/png", "image/jpeg", "image/gif", "image/bmp", "image/tiff"}
        ),
        DocumentFormat.AUDIO_VIDEO: frozenset(
            {
                "audio/mpeg",
                "audio/ogg",
                "audio/wav",
                "audio/flac",
                "video/mp4",
                "video/webm",
            }
        ),
    }
    return values.get(document_format, frozenset())


def route_artifact(data: bytes, declared_mime: str | None = None) -> RoutingDecision:
    document_format = detect_format(data, declared_mime)
    routes: dict[DocumentFormat, tuple[str, tuple[str, ...], str]] = {
        DocumentFormat.HTML: ("trafilatura", ("html-fallback",), "html-markup"),
        DocumentFormat.PDF: ("docling", ("pdfplumber",), "%PDF-text"),
        DocumentFormat.SCANNED_PDF: (
            "paddleocr",
            ("rapidocr", "tesseract"),
            "%PDF-image",
        ),
        DocumentFormat.OFFICE: ("docling", (), "ooxml-zip"),
        DocumentFormat.IMAGE: ("paddleocr", ("rapidocr", "tesseract"), "image-magic"),
        DocumentFormat.AUDIO_VIDEO: ("whisper", (), "media-magic"),
    }
    primary, fallbacks, signature = routes.get(document_format, ("none", (), "unknown"))
    mime = _mime(declared_mime)
    expected = _expected_mimes(document_format)
    conflict = bool(mime and expected and mime not in expected)
    reason = f"signature={signature}; format={document_format.value}"
    if conflict:
        reason += f"; declared_mime_conflict={mime}"
    return RoutingDecision(
        document_format=document_format,
        primary_parser=primary,
        fallback_parsers=fallbacks,
        signature=signature,
        reason=reason,
        declared_mime=mime,
        mime_conflict=conflict,
    )


def parser_is_compatible(decision: RoutingDecision, parser_profile: str) -> bool:
    return parser_profile in decision.allowed_parsers


def first_compatible_parser(
    decision: RoutingDecision, available_names: Iterable[str]
) -> str | None:
    available = set(available_names)
    return next((name for name in decision.allowed_parsers if name in available), None)
