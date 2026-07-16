"""Tests for the NormFlow CLI."""

import json
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import chdir
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from normflow.batch_import import BatchImportExecutionError, ProjectBusyError
from normflow.cli import app
from normflow.mapping_service import MappingService, ReviewItemInfo
from normflow.project_service import init_project as _init_project
from tests.helpers import seed_mappings


_active_project: Path | None = None


def init_project(path: str | Path) -> Path:
    """Initialize and remember the Project used by a CLI adapter test."""
    global _active_project
    _active_project = _init_project(path)
    return _active_project


class ProjectCliRunner(CliRunner):
    """Invoke Project-dependent commands from the initialized Project root."""

    def invoke(self, cli, args=None, **kwargs):
        project_commands = {
            "batch-import",
            "batch-import-retry",
            "batch-import-status",
            "import",
            "export",
            "export-batch",
            "suggest",
            "suggest-batch",
            "review",
            "index",
        }
        if (
            args
            and args[0] in project_commands
            and _active_project is not None
            and _active_project.is_dir()
        ):
            with chdir(_active_project):
                return super().invoke(cli, args, **kwargs)
        return super().invoke(cli, args, **kwargs)


runner = ProjectCliRunner()


def test_cli_help():
    """CLI help should exit cleanly with code 0."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "normflow" in result.stdout


def test_version_is_usable_outside_a_project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip()


def test_init_creates_discoverable_project_in_current_directory(
    tmp_path: Path, monkeypatch,
):
    """`normflow init` initializes the current directory as the Project."""
    project_root = tmp_path / "myproject"
    project_root.mkdir()
    unrelated_file = project_root / "notes.txt"
    unrelated_file.write_text("keep me", encoding="utf-8")
    unrelated_directory = project_root / "documents"
    unrelated_directory.mkdir()
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["init"])
    info_result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    assert f"Project initialized at: {project_root.resolve()}" in result.stdout
    assert (project_root / "normflow.db").is_file()
    assert (project_root / "input").is_dir()
    assert (project_root / "output").is_dir()
    assert (project_root / "samples").is_dir()
    assert unrelated_file.read_text(encoding="utf-8") == "keep me"
    assert unrelated_directory.is_dir()
    assert info_result.exit_code == 0
    assert f"Project:    {project_root.resolve()}" in info_result.stdout
    assert "Semantic index: missing" in info_result.stdout


def test_init_preserves_contents_and_repairs_existing_project(
    tmp_path: Path, monkeypatch,
):
    project_root = init_project(str(tmp_path / "project"))
    unrelated_file = project_root / "notes.txt"
    unrelated_file.write_text("keep me", encoding="utf-8")
    unrelated_directory = project_root / "documents"
    unrelated_directory.mkdir()
    (project_root / "input").rmdir()
    seed_mappings(project_root, [("colour", "color")])
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert unrelated_file.read_text(encoding="utf-8") == "keep me"
    assert unrelated_directory.is_dir()
    assert (project_root / "input").is_dir()
    assert MappingService(str(project_root)).project_info()["mappings"] == 1


def test_init_refuses_damaged_database_without_mutating_project(
    tmp_path: Path, monkeypatch,
):
    damaged_database = tmp_path / "normflow.db"
    original_contents = b"not a sqlite database"
    damaged_database.write_bytes(original_contents)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert str(damaged_database.resolve()) in result.stdout
    assert "recover" in result.stdout.lower()
    assert damaged_database.read_bytes() == original_contents
    assert not (tmp_path / "input").exists()
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "samples").exists()


def test_init_rejects_ancestor_project_before_mutation(
    tmp_path: Path, monkeypatch,
):
    ancestor = init_project(str(tmp_path / "ancestor"))
    candidate = ancestor / "documents" / "candidate"
    candidate.mkdir(parents=True)
    existing_file = candidate / "notes.txt"
    existing_file.write_text("unchanged", encoding="utf-8")
    monkeypatch.chdir(candidate)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert str(ancestor.resolve()) in result.stdout
    assert "nested" in result.stdout.lower()
    assert existing_file.read_text(encoding="utf-8") == "unchanged"
    assert sorted(path.name for path in candidate.iterdir()) == ["notes.txt"]


def test_init_rejects_descendant_project_before_mutation(
    tmp_path: Path, monkeypatch,
):
    candidate = tmp_path / "candidate"
    descendant = init_project(str(candidate / "documents" / "existing-project"))
    existing_file = candidate / "notes.txt"
    existing_file.write_text("unchanged", encoding="utf-8")
    monkeypatch.chdir(candidate)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert str(descendant.resolve()) in result.stdout
    assert "nested" in result.stdout.lower()
    assert existing_file.read_text(encoding="utf-8") == "unchanged"
    assert not (candidate / "normflow.db").exists()
    assert not (candidate / "input").exists()
    assert not (candidate / "output").exists()
    assert not (candidate / "samples").exists()


def test_init_rejects_project_selection_options(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    removed_flag_result = runner.invoke(app, ["init", "--workspace", str(tmp_path)])
    project_result = runner.invoke(app, ["init", "--project", str(tmp_path)])

    assert removed_flag_result.exit_code == 2
    assert project_result.exit_code == 2
    assert not (tmp_path / "normflow.db").exists()


def test_project_validation_rejects_unsupported_sqlite_schema_before_mutation(
    tmp_path: Path, monkeypatch,
):
    # Persistence fixture: no domain operation should create an invalid Project.
    database = tmp_path / "normflow.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    original_contents = database.read_bytes()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "unsupported" in result.stdout.lower()
    assert database.read_bytes() == original_contents
    assert not (tmp_path / "input").exists()


def test_init_refuses_unreadable_database_marker_before_mutation(
    tmp_path: Path, monkeypatch,
):
    database = tmp_path / "normflow.db"
    database.mkdir()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "unreadable" in result.stdout.lower()
    assert database.is_dir()
    assert not (tmp_path / "input").exists()


def test_project_info_discovers_project_from_current_directory(
    tmp_path: Path, monkeypatch,
):
    """`normflow info` reports the Project selected by the current directory."""
    project_root = init_project(str(tmp_path / "myproject"))
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    assert f"Project:    {project_root}" in result.stdout
    assert f"Database:   {project_root / 'normflow.db'}" in result.stdout
    assert "Mappings:   0" in result.stdout
    assert "Review Items: 0" in result.stdout


def test_project_info_discovers_project_from_nested_current_directory(
    tmp_path: Path, monkeypatch,
):
    project_root = init_project(str(tmp_path / "myproject"))
    nested = project_root / "input" / "incoming"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    assert f"Project:    {project_root}" in result.stdout


def test_project_info_errors_outside_a_project(tmp_path: Path, monkeypatch):
    starting_directory = tmp_path / "not-a-project"
    starting_directory.mkdir()
    monkeypatch.chdir(starting_directory)

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 1
    assert str(starting_directory.resolve()) in result.stdout
    assert "normflow init" in result.stdout


def test_project_info_rejects_explicit_project_selection(tmp_path: Path, monkeypatch):
    project_root = init_project(str(tmp_path / "myproject"))
    monkeypatch.chdir(project_root)

    removed_flag_result = runner.invoke(app, ["info", "--workspace", str(project_root)])
    project_result = runner.invoke(app, ["info", "--project", str(project_root)])

    assert removed_flag_result.exit_code == 2
    assert project_result.exit_code == 2


def test_project_info_reports_damaged_nearest_marker_without_parent_fallback(
    tmp_path: Path, monkeypatch,
):
    outer = init_project(str(tmp_path / "outer"))
    damaged_root = outer / "nested"
    damaged_root.mkdir()
    damaged_database = damaged_root / "normflow.db"
    damaged_database.write_text("damaged", encoding="utf-8")
    monkeypatch.chdir(damaged_root)

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 1
    assert str(damaged_database) in result.stdout
    assert str(outer / "normflow.db") not in result.stdout
    assert "recover" in result.stdout.lower()


def test_ui_rejects_invalid_llm_configuration_before_startup(
    tmp_path: Path, monkeypatch,
):
    project_root = init_project(str(tmp_path / "project"))
    monkeypatch.chdir(project_root)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("NORMFLOW_LLM_MODEL", raising=False)

    with (
        patch("socket.socket") as socket_factory,
        patch("httpx.Client.send") as send_http_request,
        patch("uvicorn.run") as run_server,
        patch("webbrowser.open") as open_browser,
    ):
        result = runner.invoke(app, ["ui"])

    assert result.exit_code == 1
    assert result.stdout.strip() == (
        "Error: OPENAI_API_KEY is required and must not be blank."
    )
    socket_factory.assert_not_called()
    send_http_request.assert_not_called()
    run_server.assert_not_called()
    open_browser.assert_not_called()


def test_ui_discovers_project_and_launches_bound_local_server_from_subdirectory(
    tmp_path: Path, monkeypatch,
):
    """`normflow ui` binds the browser server to the canonical active Project."""
    project_root = init_project(str(tmp_path / "project"))
    (project_root / ".env").write_text(
        "OPENAI_API_KEY=project-key\n", encoding="utf-8"
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("NORMFLOW_LLM_MODEL", raising=False)
    nested = project_root / "input" / "incoming"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    bound_app = object()

    with (
        patch("socket.socket") as socket_factory,
        patch("normflow.api.create_app", return_value=bound_app) as create_app,
        patch("uvicorn.run") as run_server,
        patch("webbrowser.open") as open_browser,
    ):
        local_socket = socket_factory.return_value.__enter__.return_value
        local_socket.getsockname.return_value = ("127.0.0.1", 43123)
        result = runner.invoke(app, ["ui"])

    assert result.exit_code == 0
    url = result.stdout.strip()
    assert url == "http://127.0.0.1:43123"
    local_socket.bind.assert_called_once_with(("127.0.0.1", 0))
    open_browser.assert_called_once_with(url)
    assert create_app.call_args.args[0].root == project_root
    assert run_server.call_args.args == (bound_app,)
    assert run_server.call_args.kwargs["host"] == "127.0.0.1"
    assert run_server.call_args.kwargs["port"] == 43123


def test_ui_no_open_starts_same_bound_server_without_opening_browser(
    tmp_path: Path, monkeypatch,
):
    """`normflow ui --no-open` leaves browser launching to the user."""
    project_root = init_project(str(tmp_path / "project"))
    monkeypatch.setenv("OPENAI_API_KEY", "shell-key")
    monkeypatch.chdir(project_root)
    bound_app = object()

    with (
        patch("socket.socket") as socket_factory,
        patch("normflow.api.create_app", return_value=bound_app),
        patch("uvicorn.run") as run_server,
        patch("webbrowser.open") as open_browser,
    ):
        local_socket = socket_factory.return_value.__enter__.return_value
        local_socket.getsockname.return_value = ("127.0.0.1", 43124)
        result = runner.invoke(app, ["ui", "--no-open"])

    assert result.exit_code == 0
    url = result.stdout.strip()
    assert url == "http://127.0.0.1:43124"
    local_socket.bind.assert_called_once_with(("127.0.0.1", 0))
    open_browser.assert_not_called()
    assert run_server.call_args.args == (bound_app,)
    assert run_server.call_args.kwargs["host"] == "127.0.0.1"
    assert run_server.call_args.kwargs["port"] == 43124


def test_ui_fixed_port_is_checked_and_used(tmp_path: Path, monkeypatch):
    project_root = init_project(str(tmp_path / "project"))
    monkeypatch.setenv("OPENAI_API_KEY", "shell-key")
    monkeypatch.chdir(project_root)

    with (
        patch("socket.socket") as socket_factory,
        patch("normflow.api.create_app", return_value=object()),
        patch("uvicorn.run") as run_server,
        patch("webbrowser.open"),
    ):
        result = runner.invoke(app, ["ui", "--port", "43125", "--no-open"])

    assert result.exit_code == 0
    local_socket = socket_factory.return_value.__enter__.return_value
    local_socket.bind.assert_called_once_with(("127.0.0.1", 43125))
    assert result.stdout.strip() == "http://127.0.0.1:43125"
    assert run_server.call_args.kwargs == {"host": "127.0.0.1", "port": 43125}


def test_ui_unavailable_fixed_port_fails_usefully(tmp_path: Path, monkeypatch):
    project_root = init_project(str(tmp_path / "project"))
    monkeypatch.setenv("OPENAI_API_KEY", "shell-key")
    monkeypatch.chdir(project_root)

    with (
        patch("socket.socket") as socket_factory,
        patch("uvicorn.run") as run_server,
        patch("webbrowser.open") as open_browser,
    ):
        local_socket = socket_factory.return_value.__enter__.return_value
        local_socket.bind.side_effect = OSError("Address already in use")
        result = runner.invoke(app, ["ui", "--port", "43126"])

    assert result.exit_code == 1
    assert "43126" in result.stdout
    assert "unavailable" in result.stdout.lower()
    run_server.assert_not_called()
    open_browser.assert_not_called()


def test_ui_rejects_invalid_ports_and_host_selection(tmp_path: Path, monkeypatch):
    project_root = init_project(str(tmp_path / "project"))
    monkeypatch.setenv("OPENAI_API_KEY", "shell-key")
    monkeypatch.chdir(project_root)

    assert runner.invoke(app, ["ui", "--port", "0"]).exit_code != 0
    assert runner.invoke(app, ["ui", "--port", "65536"]).exit_code != 0
    assert runner.invoke(app, ["ui", "--host", "0.0.0.0"]).exit_code != 0  # noqa: S104


def test_serve_command_is_not_public():
    result = runner.invoke(app, ["serve"])

    assert result.exit_code != 0
    assert "No such command 'serve'" in result.output


# ---- import tests ----


def _write_csv(path: Path, header: str, *rows: str) -> None:
    """Write a CSV file. Each row should be a full CSV line (e.g. 'hello,world')."""
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def test_batch_import_runs_complete_fallback_chain_and_returns_json(
    tmp_path: Path,
    monkeypatch,
):
    """`normflow batch-import` synchronously reports the canonical Batch result."""
    project_path = init_project(tmp_path / "project")
    csv_path = project_path / "batch.csv"
    _write_csv(
        csv_path,
        "raw",
        "colour",
        "colr",
        "new phrase",
        "colr",
    )
    seed_mappings(project_path, [("colour", "color")])
    (project_path / ".env").write_text(
        "OPENAI_API_KEY=project-secret\n", encoding="utf-8"
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("NORMFLOW_LLM_MODEL", raising=False)

    encoder = MagicMock()
    vectors = {
        "colour": [1.0, 0.0, 0.0],
        "colr": [1.0, 0.0, 0.0],
        "new phrase": [0.0, 1.0, 0.0],
    }
    encoder.encode.side_effect = lambda texts, **_kwargs: [
        vectors[text] for text in texts
    ]
    encoder.get_sentence_embedding_dimension.return_value = 3
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="New Phrase"))]
    )

    with (
        patch("normflow.semantic_index._ensure_model", return_value=encoder),
        patch("normflow.llm_matcher.build_client", return_value=client),
    ):
        result = runner.invoke(
            app,
            ["batch-import", str(csv_path), "--column", "raw"],
        )

    assert result.exit_code == 0
    terminal = json.loads(result.stdout)
    assert terminal["status"] == "succeeded"
    assert result.stderr.strip() == terminal["id"]
    assert terminal["result"] == {
        "auto_committed": 2,
        "review_items": 1,
        "skipped": 1,
        "semantic_index_status": "refresh_required",
        "semantic_index_warning": (
            "The semantic index will refresh before the next semantic Suggestion."
        ),
    }
    assert "project-secret" not in result.output
    assert MappingService(project_path).list_review_items() == [
        {"id": 1, "raw_text": "new phrase", "suggested_text": "New Phrase"}
    ]


def test_batch_import_waits_for_delayed_provider_completion(
    tmp_path: Path,
    monkeypatch,
):
    """The CLI remains in flight until the provider-backed Batch finishes."""
    project_path = init_project(tmp_path / "project")
    csv_path = project_path / "batch.csv"
    _write_csv(csv_path, "raw", "new phrase")
    seed_mappings(project_path, [("colour", "color")])
    (project_path / ".env").write_text(
        "OPENAI_API_KEY=project-secret\n", encoding="utf-8"
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("NORMFLOW_LLM_MODEL", raising=False)

    encoder = MagicMock()
    vectors = {
        "colour": [1.0, 0.0],
        "new phrase": [0.0, 1.0],
    }
    encoder.encode.side_effect = lambda texts, **_kwargs: [
        vectors[text] for text in texts
    ]
    encoder.get_sentence_embedding_dimension.return_value = 2

    provider_started = Event()
    allow_provider_completion = Event()

    def delayed_completion(**_kwargs):
        provider_started.set()
        if not allow_provider_completion.wait(timeout=5):
            raise RuntimeError("test did not release the provider")
        return MagicMock(
            choices=[MagicMock(message=MagicMock(content="New Phrase"))]
        )

    client = MagicMock()
    client.chat.completions.create.side_effect = delayed_completion

    with (
        patch("normflow.semantic_index._ensure_model", return_value=encoder),
        patch("normflow.llm_matcher.build_client", return_value=client),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        invocation = executor.submit(
            runner.invoke,
            app,
            ["batch-import", str(csv_path), "--column", "raw"],
        )
        assert provider_started.wait(timeout=5)
        try:
            assert not invocation.done()
        finally:
            allow_provider_completion.set()
        result = invocation.result(timeout=5)

    assert result.exit_code == 0
    assert json.loads(result.stdout)["result"]["review_items"] == 1
    assert MappingService(project_path).list_review_items() == [
        {"id": 1, "raw_text": "new phrase", "suggested_text": "New Phrase"}
    ]


def test_batch_import_provider_failure_is_actionable_and_atomic(
    tmp_path: Path,
    monkeypatch,
):
    """A failed CLI Batch Import reports exit 1 and preserves Project state."""
    project_path = init_project(tmp_path / "project")
    seed_mappings(project_path, [("colour", "color")])
    service = MappingService(project_path)

    previous_batch = project_path / "previous.csv"
    _write_csv(previous_batch, "raw", "preserved")
    service.import_records_for_review(
        str(previous_batch), "raw", semantic=False, llm=False
    )

    failed_batch = project_path / "failed.csv"
    _write_csv(failed_batch, "raw", "colr", "provider fail")
    (project_path / ".env").write_text(
        "OPENAI_API_KEY=project-secret\n", encoding="utf-8"
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("NORMFLOW_LLM_MODEL", raising=False)

    encoder = MagicMock()
    vectors = {
        "colour": [1.0, 0.0],
        "colr": [1.0, 0.0],
        "provider fail": [0.0, 1.0],
    }
    encoder.encode.side_effect = lambda texts, **_kwargs: [
        vectors[text] for text in texts
    ]
    encoder.get_sentence_embedding_dimension.return_value = 2
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("provider unavailable")

    with (
        patch("normflow.semantic_index._ensure_model", return_value=encoder),
        patch("normflow.llm_matcher.build_client", return_value=client),
    ):
        result = runner.invoke(
            app,
            ["batch-import", str(failed_batch), "--column", "raw"],
        )

    assert result.exit_code == 1
    assert "provider unavailable" in result.output
    assert "no changes were made" in result.output
    assert "project-secret" not in result.output
    assert service.project_info()["mappings"] == 1
    assert service.list_review_items() == [
        {"id": 1, "raw_text": "preserved", "suggested_text": ""}
    ]
    assert (project_path / ".batches" / "current.csv").read_bytes() == (
        previous_batch.read_bytes()
    )


def test_batch_import_retry_reports_the_active_run_as_json(tmp_path: Path):
    project_path = init_project(tmp_path / "project")
    csv_path = project_path / "batch.csv"
    _write_csv(csv_path, "raw", "new phrase")
    (project_path / ".env").write_text("OPENAI_API_KEY=test-key\n", encoding="utf-8")
    active_run = {"id": "active-run", "status": "active"}

    with patch.object(
        MappingService,
        "retry_batch_import",
        side_effect=ProjectBusyError(active_run),
    ):
        result = runner.invoke(
            app,
            ["batch-import-retry", "failed-run", str(csv_path), "--column", "raw"],
        )

    assert result.exit_code == 3
    assert json.loads(result.stdout) == active_run


def test_batch_import_status_resolves_the_initialized_project(tmp_path: Path, monkeypatch):
    project_path = init_project(tmp_path / "project")
    malformed = project_path / "malformed.csv"
    _write_csv(malformed, "other", "value")
    with pytest.raises(BatchImportExecutionError) as failed:
        MappingService(project_path).run_batch_import(malformed, "raw")
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    monkeypatch.chdir(ambient)

    result = runner.invoke(app, ["batch-import-status", failed.value.run["id"]])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["id"] == failed.value.run["id"]


def test_import_creates_mappings():
    """`normflow import` should insert CSV rows as mappings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "mappings.csv"

        init_project(str(project_path))
        _write_csv(csv_path, "source,target", " hello,world", "world,bar", "  foo  ,baz")

        result = runner.invoke(
            app,
            ["import", str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert result.exit_code == 0
        assert "Imported 3" in result.stdout


def test_import_skips_duplicates():
    """`normflow import` should skip rows where raw_text already exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "mappings.csv"

        init_project(str(project_path))
        _write_csv(csv_path, "source,target", "hello,world", "foo,bar")

        # First import
        r1 = runner.invoke(
            app,
            ["import", str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert r1.exit_code == 0
        assert "Imported 2" in r1.stdout

        # Second import same file
        r2 = runner.invoke(
            app,
            ["import", str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert r2.exit_code == 0
        assert "0 new" in r2.stdout
        assert "2 skipped" in r2.stdout


def test_import_skips_repeated_new_raw_text_in_one_csv():
    """`normflow import` should insert a repeated new raw_text only once."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "mappings.csv"

        init_project(str(project_path))
        _write_csv(csv_path, "source,target", "hello,world", "hello,other")

        result = runner.invoke(
            app,
            ["import", str(csv_path), "--source-column", "source", "--target-column", "target"],
        )

        assert result.exit_code == 0
        assert "Imported 1 new mapping" in result.stdout
        assert "1 skipped" in result.stdout


def test_import_invalid_column():
    """`normflow import` should error when source column is missing from CSV."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "mappings.csv"

        init_project(str(project_path))
        _write_csv(csv_path, "src,dst", "hello,world")

        result = runner.invoke(
            app,
            ["import", str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert result.exit_code != 0
        assert "source" in result.stdout.lower() or "column" in result.stdout.lower()


def test_import_skips_empty_rows():
    """`normflow import` should silently skip empty rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "mappings.csv"

        init_project(str(project_path))
        csv_path.write_text("source,target\nhello,world\n\n\nfoo,bar\n")

        result = runner.invoke(
            app,
            ["import", str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert result.exit_code == 0
        assert "Imported 2" in result.stdout


# ---- export tests ----


def test_export_writes_csv():
    """`normflow export` should write mappings to a CSV file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "out.csv"

        init_project(str(project_path))

        seed_mappings(project_path, [("hello", "world"), ("foo", "bar")])

        result = runner.invoke(
            app,
            ["export", str(csv_path)],
        )
        assert result.exit_code == 0
        assert "Exported 2" in result.stdout
        assert csv_path.exists()
        content = csv_path.read_text()
        assert "raw_text" in content
        assert "normalized_text" in content
        assert "hello" in content
        assert "world" in content


def test_export_custom_columns():
    """`normflow export` should use custom column names when flags are provided."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "out.csv"

        init_project(str(project_path))

        seed_mappings(project_path, [("hello", "world")])

        result = runner.invoke(
            app,
            ["export", str(csv_path), "--source-column", "src", "--target-column", "tgt"],
        )
        assert result.exit_code == 0
        content = csv_path.read_text()
        assert "src" in content
        assert "tgt" in content
        assert "raw_text" not in content


def test_import_export_round_trip():
    """Import a CSV, export it, and the contents should match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        input_csv = project_path / "input.csv"
        output_csv = project_path / "output.csv"

        init_project(str(project_path))
        _write_csv(input_csv, "source,target", "hello,world", "foo,bar")

        runner.invoke(
            app,
            ["import", str(input_csv), "--source-column", "source", "--target-column", "target"],
        )

        result = runner.invoke(
            app,
            ["export", str(output_csv)],
        )
        assert result.exit_code == 0

        # Round-trip: import again from exported file should be 0 new
        result2 = runner.invoke(
            app,
            ["import", str(output_csv), "--source-column", "raw_text", "--target-column", "normalized_text"],
        )
        assert result2.exit_code == 0
        assert "0 new" in result2.stdout


def test_export_batch_writes_retained_rows_with_normalized_values():
    """`normflow export-batch` exports the retained Batch rather than Mappings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        batch_csv = project_path / "batch.csv"
        output_csv = project_path / "normalized.csv"

        init_project(str(project_path))
        _write_csv(batch_csv, "id,name", "1,United States", "2,Canada")
        service = MappingService(str(project_path))
        service.import_records_for_review(
            str(batch_csv), "name", semantic=False, llm=False,
        )
        united_states = next(
            item for item in service.list_review_items()
            if item["raw_text"] == "United States"
        )
        service.accept_review_item(united_states["id"], "US")

        result = runner.invoke(
            app,
            ["export-batch", str(output_csv), "--source-column", "name"],
        )

        assert result.exit_code == 0
        assert result.stdout == f"Exported normalized Batch CSV to {output_csv}\n"
        assert output_csv.read_text(encoding="utf-8") == (
            "id,name,normalized_text\n"
            "1,United States,US\n"
            "2,Canada,\n"
        )


def test_export_batch_uses_selected_source_and_output_columns():
    """Batch export preserves retained columns and uses the selected output name."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        batch_csv = project_path / "batch.csv"
        output_csv = project_path / "normalized.csv"

        init_project(str(project_path))
        _write_csv(batch_csv, "id,label,note", "1,Canada,", "2,Mexico,south")
        service = MappingService(str(project_path))
        service.import_records_for_review(
            str(batch_csv), "label", semantic=False, llm=False,
        )
        canada = next(
            item for item in service.list_review_items()
            if item["raw_text"] == "Canada"
        )
        service.accept_review_item(canada["id"], "CA")

        result = runner.invoke(
            app,
            [
                "export-batch",
                str(output_csv),
                "--source-column",
                "label",
                "--output-column",
                "clean_label",
            ],
        )

        assert result.exit_code == 0
        assert output_csv.read_text(encoding="utf-8") == (
            "id,label,note,clean_label\n"
            "1,Canada,,CA\n"
            "2,Mexico,south,\n"
        )


def test_export_batch_without_retained_batch_fails_with_next_step():
    """Batch export reports how to create the missing retained Batch."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        output_csv = project_path / "normalized.csv"
        init_project(str(project_path))

        result = runner.invoke(app, ["export-batch", str(output_csv)])

        assert result.exit_code == 1
        assert result.stdout == "Error: No batch CSV found. Import records first.\n"
        assert not output_csv.exists()


def test_export_batch_rejects_missing_source_column():
    """Batch export fails instead of silently producing an all-blank output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        batch_csv = project_path / "batch.csv"
        output_csv = project_path / "normalized.csv"
        init_project(str(project_path))
        _write_csv(batch_csv, "name", "Canada")
        MappingService(project_path).import_records_for_review(
            batch_csv, "name", semantic=False, llm=False,
        )

        result = runner.invoke(
            app,
            ["export-batch", str(output_csv), "--source-column", "missing"],
        )

        assert result.exit_code == 1
        assert "does not contain a column named 'missing'" in result.stdout
        assert not output_csv.exists()


def test_export_batch_rejects_an_existing_output_column():
    """Batch export preserves every retained column instead of overwriting one."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        batch_csv = project_path / "batch.csv"
        output_csv = project_path / "normalized.csv"
        init_project(str(project_path))
        _write_csv(batch_csv, "name,clean", "Canada,original")
        MappingService(project_path).import_records_for_review(
            batch_csv, "name", semantic=False, llm=False,
        )

        result = runner.invoke(
            app,
            ["export-batch", str(output_csv), "--source-column", "name", "--output-column", "clean"],
        )

        assert result.exit_code == 1
        assert "already contains a column named 'clean'" in result.stdout
        assert not output_csv.exists()


# ---- suggest tests ----


def test_suggest_exact_match_found():
    """`normflow suggest` should return a suggestion when exact match exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"

        init_project(str(project_path))

        seed_mappings(project_path, [("colour", "color")])

        result = runner.invoke(
            app,
            ["suggest", "colour"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert data["raw_text"] == "colour"
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["suggested_text"] == "color"
        assert data["suggestions"][0]["method"] == "exact"
        assert data["suggestions"][0]["confidence"] == 1.0


def test_suggest_no_match_found():
    """`normflow suggest` should return empty suggestions when no match exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"

        init_project(str(project_path))

        seed_mappings(project_path, [("colour", "color")])

        result = runner.invoke(
            app,
            ["suggest", "colr", "--no-semantic", "--no-llm"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert data["raw_text"] == "colr"
        assert data["suggestions"] == []


def test_suggest_limit_respected():
    """`normflow suggest --limit 0` should return empty suggestions even when match exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"

        init_project(str(project_path))

        seed_mappings(project_path, [("colour", "color")])

        result = runner.invoke(
            app,
            ["suggest", "colour", "--limit", "0"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert data["suggestions"] == []


def test_suggest_limit_default():
    """`normflow suggest` with default limit should return the match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"

        init_project(str(project_path))

        seed_mappings(project_path, [("colour", "color")])

        result = runner.invoke(
            app,
            ["suggest", "colour", "--limit", "5"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert len(data["suggestions"]) == 1


def test_suggest_outside_project(tmp_path: Path, monkeypatch):
    """`normflow suggest` should error outside a Project."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        ["suggest", "colour"],
    )

    assert result.exit_code != 0


# ---- suggest batch tests ----


def test_suggest_batch_basic():
    """`normflow suggest batch` should output CSV with normalized_text column."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "input.csv"

        init_project(str(project_path))

        seed_mappings(project_path, [("colour", "color"), ("centre", "center")])

        # Input CSV with a raw text column
        _write_csv(csv_path, "id,item", "1,colour", "2,centre", "3,unknown")

        result = runner.invoke(
            app,
            ["suggest-batch", str(csv_path), "--column", "item"],
        )
        assert result.exit_code == 0

        lines = result.stdout.strip().split("\n")
        header = lines[0]
        assert "id" in header
        assert "item" in header
        assert "normalized_text" in header

        # colour -> color, centre -> center, unknown -> blank
        assert "color" in result.stdout
        assert "center" in result.stdout


def test_suggest_batch_no_match_blank():
    """Rows with no match should have blank normalized_text."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "input.csv"

        init_project(str(project_path))

        seed_mappings(project_path, [("colour", "color")])

        _write_csv(csv_path, "text", "colour", "nope")

        result = runner.invoke(
            app,
            ["suggest-batch", str(csv_path), "--column", "text"],
        )
        assert result.exit_code == 0

        lines = result.stdout.strip().split("\n")
        # header + 2 data rows
        assert len(lines) == 3
        # second data row (nope) has blank normalized_text
        last_line = lines[2]
        assert "nope" in last_line


def test_suggest_batch_custom_output_column():
    """--output-column should rename the suggestion column."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "input.csv"

        init_project(str(project_path))

        seed_mappings(project_path, [("colour", "color")])

        _write_csv(csv_path, "text", "colour")

        result = runner.invoke(
            app,
            ["suggest-batch", str(csv_path), "--column", "text", "--output-column", "mapping"],
        )
        assert result.exit_code == 0
        assert "mapping" in result.stdout
        assert "normalized_text" not in result.stdout


def test_suggest_batch_output_to_file():
    """--output should write CSV to the specified file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "input.csv"
        out_path = project_path / "output.csv"

        init_project(str(project_path))

        seed_mappings(project_path, [("colour", "color")])

        _write_csv(csv_path, "text", "colour")

        result = runner.invoke(
            app,
            ["suggest-batch", str(csv_path), "--column", "text", "--output", str(out_path)],
        )
        assert result.exit_code == 0
        assert out_path.exists()
        assert "color" in out_path.read_text()


def test_suggest_batch_excludes_entirely_blank_rows():
    """Rows where every column is blank should be excluded from output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "input.csv"

        init_project(str(project_path))

        seed_mappings(project_path, [("colour", "color")])

        # Row 1: valid, Row 2: all blank, Row 3: valid
        csv_path.write_text("id,text\n1,colour\n,,\n3,centre\n")

        result = runner.invoke(
            app,
            ["suggest-batch", str(csv_path), "--column", "text"],
        )
        assert result.exit_code == 0

        lines = result.stdout.strip().split("\n")
        # header + 2 data rows (blank row excluded)
        assert len(lines) == 3


def test_suggest_batch_includes_partial_rows_skips_processing():
    """Rows with some data but blank raw text column should appear in output with blank suggestion."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "input.csv"

        init_project(str(project_path))

        seed_mappings(project_path, [("colour", "color")])

        # Row 1: valid, Row 2: has id but blank text, Row 3: valid
        csv_path.write_text("id,text\n1,colour\n2,\n3,centre\n")

        result = runner.invoke(
            app,
            ["suggest-batch", str(csv_path), "--column", "text"],
        )
        assert result.exit_code == 0

        lines = result.stdout.strip().split("\n")
        # header + 3 data rows (partial row included)
        assert len(lines) == 4
        # middle row has id=2
        assert "2" in lines[2]


def test_suggest_batch_preserves_extra_columns():
    """All original columns should be preserved in the output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "input.csv"

        init_project(str(project_path))

        seed_mappings(project_path, [("colour", "color")])

        _write_csv(csv_path, "id,category,text,notes", "1,UK,colour,primary")

        result = runner.invoke(
            app,
            ["suggest-batch", str(csv_path), "--column", "text"],
        )
        assert result.exit_code == 0

        header = result.stdout.strip().split("\n")[0]
        assert "id" in header
        assert "category" in header
        assert "text" in header
        assert "notes" in header
        assert "normalized_text" in header


def test_suggest_batch_outside_project(tmp_path: Path, monkeypatch):
    """`normflow suggest batch` should error outside a Project."""
    csv_path = tmp_path / "input.csv"
    _write_csv(csv_path, "text", "hello")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        ["suggest-batch", str(csv_path), "--column", "text"],
    )

    assert result.exit_code != 0


def test_suggest_batch_missing_column():
    """`normflow suggest batch` should error when column is not in CSV."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = project_path / "input.csv"

        init_project(str(project_path))
        _write_csv(csv_path, "id,text", "1,hello")

        result = runner.invoke(
            app,
            ["suggest-batch", str(csv_path), "--column", "missing"],
        )
        assert result.exit_code != 0


def test_suggest_batch_missing_input_file():
    """`normflow suggest batch` should error when input file does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"

        init_project(str(project_path))

        result = runner.invoke(
            app,
            ["suggest-batch", "nonexistent.csv", "--column", "text"],
        )
        assert result.exit_code != 0


# ---- review tests ----


def _fake_mapping_service(
    review_items: list[ReviewItemInfo] | None = None,
) -> MagicMock:
    """Return a Mapping interface fake for CLI adapter contracts."""
    service = MagicMock(spec=MappingService)
    service.list_review_items.return_value = review_items or []
    return service


def test_review_list_shows_review_items():
    """`normflow review list` shows pending Review Items."""
    service = _fake_mapping_service([
        {"id": 1, "raw_text": "o2 sensor", "suggested_text": "O2 Sensor"},
        {"id": 2, "raw_text": "oxygen sensor", "suggested_text": "Oxygen Sensor"},
    ])

    with patch("normflow.cli._project_service", return_value=service):
        result = runner.invoke(
            app,
            ["review", "list"],
        )

    assert result.exit_code == 0
    assert "o2 sensor" in result.stdout
    assert "oxygen sensor" in result.stdout
    service.list_review_items.assert_called_once_with()


def test_review_list_empty_when_no_pending():
    """`normflow review list` is empty when no Review Items are pending."""
    service = _fake_mapping_service()

    with patch("normflow.cli._project_service", return_value=service):
        result = runner.invoke(
            app,
            ["review", "list"],
        )

    assert result.exit_code == 0
    assert result.stdout == ""


def test_review_list_json_output():
    """`normflow review list --json` should return valid JSON array."""
    service = _fake_mapping_service([
        {"id": 1, "raw_text": "o2 sensor", "suggested_text": "O2 Sensor"},
    ])

    with patch("normflow.cli._project_service", return_value=service):
        result = runner.invoke(
            app,
            ["review", "list", "--json"],
        )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [
        {"id": 1, "raw_text": "o2 sensor", "suggested_text": "O2 Sensor"}
    ]


def test_review_accept_forwards_review_item_id():
    """`normflow review accept` forwards the selected Review Item."""
    service = _fake_mapping_service()

    with patch("normflow.cli._project_service", return_value=service):
        result = runner.invoke(
            app,
            ["review", "accept", "--review-item-id", "1"],
        )

    assert result.exit_code == 0
    assert "Review Item 1 accepted." in result.stdout
    service.accept_review_item.assert_called_once_with(1, None)


def test_review_accept_forwards_replacement_text():
    """`normflow review accept` forwards optional replacement normalized text."""
    service = _fake_mapping_service()

    with patch("normflow.cli._project_service", return_value=service):
        result = runner.invoke(
            app,
            [
                "review",
                "accept",
                "--review-item-id",
                "1",
                "--normalized-text",
                "Oxygen Sensor",
            ],
        )

    assert result.exit_code == 0
    service.accept_review_item.assert_called_once_with(1, "Oxygen Sensor")


def test_legacy_review_accept_command_and_option_are_unavailable():
    edit_result = runner.invoke(app, ["review", "edit-and-accept", "--help"])
    record_id_result = runner.invoke(
        app,
        ["review", "accept", "--record-id", "1"],
    )

    assert edit_result.exit_code == 2
    assert record_id_result.exit_code == 2


def test_review_accept_removed_item_fails():
    """A Review Item cannot be accepted twice because acceptance removes it."""
    service = _fake_mapping_service()
    service.accept_review_item.side_effect = [
        None,
        ValueError("Review Item with id 1 not found"),
    ]

    with patch("normflow.cli._project_service", return_value=service):
        first = runner.invoke(
            app,
            ["review", "accept", "--review-item-id", "1"],
        )
        assert first.exit_code == 0

        result = runner.invoke(
            app,
            ["review", "accept", "--review-item-id", "1"],
        )
        assert result.exit_code != 0


def test_review_accept_invalid_review_item_id_fails():
    """`normflow review accept` fails for an unknown Review Item."""
    service = _fake_mapping_service()
    service.accept_review_item.side_effect = ValueError(
        "Review Item with id 999 not found"
    )

    with patch("normflow.cli._project_service", return_value=service):
        result = runner.invoke(
            app,
            [
                "review",
                "accept",
                "--review-item-id",
                "999",
                "--normalized-text",
                "Something",
            ],
        )

    assert result.exit_code != 0
    assert "Review Item with id 999 not found" in result.stdout
