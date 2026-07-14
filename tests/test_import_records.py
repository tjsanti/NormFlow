"""Tests for batch import → review workflow."""

import csv
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlmodel import select

from normflow.mapping_service import ExampleMapping, MappingService, ReviewItem
from normflow.project_service import init_project


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

        # Seed the library with one mapping
        ms = MappingService(str(project))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="United States", normalized_text="US"))
            session.commit()

        # CSV with 2 rows: one exact match, one unknown
        csv_path = project / "input.csv"
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

        # Unknown value becomes a Review Item.
        with ms.session() as session:
            pending = session.exec(
                select(ReviewItem)
            ).all()
            assert len(pending) == 1
            assert pending[0].raw_text == "Nordic Confederation"


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

        with ms.session() as session:
            pending = session.exec(select(ReviewItem)).all()
            assert len(pending) == 1

        assert result["skipped"] == 4
        assert result["review_items"] == 1


@patch("normflow.semantic_index._ensure_model")
def test_semantic_auto_commit_marks_index_for_one_later_refresh(mock_ensure):
    """A Batch uses one snapshot, then dirties it after adding a Mapping."""
    model = MagicMock()
    model.encode.return_value = [[1.0, 0.0, 0.0]]
    model.get_sentence_embedding_dimension.return_value = 3
    mock_ensure.return_value = model

    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(str(project))
        with service.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()
        service.build_index()
        csv_path = project / "input.csv"
        _write_csv(csv_path, [{"name": "colr"}], ["name"])

        result = service.import_records_for_review(
            str(csv_path), "name", llm=False, threshold=0.5,
        )

        assert result["auto_committed"] == 1
        assert service.project_info()["semantic_index_status"] == "refresh_required"


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
        with ms.session() as session:
            us_review_item = session.exec(
                select(ReviewItem).where(ReviewItem.raw_text == "United States")
            ).first()
            ca_review_item = session.exec(
                select(ReviewItem).where(ReviewItem.raw_text == "Canada")
            ).first()

        ms.accept_review_item(us_review_item.id, "US")
        assert ca_review_item is not None

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
