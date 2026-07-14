"""Shared test helpers that arrange state through NormFlow's domain interface."""

import csv
from pathlib import Path
import tempfile
from typing import IO
from unittest.mock import MagicMock, patch

from normflow.mapping_service import MappingService


def _write_records_csv(raw_texts: list[str]) -> IO[str]:
    csv_file = tempfile.NamedTemporaryFile(
        mode="w",
        newline="",
        encoding="utf-8",
        suffix=".csv",
    )
    writer = csv.writer(csv_file)
    writer.writerow(["raw_text"])
    writer.writerows((raw_text,) for raw_text in raw_texts)
    csv_file.flush()
    return csv_file


def seed_mappings(project_path: Path, pairs: list[tuple[str, str]]) -> None:
    """Import Mappings through the same interface used by production callers."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        newline="",
        encoding="utf-8",
        suffix=".csv",
    ) as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["raw_text", "normalized_text"])
        writer.writerows(pairs)
        csv_file.flush()
        MappingService(project_path).import_mappings(
            csv_file.name,
            "raw_text",
            "normalized_text",
        )


def import_blank_review_items(project_path: Path, raw_texts: list[str]) -> None:
    """Create unmatched Review Items through Batch Import."""
    with _write_records_csv(raw_texts) as csv_file:
        MappingService(project_path).import_records_for_review(
            csv_file.name,
            "raw_text",
            semantic=False,
            llm=False,
        )


def import_suggested_review_items(
    project_path: Path,
    items: list[tuple[str, str]],
) -> None:
    """Create suggested Review Items through Batch Import and Suggestion lookup."""
    seed_mappings(project_path, [("example input", "Example Output")])

    encoder = MagicMock()

    def encode(texts, **_kwargs):
        if texts == ["example input"]:
            return [[1.0, 0.0, 0.0] for _ in texts]
        return [[0.0, 1.0, 0.0] for _ in texts]

    encoder.encode.side_effect = encode
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content=suggested_text))])
        for _, suggested_text in items
    ]

    service = MappingService(project_path)
    with (
        patch("normflow.semantic_index._ensure_model", return_value=encoder),
        patch("normflow.llm_matcher.build_client", return_value=client),
        _write_records_csv([raw_text for raw_text, _ in items]) as csv_file,
    ):
        service.build_index()
        service.import_records_for_review(
            csv_file.name,
            "raw_text",
            semantic=True,
            llm=True,
        )
