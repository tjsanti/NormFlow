import csv
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch
import subprocess
import sys
import time
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


def test_http_collection_status_and_explicit_retry(tmp_path: Path):
    project = init_project(tmp_path / "project")
    client = TestClient(create_app(resolve_project(project)))
    assert client.get("/batch-import-runs").status_code == 404
    batch = _csv(tmp_path / "records.csv", "o2 sensor")
    failed = MappingService(project).run_batch_import(
        batch, "name", semantic=False, llm=False,
    )
    # Make a retryable terminal attempt without reaching into persistence.
    interrupted_program = (
        "import os; from normflow.mapping_service import MappingService; "
        f"MappingService({str(project)!r}).run_batch_import("
        f"{str(batch)!r}, 'name', semantic=False, llm=False, "
        "on_started=lambda run: os._exit(9))"
    )
    subprocess.run([sys.executable, "-c", interrupted_program], check=False)
    interrupted = MappingService(project).batch_import_status()

    latest = client.get("/batch-import-runs")
    retried = client.post(
        f"/batch-import-runs/{interrupted['id']}/retry?column=name&semantic=false&llm=false",
        files={"file": ("retry.csv", batch.read_bytes(), "text/csv")},
    )

    assert failed["status"] == "succeeded"
    assert latest.status_code == 200
    assert latest.json()["id"] == interrupted["id"]
    assert retried.status_code == 202
    assert retried.json()["id"] != interrupted["id"]
    assert retried.headers["location"] == f"/batch-import-runs/{retried.json()['id']}"
    assert MappingService(project).batch_import_status(interrupted["id"])[
        "replacement_run_id"
    ] == retried.json()["id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        retry_status = client.get(retried.headers["location"])
        if retry_status.json()["status"] != "active":
            break
        time.sleep(0.01)
    assert retry_status.json()["status"] == "succeeded"


def test_all_http_project_writers_report_shared_active_run_conflict(tmp_path: Path):
    project = init_project(tmp_path / "project")
    seed = _csv(tmp_path / "seed.csv", "pending")
    MappingService(project).run_batch_import(seed, "name", semantic=False, llm=False)
    item_id = MappingService(project).list_review_items()[0]["id"]
    retry_csv = _csv(tmp_path / "retry.csv", "retry")
    crash = (
        "import os; from normflow.mapping_service import MappingService; "
        f"MappingService({str(project)!r}).run_batch_import("
        f"{str(retry_csv)!r}, 'name', semantic=False, llm=False, "
        "on_started=lambda run: os._exit(9))"
    )
    subprocess.run([sys.executable, "-c", crash], check=False)
    retryable_id = MappingService(project).batch_import_status()["id"]
    held = _csv(tmp_path / "held.csv", "held")
    started, release = Event(), Event()

    def hold(_run):
        started.set()
        assert release.wait(5)

    with ThreadPoolExecutor() as executor:
        running = executor.submit(
            lambda: MappingService(project).run_batch_import(
                held, "name", semantic=False, llm=False, on_started=hold,
            )
        )
        assert started.wait(5)
        client = TestClient(create_app(resolve_project(project)))
        responses = [
            client.post(
                "/import/mappings?source_column=raw&target_column=clean",
                files={"file": ("m.csv", b"raw,clean\nx,X\n", "text/csv")},
            ),
            client.post(f"/review-items/{item_id}/accept", json={"normalized_text": "P"}),
            client.post("/review-items/bulk-accept", json={"review_item_ids": [item_id]}),
            client.post("/index/build"),
            client.post("/index/clear"),
            client.post(
                "/import/records?column=name&semantic=false&llm=false",
                files={"file": ("b.csv", b"name\nx\n", "text/csv")},
            ),
            client.post(
                f"/batch-import-runs/{retryable_id}/retry?column=name&semantic=false&llm=false",
                files={"file": ("retry.csv", retry_csv.read_bytes(), "text/csv")},
            ),
        ]
        active_id = MappingService(project).batch_import_status()["id"]
        for response in responses:
            assert response.status_code == 409
            assert response.headers["location"] == f"/batch-import-runs/{active_id}"
            assert response.json()["detail"]["active_run"]["id"] == active_id
        release.set()
        running.result(timeout=5)


def test_separate_process_owns_lock_and_competing_writer_fails_fast(tmp_path: Path):
    project = init_project(tmp_path / "project")
    batch = _csv(tmp_path / "records.csv", "held")
    ready, release = tmp_path / "ready", tmp_path / "release"
    program = f"""
import time
from pathlib import Path
from normflow.mapping_service import MappingService
ready, release = Path({str(ready)!r}), Path({str(release)!r})
def hold(run):
    ready.write_text(run['id'])
    while not release.exists():
        time.sleep(0.01)
MappingService({str(project)!r}).run_batch_import(
    {str(batch)!r}, 'name', semantic=False, llm=False, on_started=hold)
"""
    owner = subprocess.Popen([sys.executable, "-c", program])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        mappings = tmp_path / "mappings.csv"
        mappings.write_text("raw,clean\nx,X\n", encoding="utf-8")
        started = time.monotonic()
        with pytest.raises(ProjectBusyError):
            MappingService(project).import_mappings(mappings, "raw", "clean")
        assert time.monotonic() - started < 1
    finally:
        release.touch()
        owner.wait(timeout=10)


def test_http_observer_disconnect_does_not_stop_provider_backed_run(tmp_path: Path):
    project = init_project(tmp_path / "project")
    mappings = tmp_path / "mappings.csv"
    mappings.write_text("raw,clean\noxygen,Oxygen\n", encoding="utf-8")
    MappingService(project).import_mappings(mappings, "raw", "clean")
    app_instance = create_app(resolve_project(project))
    provider_started, release_provider = Event(), Event()
    encoder = MagicMock()
    encoder.encode.side_effect = lambda texts, **_kwargs: [
        [1.0, 0.0] if text == "oxygen" else [0.0, 1.0] for text in texts
    ]
    encoder.get_sentence_embedding_dimension.return_value = 2
    provider = MagicMock()

    def delayed(**_kwargs):
        provider_started.set()
        assert release_provider.wait(5)
        return MagicMock(choices=[MagicMock(message=MagicMock(content="Oxygen"))])

    provider.chat.completions.create.side_effect = delayed
    with (
        patch("normflow.semantic_index._ensure_model", return_value=encoder),
        patch("normflow.llm_matcher.build_client", return_value=provider),
    ):
        initiating = TestClient(app_instance)
        accepted = initiating.post(
            "/batch-import-runs?column=name&semantic=false",
            files={"file": ("records.csv", b"name\no2\n", "text/csv")},
        )
        assert accepted.status_code == 202
        assert provider_started.wait(5)
        initiating.close()
        release_provider.set()
        observer = TestClient(app_instance)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = observer.get(accepted.headers["location"])
            if status.json()["status"] != "active":
                break
            time.sleep(0.01)
        assert status.json()["status"] == "succeeded"


def test_committed_process_crash_is_reconciled_as_succeeded(tmp_path: Path):
    project = init_project(tmp_path / "project")
    batch = _csv(tmp_path / "records.csv", "committed")
    program = (
        "import os; from normflow.mapping_service import MappingService; "
        f"MappingService({str(project)!r}).run_batch_import("
        f"{str(batch)!r}, 'name', semantic=False, llm=False, "
        "on_committed=lambda run: os._exit(9))"
    )

    crashed = subprocess.run([sys.executable, "-c", program,], check=False)
    recovered = MappingService(project).batch_import_status()

    assert crashed.returncode == 9
    assert recovered["status"] == "succeeded"
    assert recovered["result"]["review_items"] == 1
    assert MappingService(project).list_review_items()[0]["raw_text"] == "committed"
    assert (project / ".batches" / "current.csv").read_bytes() == batch.read_bytes()
    assert not list((project / ".batches").glob(".previous-*.tmp"))


def test_provider_failure_is_durable_and_preserves_project_and_retained_batch(
    tmp_path: Path, monkeypatch,
):
    project = init_project(tmp_path / "project")
    service = MappingService(project)
    previous = _csv(tmp_path / "previous.csv", "preserved")
    service.run_batch_import(previous, "name", semantic=False, llm=False)
    mappings = tmp_path / "mappings.csv"
    mappings.write_text("raw,clean\noxygen,Oxygen\n", encoding="utf-8")
    service.import_mappings(mappings, "raw", "clean")
    failed_csv = _csv(tmp_path / "failed.csv", "provider failure")
    (project / ".env").write_text("OPENAI_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.chdir(project)
    encoder = MagicMock()
    encoder.encode.side_effect = lambda texts, **_kwargs: [
        [1.0, 0.0] if text == "oxygen" else [0.0, 1.0] for text in texts
    ]
    encoder.get_sentence_embedding_dimension.return_value = 2
    provider = MagicMock()
    provider.chat.completions.create.side_effect = RuntimeError("provider unavailable")

    with (
        patch("normflow.semantic_index._ensure_model", return_value=encoder),
        patch("normflow.llm_matcher.build_client", return_value=provider),
    ):
        failed = CliRunner().invoke(
            app, ["batch-import", str(failed_csv), "--column", "name"]
        )

    terminal = __import__("json").loads(failed.stdout)
    observed = CliRunner().invoke(app, ["batch-import-status", terminal["id"]])
    assert failed.exit_code == 1
    assert failed.stderr.strip() == terminal["id"]
    assert terminal["status"] == "failed"
    assert "provider unavailable" in terminal["error"]
    assert observed.exit_code == 0
    assert __import__("json").loads(observed.stdout) == terminal
    assert service.project_info()["mappings"] == 1
    assert service.list_review_items() == [
        {"id": 1, "raw_text": "preserved", "suggested_text": ""}
    ]
    assert (project / ".batches" / "current.csv").read_bytes() == previous.read_bytes()
    assert not (project / ".batches" / "runs" / f"{terminal['id']}.csv").exists()


def test_http_conflict_does_not_claim_historical_run_for_non_batch_writer(
    tmp_path: Path,
):
    project = init_project(tmp_path / "project")
    batch = _csv(tmp_path / "records.csv", "historical")
    historical = MappingService(project).run_batch_import(
        batch, "name", semantic=False, llm=False,
    )
    mappings = tmp_path / "mappings.csv"
    mappings.write_text("raw,clean\noxygen,Oxygen\n", encoding="utf-8")
    MappingService(project).import_mappings(mappings, "raw", "clean")
    build_started, release_build = Event(), Event()
    encoder = MagicMock()

    def blocked_encode(texts, **_kwargs):
        build_started.set()
        assert release_build.wait(5)
        return [[1.0, 0.0] for _ in texts]

    encoder.encode.side_effect = blocked_encode
    encoder.get_sentence_embedding_dimension.return_value = 2
    with (
        patch("normflow.semantic_index._ensure_model", return_value=encoder),
        ThreadPoolExecutor() as executor,
    ):
        building = executor.submit(MappingService(project).build_index)
        assert build_started.wait(5)
        response = TestClient(create_app(resolve_project(project))).post(
            "/import/mappings?source_column=raw&target_column=clean",
            files={"file": ("m.csv", b"raw,clean\nx,X\n", "text/csv")},
        )
        assert response.status_code == 409
        assert "location" not in response.headers
        assert response.json()["detail"] == (
            "The Project is currently being changed; try again later."
        )
        assert response.json().get("active_run") is None
        assert MappingService(project).batch_import_status()["id"] == historical["id"]
        release_build.set()
        building.result(timeout=5)
