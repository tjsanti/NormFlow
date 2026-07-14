"""Behavior tests for the Review Item lifecycle."""

import sqlite3
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from normflow.mapping_service import BulkAcceptResult, MappingService
from normflow.project_service import init_project
from tests.helpers import import_blank_review_items, import_suggested_review_items


def test_opening_legacy_project_migrates_only_pending_suggestions_to_review_items():
    """Opening an existing Project preserves pending work and approved Mappings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        database = project / "normflow.db"
        with sqlite3.connect(database) as connection:
            connection.executescript(
                """
                CREATE TABLE examplemapping (
                    id INTEGER PRIMARY KEY,
                    raw_text TEXT NOT NULL,
                    normalized_text TEXT NOT NULL
                );
                CREATE TABLE suggestion (
                    id INTEGER PRIMARY KEY,
                    raw_text TEXT NOT NULL,
                    suggested_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                INSERT INTO examplemapping VALUES (7, 'colour', 'color');
                INSERT INTO suggestion VALUES
                    (3, 'centre', 'center', 'pending', '2026-01-02 00:00:00'),
                    (4, 'organise', 'organize', 'accepted', '2026-01-01 00:00:00');
                """
            )

        service = MappingService(str(project))

        assert service.list_review_items() == [
            {"id": 3, "raw_text": "centre", "suggested_text": "center"}
        ]
        assert service.project_info()["mappings"] == 1
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT id, raw_text, normalized_text FROM examplemapping"
            ).fetchall() == [(7, "colour", "color")]
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'suggestion'"
            ).fetchall() == []


def test_imported_review_items_are_listed_oldest_first_with_stable_ids():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        records = project / "records.csv"
        records.write_text("name\nfirst\nsecond\n", encoding="utf-8")
        service = MappingService(str(project))

        service.import_records_for_review(
            str(records), "name", semantic=False, llm=False
        )

        assert service.list_review_items() == [
            {"id": 1, "raw_text": "first", "suggested_text": ""},
            {"id": 2, "raw_text": "second", "suggested_text": ""},
        ]
        assert service.list_review_items() == service.list_review_items()


def test_accept_trims_mapping_text_and_removes_review_item():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(str(project))
        import_suggested_review_items(
            project,
            [("o2 sensor", "  Oxygen Sensor  ")],
        )
        initial_mapping_count = service.project_info()["mappings"]

        service.accept_review_item(1)

        assert service.list_review_items() == []
        assert service.lookup("o2 sensor", semantic=False, llm=False)[0].suggested_text == "Oxygen Sensor"
        assert service.project_info() == {
            "project": str(project.resolve()),
            "database": str((project / "normflow.db").resolve()),
            "mappings": initial_mapping_count + 1,
            "review_items": 0,
        }


def test_bulk_accept_creates_all_mappings_and_removes_all_selected_items():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(str(project))
        import_suggested_review_items(project, [
            ("o2 sensor", "  Oxygen Sensor  "),
            ("fuel pump", "Fuel Pump"),
            ("keep me", "Keep Me"),
        ])
        initial_mapping_count = service.project_info()["mappings"]

        result = service.accept_review_items([1, 2])

        assert result == BulkAcceptResult(accepted=2)
        assert service.list_review_items() == [
            {"id": 3, "raw_text": "keep me", "suggested_text": "Keep Me"}
        ]
        assert service.lookup("o2 sensor", semantic=False, llm=False)[0].suggested_text == "Oxygen Sensor"
        assert service.lookup("fuel pump", semantic=False, llm=False)[0].suggested_text == "Fuel Pump"
        assert service.project_info()["mappings"] == initial_mapping_count + 2


@pytest.mark.parametrize(
    ("record_ids", "message"),
    [
        ([], "Select at least one Review Item"),
        ([0], "Review Item IDs must be positive integers"),
        ([1, 1], "Review Item IDs must not contain duplicates"),
    ],
)
def test_bulk_accept_rejects_invalid_ids(record_ids, message):
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(str(project))

        with pytest.raises(ValueError, match=message):
            service.accept_review_items(record_ids)


def test_bulk_accept_stale_item_rolls_back_the_full_selection():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(str(project))
        import_blank_review_items(project, ["o2 sensor"])

        with pytest.raises(ValueError, match="Review Items with IDs 99 are no longer pending"):
            service.accept_review_items([1, 99])

        assert service.list_review_items() == [
            {"id": 1, "raw_text": "o2 sensor", "suggested_text": ""}
        ]
        assert service.project_info()["mappings"] == 0


def test_bulk_accept_blank_suggestion_rolls_back_the_full_selection():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(str(project))
        import_suggested_review_items(
            project,
            [("o2 sensor", "Oxygen Sensor")],
        )
        import_blank_review_items(project, ["unknown"])
        initial_mapping_count = service.project_info()["mappings"]

        with pytest.raises(ValueError, match="Review Items with IDs 2 have blank Suggestions"):
            service.accept_review_items([1, 2])

        assert len(service.list_review_items()) == 2
        assert service.project_info()["mappings"] == initial_mapping_count


def test_bulk_accept_mapping_failure_rolls_back_the_full_selection():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(str(project))
        import_suggested_review_items(project, [
            ("o2 sensor", "Oxygen Sensor"),
            ("fuel pump", "Fuel Pump"),
        ])
        initial_mapping_count = service.project_info()["mappings"]
        with sqlite3.connect(project / "normflow.db") as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_fuel_pump BEFORE INSERT ON examplemapping
                WHEN NEW.raw_text = 'fuel pump'
                BEGIN SELECT RAISE(ABORT, 'mapping insert rejected'); END
                """
            )

        with pytest.raises(ValueError, match="Could not accept selected Review Items; no changes were made"):
            service.accept_review_items([1, 2])

        assert len(service.list_review_items()) == 2
        assert service.project_info()["mappings"] == initial_mapping_count


def test_accept_rejects_blank_text_without_changing_review_item_or_mappings():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(str(project))
        import_blank_review_items(project, ["unknown"])

        try:
            service.accept_review_item(1)
        except ValueError as error:
            assert str(error) == "Normalized text must not be blank"
        else:
            raise AssertionError("blank normalized text was accepted")

        assert service.list_review_items() == [
            {"id": 1, "raw_text": "unknown", "suggested_text": ""}
        ]
        assert service.project_info()["mappings"] == 0


def test_accept_uses_trimmed_replacement_text_and_removes_review_item():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(str(project))
        import_blank_review_items(project, ["o2 sensor"])

        service.accept_review_item(1, "  Oxygen Sensor  ")

        assert service.list_review_items() == []
        assert service.lookup("o2 sensor", semantic=False, llm=False)[0].suggested_text == "Oxygen Sensor"


def test_accept_rejects_blank_replacement_text_without_changing_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(str(project))
        import_blank_review_items(project, ["o2 sensor"])

        with pytest.raises(ValueError, match="Normalized text must not be blank"):
            service.accept_review_item(1, " \t ")

        assert service.list_review_items() == [
            {"id": 1, "raw_text": "o2 sensor", "suggested_text": ""}
        ]
        assert service.project_info()["mappings"] == 0


def test_accept_rolls_back_review_item_removal_when_mapping_insert_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        service = MappingService(str(project))
        import_blank_review_items(project, ["o2 sensor"])
        with sqlite3.connect(project / "normflow.db") as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_mapping BEFORE INSERT ON examplemapping
                BEGIN SELECT RAISE(ABORT, 'mapping insert rejected'); END
                """
            )

        with pytest.raises(IntegrityError):
            service.accept_review_item(1, "Oxygen Sensor")

        assert service.list_review_items() == [
            {"id": 1, "raw_text": "o2 sensor", "suggested_text": ""}
        ]
        assert service.project_info()["mappings"] == 0


def test_review_item_ids_are_not_reused_after_acceptance():
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        init_project(str(project))
        first_batch = project / "first.csv"
        first_batch.write_text("name\nfirst\nsecond\n", encoding="utf-8")
        service = MappingService(str(project))
        service.import_records_for_review(
            str(first_batch), "name", semantic=False, llm=False
        )
        service.accept_review_item(2, "Second")
        second_batch = project / "second.csv"
        second_batch.write_text("name\nthird\n", encoding="utf-8")

        service.import_records_for_review(
            str(second_batch), "name", semantic=False, llm=False
        )

        assert service.list_review_items() == [
            {"id": 1, "raw_text": "first", "suggested_text": ""},
            {"id": 3, "raw_text": "third", "suggested_text": ""},
        ]
