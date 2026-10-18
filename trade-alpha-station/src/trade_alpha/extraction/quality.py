"""Golden-set quality metrics for parser and OCR acceptance."""

from __future__ import annotations

import re
from collections.abc import Sequence

from rapidfuzz.distance import Levenshtein

from .models import DocumentBlock, QualityReport


def normalize_for_accuracy(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def character_error_rate(expected: str, actual: str) -> float:
    expected_value = normalize_for_accuracy(expected)
    actual_value = normalize_for_accuracy(actual)
    if not expected_value:
        return 0.0 if not actual_value else 1.0
    return min(1.0, Levenshtein.distance(expected_value, actual_value) / len(expected_value))


def build_quality_report(
    *,
    expected_text: str,
    actual_text: str,
    expected_table_cells: Sequence[str] = (),
    actual_table_cells: Sequence[str] = (),
) -> QualityReport:
    error_rate = character_error_rate(expected_text, actual_text)
    table_accuracy: float | None = None
    if expected_table_cells:
        actual = {normalize_for_accuracy(value) for value in actual_table_cells}
        correct = sum(
            normalize_for_accuracy(value) in actual for value in expected_table_cells
        )
        table_accuracy = correct / len(expected_table_cells)
    return QualityReport(
        character_accuracy=1.0 - error_rate,
        character_error_rate=error_rate,
        table_cell_accuracy=table_accuracy,
        expected_characters=len(normalize_for_accuracy(expected_text)),
        expected_table_cells=len(expected_table_cells),
    )


def text_from_blocks(blocks: Sequence[DocumentBlock]) -> str:
    return " ".join(
        block.text
        for block in sorted(blocks, key=lambda item: item.reading_order)
        if block.block_type not in {"table", "table_cell"}
    )


def table_cells_from_blocks(blocks: Sequence[DocumentBlock]) -> tuple[str, ...]:
    return tuple(
        block.text
        for block in sorted(
            (item for item in blocks if item.block_type == "table_cell"),
            key=lambda item: (
                item.page_number or 0,
                item.parent_block_id or "",
                item.table_row or 0,
                item.table_col or 0,
            ),
        )
    )

