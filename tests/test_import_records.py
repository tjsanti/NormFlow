"""Tests for batch import → review workflow."""

import csv
import tempfile
from pathlib import Path

from sqlmodel import select

from normflow.mapping_service import ExampleMapping, MappingService, Suggestion
from normflow.workspace import init_workspace


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_import_routes_exact_match_to_library_and_no_match_to_suggestions():
    """Exact matches auto-commit to library. Unmatched values become pending suggestions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        init_workspace(str(ws))

        # Seed the library with one mapping
        ms = MappingService(str(ws))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="United States", normalized_text="US"))
            session.commit()

        # CSV with 2 rows: one exact match, one unknown
        csv_path = ws / "input.csv"
        _write_csv(csv_path, [
            {"name": "United States"},
            {"name": "Nordic Confederation"},
        ], ["name"])

        ms.import_records_for_review(str(csv_path), "name")

        # Exact match auto-committed (already existed, so no duplicate)
        with ms.session() as session:
            us_mappings = session.exec(
                select(ExampleMapping).where(
                    ExampleMapping.raw_text == "United States"
                )
            ).all()
            assert len(us_mappings) == 1

        # Unknown value stored as pending suggestion
        with ms.session() as session:
            pending = session.exec(
                select(Suggestion).where(
                    Suggestion.status == "pending"
                )
            ).all()
            assert len(pending) == 1
            assert pending[0].raw_text == "Nordic Confederation"


def test_import_deduplicates_identical_raw_text():
    """Same raw_text appearing multiple times creates only one suggestion."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        init_workspace(str(ws))

        ms = MappingService(str(ws))

        # CSV with 5 rows, all the same value
        csv_path = ws / "input.csv"
        _write_csv(csv_path, [
            {"name": "Nordic Confederation"},
            {"name": "Nordic Confederation"},
            {"name": "Nordic Confederation"},
            {"name": "Nordic Confederation"},
            {"name": "Nordic Confederation"},
        ], ["name"])

        result = ms.import_records_for_review(str(csv_path), "name")

        with ms.session() as session:
            pending = session.exec(
                select(Suggestion).where(
                    Suggestion.status == "pending"
                )
            ).all()
            assert len(pending) == 1

        assert result["skipped"] == 4
        assert result["pending"] == 1


def test_import_stores_original_csv_in_workspace():
    """Original CSV is copied to workspace/.batches/current.csv."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        init_workspace(str(ws))

        ms = MappingService(str(ws))

        csv_path = ws / "input.csv"
        _write_csv(csv_path, [{"name": "Foo"}], ["name"])

        ms.import_records_for_review(str(csv_path), "name")

        stored = ws / ".batches" / "current.csv"
        assert stored.exists()
        assert stored.read_text().strip() == "name\nFoo"


def test_export_returns_original_csv_with_normalized_column():
    """Export reconstructs the original CSV with a normalized_text column filled from mappings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        init_workspace(str(ws))

        ms = MappingService(str(ws))

        # Import a CSV with 3 columns, 2 rows
        csv_path = ws / "input.csv"
        _write_csv(csv_path, [
            {"id": "1", "name": "United States", "pop": "330M"},
            {"id": "2", "name": "Canada", "pop": "38M"},
        ], ["id", "name", "pop"])

        ms.import_records_for_review(str(csv_path), "name")

        # Both unmatched — accept one as-is, edit the other
        with ms.session() as session:
            us_suggestion = session.exec(
                select(Suggestion).where(Suggestion.raw_text == "United States")
            ).first()
            ca_suggestion = session.exec(
                select(Suggestion).where(Suggestion.raw_text == "Canada")
            ).first()

        # Edit the US suggestion to set a normalized value
        ms.edit_suggestion(us_suggestion.id, "US")
        # Accept Canada as-is (empty suggestion → empty mapping)
        ms.accept_suggestion(ca_suggestion.id)

        # Export — should return original CSV + normalized_text column
        result = ms.export_normalized_csv("name")

        rows = list(csv.DictReader(result.splitlines()))
        assert len(rows) == 2
        # Has original columns + normalized_text
        assert "id" in rows[0]
        assert "name" in rows[0]
        assert "pop" in rows[0]
        assert "normalized_text" in rows[0]
        # Accepted row has value
        us_row = [r for r in rows if r["name"] == "United States"][0]
        assert us_row["normalized_text"] == "US"
        # Unaccepted row is empty
        ca_row = [r for r in rows if r["name"] == "Canada"][0]
        assert ca_row["normalized_text"] == ""
