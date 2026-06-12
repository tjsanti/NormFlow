"""Tests for the NormFlow CLI."""

import json
import tempfile
from pathlib import Path

from sqlmodel import Session
from typer.testing import CliRunner

from normflow.cli import app
from normflow.models import ExampleMapping
from normflow.workspace import WorkspaceService


runner = CliRunner()


def test_cli_help():
    """CLI help should exit cleanly with code 0."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "normflow" in result.stdout


def test_init_creates_workspace():
    """`normflow init` should create the expected structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "myproject"
        result = runner.invoke(app, ["init", "--workspace", str(ws_path)])

        assert result.exit_code == 0

        assert ws_path.is_dir()
        assert (ws_path / "normflow.db").is_file()
        assert (ws_path / "input").is_dir()
        assert (ws_path / "output").is_dir()
        assert (ws_path / "samples").is_dir()


def test_workspace_info():
    """`normflow workspace info` should report correct counts after init."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "myproject"

        # Initialize
        init_result = runner.invoke(app, ["init", "--workspace", str(ws_path)])
        assert init_result.exit_code == 0

        # Info
        info_result = runner.invoke(app, ["info", "--workspace", str(ws_path)])
        assert info_result.exit_code == 0

        assert "myproject" in info_result.stdout
        assert "Mappings:   0" in info_result.stdout
        assert "Suggestions: 0" in info_result.stdout


def test_workspace_info_errors_on_invalid_path():
    """`normflow info` should error when given a non-workspace path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(app, ["info", "--workspace", tmpdir])
        assert result.exit_code != 0


# ---- import tests ----


def _write_csv(path: Path, header: str, *rows: str) -> None:
    """Write a CSV file. Each row should be a full CSV line (e.g. 'hello,world')."""
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def test_import_creates_mappings():
    """`normflow import` should insert CSV rows as mappings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "mappings.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])
        _write_csv(csv_path, "source,target", " hello,world", "world,bar", "  foo  ,baz")

        result = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert result.exit_code == 0
        assert "Imported 3" in result.stdout


def test_import_skips_duplicates():
    """`normflow import` should skip rows where raw_text already exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "mappings.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])
        _write_csv(csv_path, "source,target", "hello,world", "foo,bar")

        # First import
        r1 = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert r1.exit_code == 0
        assert "Imported 2" in r1.stdout

        # Second import same file
        r2 = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert r2.exit_code == 0
        assert "0 new" in r2.stdout
        assert "2 skipped" in r2.stdout


def test_import_invalid_column():
    """`normflow import` should error when source column is missing from CSV."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "mappings.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])
        _write_csv(csv_path, "src,dst", "hello,world")

        result = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert result.exit_code != 0
        assert "source" in result.stdout.lower() or "column" in result.stdout.lower()


def test_import_skips_empty_rows():
    """`normflow import` should silently skip empty rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "mappings.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])
        csv_path.write_text("source,target\nhello,world\n\n\nfoo,bar\n")

        result = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert result.exit_code == 0
        assert "Imported 2" in result.stdout


# ---- export tests ----


def test_export_writes_csv():
    """`normflow export` should write mappings to a CSV file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "out.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        # Insert mappings directly via the service
        ws = WorkspaceService(str(ws_path))
        with ws.session() as session:
            session.add(ExampleMapping(raw_text="hello", normalized_text="world"))
            session.add(ExampleMapping(raw_text="foo", normalized_text="bar"))
            session.commit()

        result = runner.invoke(
            app,
            ["export", "--workspace", str(ws_path), str(csv_path)],
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
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "out.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ws = WorkspaceService(str(ws_path))
        with ws.session() as session:
            session.add(ExampleMapping(raw_text="hello", normalized_text="world"))
            session.commit()

        result = runner.invoke(
            app,
            ["export", "--workspace", str(ws_path), str(csv_path), "--source-column", "src", "--target-column", "tgt"],
        )
        assert result.exit_code == 0
        content = csv_path.read_text()
        assert "src" in content
        assert "tgt" in content
        assert "raw_text" not in content


def test_import_export_round_trip():
    """Import a CSV, export it, and the contents should match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        input_csv = ws_path / "input.csv"
        output_csv = ws_path / "output.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])
        _write_csv(input_csv, "source,target", "hello,world", "foo,bar")

        runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(input_csv), "--source-column", "source", "--target-column", "target"],
        )

        result = runner.invoke(
            app,
            ["export", "--workspace", str(ws_path), str(output_csv)],
        )
        assert result.exit_code == 0

        # Round-trip: import again from exported file should be 0 new
        result2 = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(output_csv), "--source-column", "raw_text", "--target-column", "normalized_text"],
        )
        assert result2.exit_code == 0
        assert "0 new" in result2.stdout


# ---- suggest tests ----


def test_suggest_exact_match_found():
    """`normflow suggest` should return a suggestion when exact match exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ws = WorkspaceService(str(ws_path))
        with ws.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        result = runner.invoke(
            app,
            ["suggest", "--workspace", str(ws_path), "colour"],
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
        ws_path = Path(tmpdir) / "proj"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ws = WorkspaceService(str(ws_path))
        with ws.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        result = runner.invoke(
            app,
            ["suggest", "--workspace", str(ws_path), "colr"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert data["raw_text"] == "colr"
        assert data["suggestions"] == []


def test_suggest_limit_respected():
    """`normflow suggest --limit 0` should return empty suggestions even when match exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ws = WorkspaceService(str(ws_path))
        with ws.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        result = runner.invoke(
            app,
            ["suggest", "--workspace", str(ws_path), "colour", "--limit", "0"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert data["suggestions"] == []


def test_suggest_limit_default():
    """`normflow suggest` with default limit should return the match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ws = WorkspaceService(str(ws_path))
        with ws.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        result = runner.invoke(
            app,
            ["suggest", "--workspace", str(ws_path), "colour", "--limit", "5"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert len(data["suggestions"]) == 1


def test_suggest_invalid_workspace():
    """`normflow suggest` should error on non-workspace path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(
            app,
            ["suggest", "--workspace", tmpdir, "colour"],
        )
        assert result.exit_code != 0
