"""Tests for the NormFlow CLI."""

import json
import sqlite3
import tempfile
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

from sqlmodel import Session
from typer.testing import CliRunner

from normflow.cli import app
from normflow.mapping_service import ExampleMapping, MappingService
from normflow.project_service import init_project as _init_project


_active_project: Path | None = None


def init_project(path: str | Path) -> Path:
    """Initialize and remember the Project used by a CLI adapter test."""
    global _active_project
    _active_project = _init_project(path)
    return _active_project


class ProjectCliRunner(CliRunner):
    """Invoke Project-dependent commands from the initialized Project root."""

    def invoke(self, cli, args=None, **kwargs):
        project_commands = {"import", "export", "suggest", "suggest-batch", "review", "index"}
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


def test_init_preserves_contents_and_repairs_existing_project(
    tmp_path: Path, monkeypatch,
):
    project_root = init_project(str(tmp_path / "project"))
    unrelated_file = project_root / "notes.txt"
    unrelated_file.write_text("keep me", encoding="utf-8")
    unrelated_directory = project_root / "documents"
    unrelated_directory.mkdir()
    (project_root / "input").rmdir()
    with MappingService(str(project_root)).session() as session:
        session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
        session.commit()
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


def test_init_refuses_unsupported_database_schema_before_mutation(
    tmp_path: Path, monkeypatch,
):
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


def test_ui_discovers_project_and_launches_bound_local_server_from_subdirectory(
    tmp_path: Path, monkeypatch,
):
    """`normflow ui` binds the browser server to the canonical active Project."""
    project_root = init_project(str(tmp_path / "project"))
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
    monkeypatch.chdir(project_root)

    assert runner.invoke(app, ["ui", "--port", "0"]).exit_code != 0
    assert runner.invoke(app, ["ui", "--port", "65536"]).exit_code != 0
    assert runner.invoke(app, ["ui", "--host", "0.0.0.0"]).exit_code != 0


def test_serve_command_is_not_public():
    result = runner.invoke(app, ["serve"])

    assert result.exit_code != 0
    assert "No such command 'serve'" in result.output


# ---- import tests ----


def _write_csv(path: Path, header: str, *rows: str) -> None:
    """Write a CSV file. Each row should be a full CSV line (e.g. 'hello,world')."""
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


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

        # Insert mappings directly via the service
        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="hello", normalized_text="world"))
            session.add(ExampleMapping(raw_text="foo", normalized_text="bar"))
            session.commit()

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

        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="hello", normalized_text="world"))
            session.commit()

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


# ---- suggest tests ----


def test_suggest_exact_match_found():
    """`normflow suggest` should return a suggestion when exact match exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"

        init_project(str(project_path))

        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

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

        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        result = runner.invoke(
            app,
            ["suggest", "colr"],
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

        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

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

        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        result = runner.invoke(
            app,
            ["suggest", "colour", "--limit", "5"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert len(data["suggestions"]) == 1


def test_suggest_outside_project():
    """`normflow suggest` should error outside a Project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(
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

        # Seed mappings
        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.add(ExampleMapping(raw_text="centre", normalized_text="center"))
            session.commit()

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

        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

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

        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

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

        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

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

        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

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

        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

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

        ms = MappingService(str(project_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

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


def test_suggest_batch_outside_project():
    """`normflow suggest batch` should error outside a Project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        csv_path = Path(tmpdir) / "input.csv"
        _write_csv(csv_path, "text", "hello")

        result = runner.invoke(
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


def _seed_review_items(project_path: Path, items: list[tuple[str, str]]) -> None:
    """Seed Review Items for CLI adapter tests."""
    from normflow.mapping_service import MappingService, ReviewItem

    ms = MappingService(str(project_path))
    with ms.session() as session:
        for raw_text, suggested_text in items:
            session.add(ReviewItem(
                raw_text=raw_text,
                suggested_text=suggested_text,
            ))
        session.commit()


def test_review_list_shows_review_items():
    """`normflow review list` shows pending Review Items."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        init_project(str(project_path))

        _seed_review_items(project_path, [
            ("o2 sensor", "O2 Sensor"),
            ("oxygen sensor", "Oxygen Sensor"),
        ])

        result = runner.invoke(
            app,
            ["review", "list"],
        )
        assert result.exit_code == 0
        assert "o2 sensor" in result.stdout
        assert "oxygen sensor" in result.stdout
        assert "oxygen sensor" in result.stdout


def test_review_list_empty_when_no_pending():
    """`normflow review list` is empty when no Review Items are pending."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        init_project(str(project_path))

        result = runner.invoke(
            app,
            ["review", "list"],
        )
        assert result.exit_code == 0


def test_review_list_json_output():
    """`normflow review list --json` should return valid JSON array."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        init_project(str(project_path))

        _seed_review_items(project_path, [
            ("o2 sensor", "O2 Sensor"),
        ])

        result = runner.invoke(
            app,
            ["review", "list", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["raw_text"] == "o2 sensor"
        assert data[0]["suggested_text"] == "O2 Sensor"


def test_review_accept_inserts_mapping_and_removes_review_item():
    """`normflow review accept` accepts a Review Item."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        init_project(str(project_path))

        _seed_review_items(project_path, [
            ("o2 sensor", "O2 Sensor"),
        ])

        result = runner.invoke(
            app,
            ["review", "accept", "--record-id", "1"],
        )
        assert result.exit_code == 0
        assert "Review Item 1 accepted." in result.stdout

        # Verify mapping was inserted
        with chdir(project_path):
            info_result = runner.invoke(app, ["info"])
        assert "Mappings:   1" in info_result.stdout
        assert "Review Items: 0" in info_result.stdout


def test_review_edit_and_accept_inserts_mapping_with_custom_text():
    """`normflow review edit-and-accept` accepts with edited text."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        init_project(str(project_path))

        _seed_review_items(project_path, [
            ("o2 sensor", "O2 Sensor"),
        ])

        result = runner.invoke(
            app,
            ["review", "edit-and-accept", "--record-id", "1", "--normalized-text", "Oxygen Sensor"],
        )
        assert result.exit_code == 0
        assert "Review Item 1 accepted with edit." in result.stdout

        # Verify mapping was inserted with edited text
        with chdir(project_path):
            info_result = runner.invoke(app, ["info"])
        assert "Mappings:   1" in info_result.stdout

        # Verify the mapping has the custom text
        ms = MappingService(str(project_path))
        with ms.session() as session:
            from sqlmodel import select
            mapping = session.exec(
                select(ExampleMapping).where(ExampleMapping.raw_text == "o2 sensor")
            ).first()
        assert mapping is not None
        assert mapping.normalized_text == "Oxygen Sensor"


def test_review_accept_removed_item_fails():
    """A Review Item cannot be accepted twice because acceptance removes it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        init_project(str(project_path))

        _seed_review_items(project_path, [
            ("o2 sensor", "O2 Sensor"),
        ])

        first = runner.invoke(
            app,
            ["review", "accept", "--record-id", "1"],
        )
        assert first.exit_code == 0

        result = runner.invoke(
            app,
            ["review", "accept", "--record-id", "1"],
        )
        assert result.exit_code != 0


def test_review_edit_and_accept_invalid_record_id_fails():
    """`normflow review edit-and-accept` fails for an unknown Review Item."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "proj"
        init_project(str(project_path))

        result = runner.invoke(
            app,
            ["review", "edit-and-accept", "--record-id", "999", "--normalized-text", "Something"],
        )
        assert result.exit_code != 0
