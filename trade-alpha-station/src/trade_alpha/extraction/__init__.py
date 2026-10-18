"""M02 public API: routed, versioned, evidence-linked multimodal extraction."""

from .backends import (
    BackendUnavailable,
    CallableBackend,
    HtmlFallbackBackend,
    ParserBackend,
    PdfPlumberBackend,
    RapidOCRBackend,
    TrafilaturaBackend,
    default_backends,
)
from .claims import gate_claim, parse_llm_claim, rule_extract
from .models import (
    CandidateClaim,
    DocumentBlock,
    DocumentFormat,
    ExtractionRun,
    ParsedBlock,
    QualityReport,
    QuarantineRecord,
    ReviewDecision,
    ReviewProjection,
    RoutingDecision,
    SchemaGateResult,
)
from .quality import (
    build_quality_report,
    character_error_rate,
    table_cells_from_blocks,
    text_from_blocks,
)
from .routing import detect_format, parser_is_compatible, route_artifact
from .service import ExtractionService, ExtractionStore, LLMExtractor

__all__ = [
    "BackendUnavailable",
    "CallableBackend",
    "CandidateClaim",
    "DocumentBlock",
    "DocumentFormat",
    "ExtractionRun",
    "ExtractionService",
    "ExtractionStore",
    "HtmlFallbackBackend",
    "LLMExtractor",
    "ParsedBlock",
    "ParserBackend",
    "PdfPlumberBackend",
    "QualityReport",
    "QuarantineRecord",
    "RapidOCRBackend",
    "ReviewDecision",
    "ReviewProjection",
    "RoutingDecision",
    "SchemaGateResult",
    "TrafilaturaBackend",
    "build_quality_report",
    "character_error_rate",
    "default_backends",
    "detect_format",
    "gate_claim",
    "parse_llm_claim",
    "parser_is_compatible",
    "route_artifact",
    "rule_extract",
    "table_cells_from_blocks",
    "text_from_blocks",
]

