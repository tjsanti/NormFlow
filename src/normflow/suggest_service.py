"""Suggestion service: generate normalization suggestions for raw text."""

import csv
import io
from pathlib import Path

from pydantic import BaseModel, Field
from sqlmodel import select

from .models import ExampleMapping
from .workspace import WorkspaceService


class SuggestionItem(BaseModel):
    """A single suggestion returned by the suggest command."""

    suggested_text: str
    method: str
    confidence: float = Field(ge=0.0, le=1.0)


class SuggestionResult(BaseModel):
    """The output shape of the suggest command."""

    raw_text: str
    suggestions: list[SuggestionItem]


def suggest(
    workspace_path: str,
    raw_text: str,
    limit: int = 1,
    semantic: bool = True,
    semantic_threshold: float = 0.85,
) -> SuggestionResult:
    """Look up suggestions from the mapping library.

    First tries exact match. If no exact match and *semantic* is True,
    falls through to semantic search using the FAISS index.

    Returns at most *limit* suggestions.
    """
    ws = WorkspaceService(workspace_path)

    suggestions: list[SuggestionItem] = []

    with ws.session() as session:
        mapping = session.exec(
            select(ExampleMapping).where(ExampleMapping.raw_text == raw_text)
        ).first()

        if mapping:
            suggestions.append(SuggestionItem(
                suggested_text=mapping.normalized_text,
                method="exact",
                confidence=1.0,
            ))

    # If no exact match and semantic is enabled, try semantic search
    if not suggestions and semantic:
        from .semantic_index import SemanticIndex

        idx = SemanticIndex(workspace_path)
        if idx.exists():
            semantic_results = idx.search(
                raw_text, limit=limit, threshold=semantic_threshold
            )
            for sr in semantic_results:
                suggestions.append(SuggestionItem(
                    suggested_text=sr["normalized_text"],
                    method="semantic",
                    confidence=sr["score"],
                ))

    # Apply limit
    suggestions = suggestions[:limit]

    return SuggestionResult(raw_text=raw_text, suggestions=suggestions)


def suggest_batch(
    workspace_path: str,
    csv_path: str,
    column: str,
    output_column: str = "normalized_text",
    semantic: bool = True,
    semantic_threshold: float = 0.85,
) -> str:
    """Suggest normalizations for every row in a CSV file.

    Reads the CSV, calls suggest for each row's raw text,
    and returns a CSV string with original columns plus the
    output_column holding the top suggestion (blank if no match).

    Entirely blank rows (every column empty) are excluded from output.
    Rows with some data but blank raw text are included with blank suggestion.
    """
    input_file = Path(csv_path).expanduser().resolve()
    if not input_file.exists():
        msg = f"CSV file not found: {input_file}"
        raise FileNotFoundError(msg)

    with open(input_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            msg = "CSV file is empty or has no header row"
            raise ValueError(msg)

        available = list(reader.fieldnames)
        if column not in available:
            msg = f"CSV does not contain a column named '{column}'. Available columns: {', '.join(available)}"
            raise ValueError(msg)

        rows = list(reader)

    # Build output header: original columns + output column
    out_fieldnames = list(rows[0].keys()) if rows else []
    if output_column not in out_fieldnames:
        out_fieldnames.append(output_column)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=out_fieldnames, lineterminator="\n")
    writer.writeheader()

    for row in rows:
        # Skip entirely blank rows
        if all(row.get(col, "").strip() == "" for col in out_fieldnames):
            continue

        raw_text = row.get(column, "").strip()

        out_row = dict(row)

        if raw_text:
            result = suggest(
                workspace_path, raw_text, limit=1,
                semantic=semantic, semantic_threshold=semantic_threshold,
            )
            if result.suggestions:
                out_row[output_column] = result.suggestions[0].suggested_text
            else:
                out_row[output_column] = ""
        else:
            # Blank raw text — include row but skip processing
            out_row[output_column] = ""

        writer.writerow(out_row)

    return output.getvalue()
