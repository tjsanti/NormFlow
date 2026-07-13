"""Tests for FastAPI endpoints."""

import sqlite3
import tempfile
from pathlib import Path
import re

from fastapi.testclient import TestClient

from normflow.api import create_app
from normflow.mapping_service import MappingService, ReviewItem
from normflow.project import resolve_project
from normflow.project_service import init_project


def test_application_is_bound_to_one_canonical_project():
    """Project information comes from the Project bound at app construction."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = init_project(str(Path(tmpdir) / "project"))
        project = resolve_project(project_root / "input")

        response = TestClient(create_app(project)).get("/project/info")

        assert response.status_code == 200
        assert response.json() == {
            "project": str(project_root),
            "database": str(project_root / "normflow.db"),
            "mappings": 0,
            "review_items": 0,
        }


def test_bound_application_imports_and_lists_review_items_without_a_project_selector():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = init_project(str(Path(tmpdir) / "project"))
        client = TestClient(create_app(resolve_project(project_root)))

        imported = client.post(
            "/import/records?column=name&semantic=false&llm=false",
            files={"file": ("records.csv", b"name\no2 sensor\n", "text/csv")},
        )
        review_items = client.get("/review-items")

        assert imported.status_code == 200
        assert imported.json() == {
            "auto_committed": 0,
            "review_items": 1,
            "skipped": 0,
        }
        assert review_items.json() == [
            {"id": 1, "raw_text": "o2 sensor", "suggested_text": ""}
        ]


def test_bound_application_retains_mapping_import_export_and_index_http_contract():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = init_project(str(Path(tmpdir) / "project"))
        client = TestClient(create_app(resolve_project(project_root)))

        mappings = client.post(
            "/import/mappings?source_column=raw&target_column=clean",
            files={
                "file": (
                    "mappings.csv",
                    b"raw,clean\no2 sensor,Oxygen Sensor\n",
                    "text/csv",
                )
            },
        )
        records = client.post(
            "/import/records?column=name&semantic=false&llm=false",
            files={"file": ("records.csv", b"name\no2 sensor\n", "text/csv")},
        )
        exported = client.post("/export?source_column=name")

        assert mappings.json() == {"imported": 1, "skipped": 0}
        assert records.json() == {
            "auto_committed": 1,
            "review_items": 0,
            "skipped": 0,
        }
        assert exported.status_code == 200
        assert exported.text == "name,normalized_text\no2 sensor,Oxygen Sensor\n"
        assert "/index/build" in client.get("/openapi.json").json()["paths"]


def test_bound_application_cannot_be_retargeted_by_cwd_header_or_query(
    tmp_path: Path,
    monkeypatch,
):
    first_root = init_project(str(tmp_path / "first"))
    second_root = init_project(str(tmp_path / "second"))
    client = TestClient(create_app(resolve_project(first_root)))

    monkeypatch.chdir(second_root)
    response = client.get(
        "/project/info",
        headers={"X-Normflow-Workspace": str(second_root)},
        params={"project": str(second_root)},
    )

    assert response.json()["project"] == str(first_root)


def test_independent_applications_do_not_cross_pollinate(tmp_path: Path):
    first_root = init_project(str(tmp_path / "first"))
    second_root = init_project(str(tmp_path / "second"))
    first = TestClient(create_app(resolve_project(first_root)))
    second = TestClient(create_app(resolve_project(second_root)))

    first.post(
        "/import/records?column=name&semantic=false&llm=false",
        files={"file": ("records.csv", b"name\no2 sensor\n", "text/csv")},
    )

    assert len(first.get("/review-items").json()) == 1
    assert second.get("/review-items").json() == []


def test_production_ui_and_api_are_served_from_same_origin():
    """The FastAPI app serves both the browser shell and project API."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = init_project(str(Path(tmpdir) / "project"))
        client = TestClient(create_app(resolve_project(project_root)))

        page = client.get("/")
        info = client.get("/project/info")

        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert "NormFlow" in page.text
        assert info.status_code == 200

        script_path = re.search(r'<script[^>]+src="([^"]+)"', page.text).group(1)
        script = client.get(script_path)
        assert script.status_code == 200
        assert "javascript" in script.headers["content-type"]


def test_json_endpoints_publish_explicit_response_schemas(tmp_path: Path):
    project_root = init_project(tmp_path / "project")
    schema = TestClient(create_app(resolve_project(project_root))).get(
        "/openapi.json"
    ).json()

    expected_models = {
        ("post", "/import/mappings"): "ImportMappingsResponse",
        ("post", "/import/records"): "ImportRecordsResponse",
        ("post", "/review-items/{record_id}/accept"): "StatusResponse",
        ("post", "/review-items/{record_id}/edit-and-accept"): "StatusResponse",
        ("post", "/index/build"): "IndexBuildResponse",
    }
    for (method, path), model in expected_models.items():
        response_schema = schema["paths"][path][method]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        assert response_schema["$ref"].endswith(f"/{model}")

    review_schema = schema["paths"]["/review-items"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert review_schema["items"]["$ref"].endswith("/ReviewItemResponse")

    edit_request_schema = schema["paths"][
        "/review-items/{record_id}/edit-and-accept"
    ]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert edit_request_schema["$ref"].endswith("/EditAndAcceptRequest")


def test_project_info_returns_stats():
    """GET /project/info returns Project stats."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)

        response = TestClient(create_app(resolve_project(project_root))).get("/project/info")

        assert response.status_code == 200
        data = response.json()
        assert data["project"] == project_root
        assert "mappings" in data
        assert data["review_items"] == 0
        assert "suggestions" not in data


def test_review_items_route_lists_pending_work():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        client = TestClient(create_app(resolve_project(project_root)))
        imported = client.post(
            "/import/records?column=name&semantic=false&llm=false",
            files={"file": ("records.csv", b"name\no2 sensor\n", "text/csv")},
        )
        assert imported.status_code == 200
        assert imported.json() == {
            "auto_committed": 0,
            "review_items": 1,
            "skipped": 0,
        }

        response = client.get("/review-items")

        assert response.status_code == 200
        assert response.json() == [
            {"id": 1, "raw_text": "o2 sensor", "suggested_text": ""}
        ]


def test_accept_review_item_route_creates_mapping_and_removes_item():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        service = MappingService(project_root)
        with service.session() as session:
            session.add(ReviewItem(raw_text="o2 sensor", suggested_text="O2 Sensor"))
            session.commit()
        client = TestClient(create_app(resolve_project(project_root)))

        response = client.post("/review-items/1/accept")

        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}
        assert client.get("/review-items").json() == []
        assert client.get("/project/info").json()["mappings"] == 1


def test_bulk_accept_review_items_route_returns_typed_count_and_commits_all():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        service = MappingService(project_root)
        with service.session() as session:
            session.add_all([
                ReviewItem(raw_text="o2 sensor", suggested_text="Oxygen Sensor"),
                ReviewItem(raw_text="fuel pump", suggested_text="Fuel Pump"),
            ])
            session.commit()
        client = TestClient(create_app(resolve_project(project_root)))

        response = client.post(
            "/review-items/bulk-accept",
            json={"review_item_ids": [1, 2]},
        )

        assert response.status_code == 200
        assert response.json() == {"accepted": 2}
        assert client.get("/review-items").json() == []
        assert client.get("/project/info").json()["mappings"] == 2


def test_bulk_accept_route_reports_invalid_stale_and_blank_selections_without_changes():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        service = MappingService(project_root)
        with service.session() as session:
            session.add_all([
                ReviewItem(raw_text="o2 sensor", suggested_text="Oxygen Sensor"),
                ReviewItem(raw_text="unknown", suggested_text="  "),
            ])
            session.commit()
        client = TestClient(create_app(resolve_project(project_root)))

        invalid = client.post(
            "/review-items/bulk-accept", json={"review_item_ids": []}
        )
        malformed = client.post(
            "/review-items/bulk-accept", json={"review_item_ids": [True]}
        )
        stale = client.post(
            "/review-items/bulk-accept", json={"review_item_ids": [1, 99]}
        )
        blank = client.post(
            "/review-items/bulk-accept", json={"review_item_ids": [1, 2]}
        )

        assert (invalid.status_code, invalid.json()) == (
            422, {"detail": "Select at least one Review Item"}
        )
        assert malformed.status_code == 422
        assert malformed.json()["detail"][0]["loc"] == ["body", "review_item_ids", 0]
        assert (stale.status_code, stale.json()) == (
            409, {"detail": "Review Items with IDs 99 are no longer pending"}
        )
        assert (blank.status_code, blank.json()) == (
            422, {"detail": "Review Items with IDs 2 have blank Suggestions"}
        )
        assert len(client.get("/review-items").json()) == 2
        assert client.get("/project/info").json()["mappings"] == 0


def test_bulk_accept_route_reports_mapping_failure_and_rolls_back_every_item():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        service = MappingService(project_root)
        with service.session() as session:
            session.add_all([
                ReviewItem(raw_text="o2 sensor", suggested_text="Oxygen Sensor"),
                ReviewItem(raw_text="fuel pump", suggested_text="Fuel Pump"),
            ])
            session.commit()
        with sqlite3.connect(Path(project_root) / "normflow.db") as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_fuel_pump BEFORE INSERT ON examplemapping
                WHEN NEW.raw_text = 'fuel pump'
                BEGIN SELECT RAISE(ABORT, 'mapping insert rejected'); END
                """
            )
        client = TestClient(create_app(resolve_project(project_root)))

        response = client.post(
            "/review-items/bulk-accept",
            json={"review_item_ids": [1, 2]},
        )

        assert response.status_code == 500
        assert response.json() == {
            "detail": "Could not accept selected Review Items; no changes were made"
        }
        assert len(client.get("/review-items").json()) == 2
        assert client.get("/project/info").json()["mappings"] == 0


def test_accept_review_item_route_reports_a_stale_item_as_a_conflict():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        response = TestClient(create_app(resolve_project(project_root))).post(
            "/review-items/99/accept",
        )

        assert response.status_code == 409
        assert response.json() == {"detail": "Review Item with id 99 not found"}


def test_accept_review_item_route_rejects_blank_suggestion_without_removing_item():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        service = MappingService(project_root)
        with service.session() as session:
            session.add(ReviewItem(raw_text="unknown", suggested_text="  "))
            session.commit()
        client = TestClient(create_app(resolve_project(project_root)))

        response = client.post("/review-items/1/accept")

        assert response.status_code == 422
        assert response.json() == {"detail": "Normalized text must not be blank"}
        assert client.get("/review-items").json() == [
            {"id": 1, "raw_text": "unknown", "suggested_text": "  "}
        ]


def test_edit_and_accept_review_item_route_uses_edited_text():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        service = MappingService(project_root)
        with service.session() as session:
            session.add(ReviewItem(raw_text="o2 sensor", suggested_text="O2 Sensor"))
            session.commit()
        client = TestClient(create_app(resolve_project(project_root)))

        response = client.post(
            "/review-items/1/edit-and-accept",
            json={"normalized_text": "  Oxygen Sensor  "},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}
        assert service.lookup("o2 sensor", semantic=False, llm=False)[0].suggested_text == "Oxygen Sensor"


def test_edit_and_accept_review_item_route_reports_a_stale_item_as_a_conflict():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)

        response = TestClient(create_app(resolve_project(project_root))).post(
            "/review-items/99/edit-and-accept",
            json={"normalized_text": "Something"},
        )

        assert response.status_code == 409
        assert response.json() == {"detail": "Review Item with id 99 not found"}


def test_edit_and_accept_review_item_route_rejects_query_text_without_mutation():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        service = MappingService(project_root)
        with service.session() as session:
            session.add(ReviewItem(raw_text="o2 sensor", suggested_text="O2 Sensor"))
            session.commit()
        client = TestClient(create_app(resolve_project(project_root)))

        response = client.post(
            "/review-items/1/edit-and-accept",
            params={"normalized_text": "Oxygen Sensor"},
        )

        assert response.status_code == 422
        assert client.get("/review-items").json() == [
            {"id": 1, "raw_text": "o2 sensor", "suggested_text": "O2 Sensor"}
        ]


def test_edit_and_accept_review_item_route_rejects_invalid_payloads_without_mutation():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        service = MappingService(project_root)
        with service.session() as session:
            session.add(ReviewItem(raw_text="o2 sensor", suggested_text="O2 Sensor"))
            session.commit()
        client = TestClient(create_app(resolve_project(project_root)))

        for body in (
            {},
            {"normalized_text": ["Oxygen Sensor"]},
            {"normalized_text": "   "},
        ):
            response = client.post(
                "/review-items/1/edit-and-accept",
                json=body,
            )
            assert response.status_code == 422

        assert client.get("/review-items").json() == [
            {"id": 1, "raw_text": "o2 sensor", "suggested_text": "O2 Sensor"}
        ]


def test_old_suggestion_review_routes_are_removed():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        client = TestClient(create_app(resolve_project(project_root)))

        assert client.get("/suggestions").status_code == 404
        assert client.post("/suggestions/1/accept").status_code == 404
        assert client.post(
            "/suggestions/1/edit",
            params={"normalized_text": "edited"},
        ).status_code == 404
