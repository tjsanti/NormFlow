"""Tests for batch import → review workflow."""

import csv
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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

        result = ms.import_records_for_review(
            str(csv_path), "name", semantic=False, llm=False,
        )

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

        result = ms.import_records_for_review(
            str(csv_path), "name", semantic=False, llm=False,
        )

        assert len(ms.list_review_items()) == 1
        assert result["skipped"] == 4
        assert result["review_items"] == 1


@pytest.mark.parametrize(
    ("provider_response", "failure_detail"),
    [
        (RuntimeError("missing API credential"), "missing API credential"),
        (RuntimeError("invalid provider endpoint"), "invalid provider endpoint"),
        (RuntimeError("configured model was not found"), "configured model was not found"),
        (RuntimeError("network connection timed out"), "network connection timed out"),
        (RuntimeError("provider unavailable"), "provider unavailable"),
        (
            MagicMock(choices=[MagicMock(message=MagicMock(content="   "))]),
            "blank Suggestion",
        ),
    ],
    ids=[
        "credential",
        "endpoint",
        "model",
        "network",
        "provider",
        "blank-response",
    ],
)
def test_provider_failure_aborts_batch_without_replacing_retained_batch(
    provider_response,
    failure_detail,
):
    """A failed Batch Import leaves all Project state at its prior snapshot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(project)
        seed_mappings(project, [("colour", "color")])

        previous_batch = project / "previous.csv"
        _write_csv(previous_batch, [{"name": "preserved"}], ["name"])
        service.import_records_for_review(
            str(previous_batch), "name", semantic=False, llm=False,
        )

        encoder = MagicMock()
        vectors = {
            "colour": [1.0, 0.0, 0.0],
            "colr": [1.0, 0.0, 0.0],
            "provider fail": [0.0, 1.0, 0.0],
        }
        encoder.encode.side_effect = lambda texts, **_kwargs: [
            vectors[text] for text in texts
        ]
        encoder.get_sentence_embedding_dimension.return_value = 2
        client = MagicMock()
        if isinstance(provider_response, Exception):
            client.chat.completions.create.side_effect = provider_response
        else:
            client.chat.completions.create.return_value = provider_response

        failed_batch = project / "failed.csv"
        _write_csv(
            failed_batch,
            [{"name": "colr"}, {"name": "provider fail"}],
            ["name"],
        )

        with (
            patch("normflow.semantic_index._ensure_model", return_value=encoder),
            patch("normflow.llm_matcher.build_client", return_value=client),
        ):
            service.build_index()
            with pytest.raises(
                RuntimeError,
                match=(
                    f"Batch Import failed.*{failure_detail}.*no changes were made"
                ),
            ):
                service.import_records_for_review(str(failed_batch), "name")

        assert service.project_info()["mappings"] == 1
        assert service.list_review_items() == [
            {"id": 1, "raw_text": "preserved", "suggested_text": ""}
        ]
        assert (project / ".batches" / "current.csv").read_bytes() == (
            previous_batch.read_bytes()
        )


def test_csv_staging_failure_rolls_back_batch_and_retains_previous_csv():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(project)

        previous_batch = project / "previous.csv"
        _write_csv(previous_batch, [{"name": "preserved"}], ["name"])
        service.import_records_for_review(
            str(previous_batch), "name", semantic=False, llm=False,
        )
        retained_batch = project / ".batches" / "current.csv"

        next_batch = project / "next.csv"
        _write_csv(next_batch, [{"name": "new item"}], ["name"])
        with (
            patch(
                "normflow.mapping_service.shutil.copy2",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(OSError, match="disk full"),
        ):
            service.import_records_for_review(
                str(next_batch), "name", semantic=False, llm=False,
            )

        assert service.list_review_items() == [
            {"id": 1, "raw_text": "preserved", "suggested_text": ""}
        ]
        assert retained_batch.read_bytes() == previous_batch.read_bytes()
        assert list(retained_batch.parent.iterdir()) == [retained_batch]


def test_csv_publication_failure_compensates_committed_batch_and_restores_csv():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(project)

        previous_batch = project / "previous.csv"
        _write_csv(previous_batch, [{"name": "preserved"}], ["name"])
        service.import_records_for_review(
            str(previous_batch), "name", semantic=False, llm=False,
        )
        retained_batch = project / ".batches" / "current.csv"

        next_batch = project / "next.csv"
        _write_csv(next_batch, [{"name": "new item"}], ["name"])

        real_replace = os.replace
        committed_before_publication = False

        def fail_new_csv_publication(source, destination):
            nonlocal committed_before_publication
            if Path(source).name.startswith(".current-"):
                committed_before_publication = service.list_review_items() == [
                    {"id": 1, "raw_text": "preserved", "suggested_text": ""},
                    {"id": 2, "raw_text": "new item", "suggested_text": ""},
                ]
                raise OSError("publication failed")
            return real_replace(source, destination)

        with (
            patch(
                "normflow.mapping_service.os.replace",
                side_effect=fail_new_csv_publication,
            ),
            pytest.raises(OSError, match="publication failed"),
        ):
            service.import_records_for_review(
                str(next_batch), "name", semantic=False, llm=False,
            )

        assert committed_before_publication
        assert service.list_review_items() == [
            {"id": 1, "raw_text": "preserved", "suggested_text": ""},
        ]
        assert retained_batch.read_bytes() == previous_batch.read_bytes()
        assert list(retained_batch.parent.iterdir()) == [retained_batch]


def test_database_commit_failure_restores_previous_csv_and_rolls_back_batch():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(project)

        previous_batch = project / "previous.csv"
        _write_csv(previous_batch, [{"name": "preserved"}], ["name"])
        service.import_records_for_review(
            str(previous_batch), "name", semantic=False, llm=False,
        )
        retained_batch = project / ".batches" / "current.csv"

        next_batch = project / "next.csv"
        _write_csv(next_batch, [{"name": "new item"}], ["name"])
        with (
            patch(
                "normflow.mapping_service._Session.commit",
                side_effect=RuntimeError("commit failed"),
            ),
            pytest.raises(RuntimeError, match="commit failed"),
        ):
            service.import_records_for_review(
                str(next_batch), "name", semantic=False, llm=False,
            )

        assert service.list_review_items() == [
            {"id": 1, "raw_text": "preserved", "suggested_text": ""}
        ]
        assert retained_batch.read_bytes() == previous_batch.read_bytes()
        assert list(retained_batch.parent.iterdir()) == [retained_batch]


def test_database_and_csv_restore_failure_leaves_no_inconsistent_batch_csv():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(project)

        previous_batch = project / "previous.csv"
        _write_csv(previous_batch, [{"name": "preserved"}], ["name"])
        service.import_records_for_review(
            str(previous_batch), "name", semantic=False, llm=False,
        )
        retained_batch = project / ".batches" / "current.csv"

        next_batch = project / "next.csv"
        _write_csv(next_batch, [{"name": "new item"}], ["name"])
        real_replace = os.replace

        def fail_previous_csv_restore(source, destination):
            if Path(source).name.startswith(".previous-"):
                raise OSError("restore failed")
            return real_replace(source, destination)

        with (
            patch(
                "normflow.mapping_service.os.replace",
                side_effect=fail_previous_csv_restore,
            ),
            patch(
                "normflow.mapping_service._Session.commit",
                side_effect=RuntimeError("commit failed"),
            ),
            pytest.raises(OSError, match="restore failed"),
        ):
            service.import_records_for_review(
                str(next_batch), "name", semantic=False, llm=False,
            )

        assert service.list_review_items() == [
            {"id": 1, "raw_text": "preserved", "suggested_text": ""}
        ]
        assert not retained_batch.exists()
        assert list(retained_batch.parent.iterdir()) == []


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
        seed_mappings(project, [("colour", "color")])
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

        ms.import_records_for_review(
            str(csv_path), "name", semantic=False, llm=False,
        )

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

        ms.import_records_for_review(
            str(csv_path), "name", semantic=False, llm=False,
        )

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
