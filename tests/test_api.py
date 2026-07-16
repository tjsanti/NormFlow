"""Tests for FastAPI endpoints."""

import tempfile
import time
from pathlib import Path
import re
from unittest.mock import MagicMock, call, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from normflow.api import build_index as build_index_endpoint
from normflow.api import create_app, get_project_service
from normflow.embedding_model import EmbeddingModelUnavailableError
from normflow.mapping_service import (
    BulkAcceptError,
    BulkAcceptPersistenceError,
    BulkAcceptResult,
    BulkAcceptStaleItemsError,
    MappingService,
    ReviewItemNotFoundError,
)
from normflow.project import resolve_project
from normflow.project_service import init_project


def _client_with_fake_service(project_root: str) -> tuple[TestClient, MagicMock]:
    """Bind an interface fake to the HTTP adapter under test."""
    app = create_app(resolve_project(project_root))
    service = MagicMock(spec=MappingService)
    app.dependency_overrides[get_project_service] = lambda: service
    return TestClient(app), service


def _await_batch_import_terminal(client: TestClient, response) -> dict:
    assert response.status_code == 202
    run = response.json()
    deadline = time.monotonic() + 5
    while run["status"] == "active" and time.monotonic() < deadline:
        run = client.get(response.headers["location"]).json()
        time.sleep(0.01)
    return run


def _await_batch_import(client: TestClient, response) -> dict:
    run = _await_batch_import_terminal(client, response)
    assert run["status"] == "succeeded", run
    return run


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
            "semantic_index_status": "missing",
            "semantic_index_warning": "The semantic index will be built before the next semantic Suggestion.",
        }


def test_bound_application_imports_and_lists_review_items_without_a_project_selector():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = init_project(str(Path(tmpdir) / "project"))
        client = TestClient(create_app(resolve_project(project_root)))

        csv_path = Path(tmpdir) / "records.csv"
        csv_path.write_text("name\no2 sensor\n", encoding="utf-8")
        MappingService(project_root).import_records_for_review(
            str(csv_path), "name", semantic=False, llm=False
        )
        review_items = client.get("/review-items")

        assert review_items.json() == [
            {"id": 1, "raw_text": "o2 sensor", "suggested_text": ""}
        ]

        accepted = client.post(
            "/review-items/1/accept",
            json={"normalized_text": "Oxygen Sensor"},
        )

        assert accepted.status_code == 200
        assert client.get("/review-items").json() == []
        assert client.get("/project/info").json()["mappings"] == 1


def test_import_reports_failed_automatic_index_refresh():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = init_project(str(Path(tmpdir) / "project"))
        client = TestClient(create_app(resolve_project(project_root)))
        mappings = Path(tmpdir) / "mappings.csv"
        mappings.write_text(
            "raw,clean\no2 sensor,Oxygen Sensor\n", encoding="utf-8"
        )
        MappingService(project_root).import_mappings(str(mappings), "raw", "clean")

        with patch(
            "normflow.semantic_index._ensure_model",
            side_effect=RuntimeError("model unavailable"),
        ):
            response = client.post(
                "/batch-import-runs?column=name",
                files={"file": ("records.csv", b"name\no2 sensor\n", "text/csv")},
            )
            run = _await_batch_import(client, response)

        assert run["result"]["semantic_index_status"] == "missing"
        assert "semantic and LLM Suggestions are unavailable" in run["result"]["semantic_index_warning"]
        assert "normflow index build" in run["result"]["semantic_index_warning"]


@pytest.mark.parametrize(
    ("contents", "detail"),
    [
        (
            b"other\nvalue\n",
            "CSV does not contain a column named 'name'. Available columns: other",
        ),
        (b"name\n\xff\n", "CSV must be UTF-8 text"),
        (
            b'name,notes\n"o2 sensor,urgent\n',
            "CSV could not be parsed: unexpected end of data. "
            "Available columns: name, notes",
        ),
    ],
    ids=["missing-header", "non-utf8", "malformed"],
)
def test_import_records_reports_actionable_csv_validation_errors(
    tmp_path: Path,
    contents: bytes,
    detail: str,
):
    project_root = init_project(tmp_path / "project")
    client = TestClient(
        create_app(resolve_project(project_root)),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/batch-import-runs?column=name",
        files={"file": ("records.csv", contents, "text/csv")},
    )

    run = _await_batch_import_terminal(client, response)
    assert response.headers["location"].endswith(run["id"])
    assert run["status"] == "failed"
    assert run["error"] == detail
    assert run["input_fingerprint"] != "pending"


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
        assert mappings.json() == {"imported": 1, "skipped": 0}
        with patch(
            "normflow.semantic_index._ensure_model",
            side_effect=RuntimeError("model unavailable"),
        ):
            records = client.post(
                "/batch-import-runs?column=name",
                files={"file": ("records.csv", b"name\no2 sensor\n", "text/csv")},
            )
            run = _await_batch_import(client, records)
        assert run["result"] == {
            "auto_committed": 1,
            "review_items": 0,
            "skipped": 0,
            "semantic_index_status": "missing",
            "semantic_index_warning": (
                "Automatic semantic index refresh failed; semantic and LLM "
                "Suggestions are unavailable. Exact matching remains available. "
                "Run `normflow index build` to retry."
            ),
        }
        exported = client.post("/export?source_column=name")
        assert exported.status_code == 200
        assert exported.text == "name,normalized_text\no2 sensor,Oxygen Sensor\n"
        assert "/index/build" in client.get("/openapi.json").json()["paths"]


def test_index_build_reports_an_actionable_missing_model_error(tmp_path: Path):
    project_root = init_project(tmp_path / "project")
    client, service = _client_with_fake_service(str(project_root))
    service.build_index.side_effect = EmbeddingModelUnavailableError(
        "NormFlow's required local embedding model is missing. Reinstall NormFlow."
    )

    response = client.post("/index/build")

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "NormFlow's required local embedding model is missing. "
            "Reinstall NormFlow."
        )
    }


def test_index_build_suppresses_handled_model_error_context():
    service = MagicMock(spec=MappingService)
    service.build_index.side_effect = EmbeddingModelUnavailableError(
        "local embedding model unavailable"
    )

    with pytest.raises(HTTPException) as raised:
        build_index_endpoint(service=service)

    assert raised.value.status_code == 503
    assert raised.value.__suppress_context__ is True


def test_mapping_import_validation_reports_available_csv_headers(tmp_path: Path):
    project_root = init_project(tmp_path / "project")
    client = TestClient(
        create_app(resolve_project(project_root)),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/import/mappings?source_column=missing&target_column=approved",
        files={
            "file": (
                "mappings.csv",
                b"raw,approved\no2 sensor,Oxygen Sensor\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": (
            "CSV does not contain a column named 'missing'. "
            "Available columns: raw, approved"
        )
    }


def test_mapping_import_rejects_matching_source_and_target_columns(tmp_path: Path):
    project_root = init_project(tmp_path / "project")
    client = TestClient(create_app(resolve_project(project_root)))

    response = client.post(
        "/import/mappings?source_column=raw&target_column=raw",
        files={
            "file": (
                "mappings.csv",
                b"raw,approved\no2 sensor,Oxygen Sensor\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Source and target columns must differ"}
    assert client.get("/project/info").json()["mappings"] == 0


def test_mapping_import_reports_short_rows_with_available_headers(tmp_path: Path):
    project_root = init_project(tmp_path / "project")
    client = TestClient(
        create_app(resolve_project(project_root)),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/import/mappings?source_column=raw&target_column=approved",
        files={"file": ("mappings.csv", b"raw,approved\no2 sensor\n", "text/csv")},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "CSV row 2 does not contain a value for selected column 'approved'"
    }


def test_batch_import_reports_the_row_and_selected_column_for_a_short_row(
    tmp_path: Path,
):
    project_root = init_project(tmp_path / "project")
    client = TestClient(
        create_app(resolve_project(project_root)),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/batch-import-runs?column=name",
        files={"file": ("records.csv", b"id,name\n1\n", "text/csv")},
    )

    run = _await_batch_import_terminal(client, response)
    assert run["status"] == "failed"
    assert run["error"] == (
        "CSV row 2 does not contain a value for selected column 'name'"
    )


def test_mapping_import_rejects_non_utf8_csv_with_a_useful_error(tmp_path: Path):
    project_root = init_project(tmp_path / "project")
    client = TestClient(
        create_app(resolve_project(project_root)),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/import/mappings?source_column=raw&target_column=approved",
        files={"file": ("mappings.csv", b"raw,approved\n\xff,value\n", "text/csv")},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "CSV must be UTF-8 text"}


def test_mapping_import_accepts_utf8_csv_with_a_leading_bom(tmp_path: Path):
    project_root = init_project(tmp_path / "project")
    client = TestClient(create_app(resolve_project(project_root)))

    response = client.post(
        "/import/mappings?source_column=raw&target_column=approved",
        files={
            "file": (
                "mappings.csv",
                b"\xef\xbb\xbfraw,approved\no2 sensor,Oxygen Sensor\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 200
    assert response.json() == {"imported": 1, "skipped": 0}


def test_mapping_import_reports_malformed_csv_with_available_headers(tmp_path: Path):
    project_root = init_project(tmp_path / "project")
    client = TestClient(create_app(resolve_project(project_root)))

    response = client.post(
        "/import/mappings?source_column=raw&target_column=approved",
        files={
            "file": (
                "mappings.csv",
                b'raw,approved\n"o2 sensor,Oxygen Sensor\n',
                "text/csv",
            )
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": (
            "CSV could not be parsed: unexpected end of data. "
            "Available columns: raw, approved"
        )
    }


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

    csv_path = tmp_path / "records.csv"
    csv_path.write_text("name\no2 sensor\n", encoding="utf-8")
    MappingService(first_root).import_records_for_review(
        str(csv_path), "name", semantic=False, llm=False
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
        ("post", "/review-items/{review_item_id}/accept"): "StatusResponse",
        ("post", "/index/build"): "IndexBuildResponse",
    }
    for (method, path), model in expected_models.items():
        response_schema = schema["paths"][path][method]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        assert response_schema["$ref"].endswith(f"/{model}")

    assert "/import/records" not in schema["paths"]
    for method, path in (
        ("post", "/batch-import-runs"),
        ("post", "/batch-import-runs/{run_id}/retry"),
    ):
        response_schema = schema["paths"][path][method]["responses"]["202"][
            "content"
        ]["application/json"]["schema"]
        assert response_schema["$ref"].endswith("/BatchImportRunResponse")

    review_schema = schema["paths"]["/review-items"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert review_schema["items"]["$ref"].endswith("/ReviewItemResponse")

    accept_operation = schema["paths"][
        "/review-items/{review_item_id}/accept"
    ]["post"]
    assert "required" not in accept_operation["requestBody"]
    request_schema = accept_operation["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert request_schema["anyOf"] == [
        {"$ref": "#/components/schemas/AcceptReviewItemRequest"},
        {"type": "null"},
    ]
    normalized_text_schema = schema["components"]["schemas"][
        "AcceptReviewItemRequest"
    ]["properties"]["normalized_text"]
    assert normalized_text_schema["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert "required" not in schema["components"]["schemas"][
        "AcceptReviewItemRequest"
    ]


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
        csv_path = Path(tmpdir) / "records.csv"
        csv_path.write_text("name\no2 sensor\n", encoding="utf-8")
        MappingService(project_root).import_records_for_review(
            str(csv_path), "name", semantic=False, llm=False
        )

        response = client.get("/review-items")

        assert response.status_code == 200
        assert response.json() == [
            {"id": 1, "raw_text": "o2 sensor", "suggested_text": ""}
        ]


@pytest.mark.parametrize("body", [None, {}, {"normalized_text": None}])
def test_accept_review_item_route_uses_suggestion_when_replacement_is_absent(body):
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        client, service = _client_with_fake_service(project_root)

        response = (
            client.post("/review-items/1/accept")
            if body is None
            else client.post("/review-items/1/accept", json=body)
        )

        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}
        service.accept_review_item.assert_called_once_with(1, None)


def test_bulk_accept_review_items_route_returns_typed_count_and_commits_all():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        client, service = _client_with_fake_service(project_root)
        service.accept_review_items.return_value = BulkAcceptResult(accepted=2)

        response = client.post(
            "/review-items/bulk-accept",
            json={"review_item_ids": [1, 2]},
        )

        assert response.status_code == 200
        assert response.json() == {"accepted": 2}
        service.accept_review_items.assert_called_once_with([1, 2])


def test_bulk_accept_route_reports_invalid_stale_and_blank_selections_without_changes():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        client, service = _client_with_fake_service(project_root)
        service.accept_review_items.side_effect = [
            BulkAcceptError("Select at least one Review Item"),
            BulkAcceptStaleItemsError(
                "Review Items with IDs 99 are no longer pending"
            ),
            BulkAcceptError("Review Items with IDs 2 have blank Suggestions"),
        ]

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
        assert service.accept_review_items.call_args_list == [
            call([]),
            call([1, 99]),
            call([1, 2]),
        ]


def test_bulk_accept_route_reports_mapping_failure_and_rolls_back_every_item():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        client, service = _client_with_fake_service(project_root)
        service.accept_review_items.side_effect = BulkAcceptPersistenceError(
            "Could not accept selected Review Items; no changes were made"
        )

        response = client.post(
            "/review-items/bulk-accept",
            json={"review_item_ids": [1, 2]},
        )

        assert response.status_code == 500
        assert response.json() == {
            "detail": "Could not accept selected Review Items; no changes were made"
        }
        service.accept_review_items.assert_called_once_with([1, 2])


def test_accept_review_item_route_reports_a_stale_item_as_a_conflict():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        client, service = _client_with_fake_service(project_root)
        service.accept_review_item.side_effect = ReviewItemNotFoundError(
            "Review Item with id 99 not found"
        )

        response = client.post("/review-items/99/accept")

        assert response.status_code == 409
        assert response.json() == {"detail": "Review Item with id 99 not found"}


def test_accept_review_item_route_rejects_blank_suggestion_without_removing_item():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        client, service = _client_with_fake_service(project_root)
        service.accept_review_item.side_effect = ValueError(
            "Normalized text must not be blank"
        )

        response = client.post("/review-items/1/accept")

        assert response.status_code == 422
        assert response.json() == {"detail": "Normalized text must not be blank"}
        service.accept_review_item.assert_called_once_with(1, None)


def test_accept_review_item_route_uses_replacement_text():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        client, service = _client_with_fake_service(project_root)

        response = client.post(
            "/review-items/1/accept",
            json={"normalized_text": "  Oxygen Sensor  "},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}
        service.accept_review_item.assert_called_once_with(1, "  Oxygen Sensor  ")


@pytest.mark.parametrize(
    "body",
    [
        {"normalized_text": ["Oxygen Sensor"]},
        {"normalized_text": "   "},
    ],
)
def test_accept_review_item_route_rejects_invalid_replacement_without_mutation(body):
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = str(Path(tmpdir).resolve())
        init_project(project_root)
        client, service = _client_with_fake_service(project_root)
        service.accept_review_item.side_effect = ValueError(
            "Normalized text must not be blank"
        )

        response = client.post("/review-items/1/accept", json=body)

        assert response.status_code == 422


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
        assert client.post(
            "/review-items/1/edit-and-accept",
            json={"normalized_text": "edited"},
        ).status_code == 404
