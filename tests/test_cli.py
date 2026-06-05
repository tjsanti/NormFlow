"""Tests for the NormFlow CLI."""

import tempfile
from pathlib import Path

from typer.testing import CliRunner

from normflow.cli import app


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
        result = runner.invoke(app, ["init", str(ws_path)])

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
        init_result = runner.invoke(app, ["init", str(ws_path)])
        assert init_result.exit_code == 0

        # Info
        info_result = runner.invoke(app, ["info", str(ws_path)])
        assert info_result.exit_code == 0

        assert "myproject" in info_result.stdout
        assert "Mappings:   0" in info_result.stdout
        assert "Suggestions: 0" in info_result.stdout


def test_workspace_info_errors_on_invalid_path():
    """`normflow info` should error when given a non-workspace path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(app, ["info", tmpdir])
        assert result.exit_code != 0
