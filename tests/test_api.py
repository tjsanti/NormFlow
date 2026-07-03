"""Tests for FastAPI endpoints."""

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from normflow.api import app
from normflow.workspace import init_workspace


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
        assert "suggestions" in data


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
