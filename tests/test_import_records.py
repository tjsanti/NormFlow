"""Tests for batch import → review workflow."""

import csv
import tempfile
from pathlib import Path

from normflow.mapping_service import MappingService
from normflow.project_service import init_project
from tests.helpers import seed_mappings


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_import_routes_exact_match_to_library_and_no_match_to_review_items():
    """Exact matches auto-commit; unmatched values become Review Items."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))

        # Seed the library through the Mapping interface.
        ms = MappingService(str(project))
        seed_mappings(project, [("United States", "US")])

        # CSV with 2 rows: one exact match, one unknown
        csv_path = project / "input.csv"
        _write_csv(csv_path, [
            {"name": "United States"},
            {"name": "Nordic Confederation"},
        ], ["name"])

        result = ms.import_records_for_review(str(csv_path), "name")

        # Exact match auto-committed (already existed, so no duplicate)
        assert result["auto_committed"] == 1
        assert ms.project_info()["mappings"] == 1
        assert ms.lookup("United States", semantic=False, llm=False)[0].suggested_text == "US"

        # Unknown value becomes a Review Item.
        assert ms.list_review_items() == [
            {"id": 1, "raw_text": "Nordic Confederation", "suggested_text": ""}
        ]


def test_import_deduplicates_identical_raw_text():
    """Same raw_text appearing multiple times creates only one Review Item."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))

        ms = MappingService(str(project))

        # CSV with 5 rows, all the same value
        csv_path = project / "input.csv"
        _write_csv(csv_path, [
            {"name": "Nordic Confederation"},
            {"name": "Nordic Confederation"},
            {"name": "Nordic Confederation"},
            {"name": "Nordic Confederation"},
            {"name": "Nordic Confederation"},
        ], ["name"])

        result = ms.import_records_for_review(str(csv_path), "name")

        assert len(ms.list_review_items()) == 1
        assert result["skipped"] == 4
        assert result["review_items"] == 1


def test_import_stores_original_csv_in_project():
    """Original CSV is copied to the Project's batch storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))

        ms = MappingService(str(project))

        csv_path = project / "input.csv"
        _write_csv(csv_path, [{"name": "Foo"}], ["name"])

        ms.import_records_for_review(str(csv_path), "name")

        stored = project / ".batches" / "current.csv"
        assert stored.exists()
        assert stored.read_text().strip() == "name\nFoo"


def test_export_returns_original_csv_with_normalized_column():
    """Export reconstructs the original CSV with a normalized_text column filled from mappings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))

        ms = MappingService(str(project))

        # Import a CSV with 3 columns, 2 rows
        csv_path = project / "input.csv"
        _write_csv(csv_path, [
            {"id": "1", "name": "United States", "pop": "330M"},
            {"id": "2", "name": "Canada", "pop": "38M"},
        ], ["id", "name", "pop"])

        ms.import_records_for_review(str(csv_path), "name")

        # Both unmatched — accept one as-is, edit the other
        review_items = {
            item["raw_text"]: item for item in ms.list_review_items()
        }
        us_review_item = review_items["United States"]
        ca_review_item = review_items["Canada"]

        ms.accept_review_item(us_review_item["id"], "US")
        assert ca_review_item["suggested_text"] == ""

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
