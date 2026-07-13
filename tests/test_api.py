"""Tests for FastAPI endpoints."""

import sqlite3
import tempfile
from pathlib import Path
import re

from fastapi.testclient import TestClient

from normflow.api import app
from normflow.mapping_service import MappingService, ReviewItem
from normflow.workspace import init_workspace


def test_production_ui_and_api_are_served_from_same_origin():
    """The FastAPI app serves both the browser shell and project API."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = init_workspace(str(Path(tmpdir) / "project"))
        client = TestClient(app)

        page = client.get("/")
        info = client.get(
            "/workspace/info",
            headers={"X-Normflow-Workspace": str(ws)},
        )

        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert "NormFlow" in page.text
        assert info.status_code == 200

        script_path = re.search(r'<script[^>]+src="([^"]+)"', page.text).group(1)
        script = client.get(script_path)
        assert script.status_code == 200
        assert "javascript" in script.headers["content-type"]


def test_workspace_validation_returns_canonical_path_and_actionable_error():
    """Project validation resolves valid paths and explains invalid ones."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = init_workspace(str(Path(tmpdir) / "projects" / "example"))
        entered_path = project / ".." / "example"
        client = TestClient(app)

        valid = client.get(
            "/workspace/info",
            headers={"X-Normflow-Workspace": str(entered_path)},
        )
        invalid = client.get(
            "/workspace/info",
            headers={"X-Normflow-Workspace": str(Path(tmpdir) / "not-a-project")},
        )

        assert valid.status_code == 200
        assert valid.json()["workspace"] == str(project)
        assert invalid.status_code == 422
        assert "no database found" in invalid.json()["detail"]


def test_workspace_info_returns_stats():
    """GET /workspace/info returns workspace stats."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())
        init_workspace(ws)

        response = TestClient(app).get(
            "/workspace/info",
            headers={"X-Normflow-Workspace": ws},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["workspace"] == ws
        assert "mappings" in data
        assert data["review_items"] == 0
        assert "suggestions" not in data


def test_workspace_info_rejects_missing_header():
    """Missing workspace header returns 422."""
    response = TestClient(app).get("/workspace/info")
    assert response.status_code == 422


def test_workspace_info_rejects_invalid_workspace():
    """Non-existent workspace returns 422."""
    response = TestClient(app).get(
        "/workspace/info",
        headers={"X-Normflow-Workspace": "/nonexistent/path"},
    )
    assert response.status_code == 422


def test_review_items_route_lists_pending_work():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())
        init_workspace(ws)
        client = TestClient(app)
        headers = {"X-Normflow-Workspace": ws}
        imported = client.post(
            "/import/records?column=name&semantic=false&llm=false",
            headers=headers,
            files={"file": ("records.csv", b"name\no2 sensor\n", "text/csv")},
        )
        assert imported.status_code == 200
        assert imported.json() == {
            "auto_committed": 0,
            "review_items": 1,
            "skipped": 0,
        }

        response = client.get("/review-items", headers=headers)

        assert response.status_code == 200
        assert response.json() == [
            {"id": 1, "raw_text": "o2 sensor", "suggested_text": ""}
        ]


def test_accept_review_item_route_creates_mapping_and_removes_item():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())
        init_workspace(ws)
        service = MappingService(ws)
        with service.session() as session:
            session.add(ReviewItem(raw_text="o2 sensor", suggested_text="O2 Sensor"))
            session.commit()
        client = TestClient(app)
        headers = {"X-Normflow-Workspace": ws}

        response = client.post("/review-items/1/accept", headers=headers)

        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}
        assert client.get("/review-items", headers=headers).json() == []
        assert client.get("/workspace/info", headers=headers).json()["mappings"] == 1


def test_bulk_accept_review_items_route_returns_typed_count_and_commits_all():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())
        init_workspace(ws)
        service = MappingService(ws)
        with service.session() as session:
            session.add_all([
                ReviewItem(raw_text="o2 sensor", suggested_text="Oxygen Sensor"),
                ReviewItem(raw_text="fuel pump", suggested_text="Fuel Pump"),
            ])
            session.commit()
        client = TestClient(app)
        headers = {"X-Normflow-Workspace": ws}

        response = client.post(
            "/review-items/bulk-accept",
            headers=headers,
            json={"review_item_ids": [1, 2]},
        )

        assert response.status_code == 200
        assert response.json() == {"accepted": 2}
        assert client.get("/review-items", headers=headers).json() == []
        assert client.get("/workspace/info", headers=headers).json()["mappings"] == 2


def test_bulk_accept_route_reports_invalid_stale_and_blank_selections_without_changes():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())
        init_workspace(ws)
        service = MappingService(ws)
        with service.session() as session:
            session.add_all([
                ReviewItem(raw_text="o2 sensor", suggested_text="Oxygen Sensor"),
                ReviewItem(raw_text="unknown", suggested_text="  "),
            ])
            session.commit()
        client = TestClient(app)
        headers = {"X-Normflow-Workspace": ws}

        invalid = client.post(
            "/review-items/bulk-accept", headers=headers, json={"review_item_ids": []}
        )
        malformed = client.post(
            "/review-items/bulk-accept", headers=headers, json={"review_item_ids": [True]}
        )
        stale = client.post(
            "/review-items/bulk-accept", headers=headers, json={"review_item_ids": [1, 99]}
        )
        blank = client.post(
            "/review-items/bulk-accept", headers=headers, json={"review_item_ids": [1, 2]}
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
        assert len(client.get("/review-items", headers=headers).json()) == 2
        assert client.get("/workspace/info", headers=headers).json()["mappings"] == 0


def test_bulk_accept_route_reports_mapping_failure_and_rolls_back_every_item():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())
        init_workspace(ws)
        service = MappingService(ws)
        with service.session() as session:
            session.add_all([
                ReviewItem(raw_text="o2 sensor", suggested_text="Oxygen Sensor"),
                ReviewItem(raw_text="fuel pump", suggested_text="Fuel Pump"),
            ])
            session.commit()
        with sqlite3.connect(Path(ws) / "normflow.db") as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_fuel_pump BEFORE INSERT ON examplemapping
                WHEN NEW.raw_text = 'fuel pump'
                BEGIN SELECT RAISE(ABORT, 'mapping insert rejected'); END
                """
            )
        client = TestClient(app)
        headers = {"X-Normflow-Workspace": ws}

        response = client.post(
            "/review-items/bulk-accept",
            headers=headers,
            json={"review_item_ids": [1, 2]},
        )

        assert response.status_code == 500
        assert response.json() == {
            "detail": "Could not accept selected Review Items; no changes were made"
        }
        assert len(client.get("/review-items", headers=headers).json()) == 2
        assert client.get("/workspace/info", headers=headers).json()["mappings"] == 0


def test_accept_review_item_route_reports_a_stale_item_as_a_conflict():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())
        init_workspace(ws)
        response = TestClient(app).post(
            "/review-items/99/accept",
            headers={"X-Normflow-Workspace": ws},
        )

        assert response.status_code == 409
        assert response.json() == {"detail": "Review Item with id 99 not found"}


def test_accept_review_item_route_rejects_blank_suggestion_without_removing_item():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())
        init_workspace(ws)
        service = MappingService(ws)
        with service.session() as session:
            session.add(ReviewItem(raw_text="unknown", suggested_text="  "))
            session.commit()
        client = TestClient(app)
        headers = {"X-Normflow-Workspace": ws}

        response = client.post("/review-items/1/accept", headers=headers)

        assert response.status_code == 422
        assert response.json() == {"detail": "Normalized text must not be blank"}
        assert client.get("/review-items", headers=headers).json() == [
            {"id": 1, "raw_text": "unknown", "suggested_text": "  "}
        ]


def test_edit_and_accept_review_item_route_uses_edited_text():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())
        init_workspace(ws)
        service = MappingService(ws)
        with service.session() as session:
            session.add(ReviewItem(raw_text="o2 sensor", suggested_text="O2 Sensor"))
            session.commit()
        client = TestClient(app)
        headers = {"X-Normflow-Workspace": ws}

        response = client.post(
            "/review-items/1/edit-and-accept",
            headers=headers,
            params={"normalized_text": "  Oxygen Sensor  "},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}
        assert service.lookup("o2 sensor", semantic=False, llm=False)[0].suggested_text == "Oxygen Sensor"


def test_edit_and_accept_review_item_route_reports_a_stale_item_as_a_conflict():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())
        init_workspace(ws)

        response = TestClient(app).post(
            "/review-items/99/edit-and-accept",
            headers={"X-Normflow-Workspace": ws},
            params={"normalized_text": "Something"},
        )

        assert response.status_code == 409
        assert response.json() == {"detail": "Review Item with id 99 not found"}


def test_old_suggestion_review_routes_are_removed():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())
        init_workspace(ws)
        client = TestClient(app)
        headers = {"X-Normflow-Workspace": ws}

        assert client.get("/suggestions", headers=headers).status_code == 404
        assert client.post("/suggestions/1/accept", headers=headers).status_code == 404
        assert client.post(
            "/suggestions/1/edit",
            headers=headers,
            params={"normalized_text": "edited"},
        ).status_code == 404
