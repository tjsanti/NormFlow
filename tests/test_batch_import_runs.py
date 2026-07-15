import csv
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from normflow.batch_import import ProjectBusyError
from normflow.cli import app
from normflow.mapping_service import MappingService
from normflow.api import create_app
from normflow.project import resolve_project
from normflow.project_service import init_project


def _csv(path: Path, value: str) -> Path:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["name"])
        writer.writeheader()
        writer.writerow({"name": value})
    return path


def test_batch_import_run_is_durable_from_start_through_terminal_status(tmp_path: Path):
    project = init_project(tmp_path / "project")
    service = MappingService(project)
    observed = []

    terminal = service.run_batch_import(
        _csv(tmp_path / "records.csv", "o2 sensor"),
        "name",
        semantic=False,
        llm=False,
        on_started=observed.append,
    )

    assert observed[0]["status"] == "active"
    assert observed[0]["id"] == terminal["id"]
    assert terminal["status"] == "succeeded"
    assert terminal["result"]["review_items"] == 1
    assert MappingService(project).batch_import_status(terminal["id"]) == terminal


def test_independent_service_writer_fails_fast_while_batch_run_owns_project(tmp_path: Path):
    project = init_project(tmp_path / "project")
    batch = _csv(tmp_path / "records.csv", "o2 sensor")
    mappings = tmp_path / "mappings.csv"
    mappings.write_text("raw,clean\no2,Oxygen\n", encoding="utf-8")
    run_started = Event()
    release_run = Event()

    def hold_after_durable_start(_run):
        run_started.set()
        assert release_run.wait(5)

    with ThreadPoolExecutor() as executor:
        future = executor.submit(
            lambda: MappingService(project).run_batch_import(
                batch, "name", semantic=False, llm=False,
                on_started=hold_after_durable_start,
            )
        )
        assert run_started.wait(5)
        active = MappingService(project).batch_import_status()
        assert active["status"] == "active"
        with pytest.raises(ProjectBusyError):
            MappingService(project).import_mappings(mappings, "raw", "clean")
        assert MappingService(project).project_info()["mappings"] == 0
        release_run.set()
        assert future.result(timeout=5)["status"] == "succeeded"


def test_cli_prints_run_id_immediately_and_terminal_run_json(tmp_path: Path, monkeypatch):
    project = init_project(tmp_path / "project")
    batch = _csv(tmp_path / "records.csv", "o2 sensor")
    (project / ".env").write_text("OPENAI_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.chdir(project)
    encoder = MagicMock()
    encoder.encode.return_value = []
    encoder.get_sentence_embedding_dimension.return_value = 3
    provider = MagicMock()
    provider.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Oxygen Sensor"))]
    )
    with (
        patch("normflow.semantic_index._ensure_model", return_value=encoder),
        patch("normflow.llm_matcher.build_client", return_value=provider),
    ):
        result = CliRunner().invoke(
            app, ["batch-import", str(batch), "--column", "name"]
        )

    terminal = __import__("json").loads(result.stdout)
    assert result.exit_code == 0
    assert result.stderr.strip() == terminal["id"]
    assert terminal["status"] == "succeeded"
    status = CliRunner().invoke(app, ["batch-import-status", terminal["id"]])
    assert status.exit_code == 0
    assert __import__("json").loads(status.stdout) == terminal


def test_http_start_returns_durable_location_and_status(tmp_path: Path):
    project = init_project(tmp_path / "project")
    client = TestClient(create_app(resolve_project(project)))

    accepted = client.post(
        "/batch-import-runs?column=name&semantic=false&llm=false",
        files={"file": ("records.csv", b"name\no2 sensor\n", "text/csv")},
    )

    assert accepted.status_code == 202
    assert accepted.headers["location"] == f"/batch-import-runs/{accepted.json()['id']}"
    observed = client.get(accepted.headers["location"])
    assert observed.status_code == 200
    assert observed.json()["id"] == accepted.json()["id"]
    assert observed.json()["status"] in {"active", "succeeded"}


def test_http_competing_start_returns_active_run_location(tmp_path: Path):
    project = init_project(tmp_path / "project")
    batch = _csv(tmp_path / "records.csv", "first")
    started = Event()
    release = Event()

    def hold(_run):
        started.set()
        assert release.wait(5)

    with ThreadPoolExecutor() as executor:
        running = executor.submit(
            lambda: MappingService(project).run_batch_import(
                batch, "name", semantic=False, llm=False, on_started=hold,
            )
        )
        assert started.wait(5)
        response = TestClient(create_app(resolve_project(project))).post(
            "/batch-import-runs?column=name&semantic=false&llm=false",
            files={"file": ("second.csv", b"name\nsecond\n", "text/csv")},
        )
        assert response.status_code == 409
        assert response.headers["location"].endswith(response.json()["detail"]["active_run"]["id"])
        release.set()
        running.result(timeout=5)


def test_status_recovers_run_after_owning_process_crashes(tmp_path: Path):
    project = init_project(tmp_path / "project")
    batch = _csv(tmp_path / "records.csv", "o2 sensor")
    program = (
        "import os; from normflow.mapping_service import MappingService; "
        f"MappingService({str(project)!r}).run_batch_import("
        f"{str(batch)!r}, 'name', semantic=False, llm=False, "
        "on_started=lambda run: os._exit(9))"
    )

    crashed = subprocess.run([sys.executable, "-c", program], check=False)
    mappings = tmp_path / "mappings.csv"
    mappings.write_text("raw,clean\ncolour,color\n", encoding="utf-8")
    assert MappingService(project).import_mappings(mappings, "raw", "clean") == (1, 0)
    recovered = MappingService(project).batch_import_status()

    assert crashed.returncode == 9
    assert recovered["status"] == "interrupted"
    assert MappingService(project).list_review_items() == []
    retry = MappingService(project).retry_batch_import(
        recovered["id"], batch, "name", semantic=False, llm=False,
    )
    assert retry["id"] != recovered["id"]
    assert retry["status"] == "succeeded"
    assert MappingService(project).batch_import_status(recovered["id"])[
        "replacement_run_id"
    ] == retry["id"]
