"""Current-directory Project discovery at the data-command CLI seam."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from normflow.cli import app
from normflow.project_service import init_project
from tests.helpers import import_blank_review_items, seed_mappings


runner = CliRunner()


def test_import_uses_project_discovered_from_current_directory(
    tmp_path: Path, monkeypatch,
):
    project = init_project(tmp_path / "project")
    source = project / "mappings.csv"
    source.write_text("source,target\ncolour,color\n", encoding="utf-8")
    monkeypatch.chdir(project)

    result = runner.invoke(
        app,
        [
            "import",
            str(source),
            "--source-column",
            "source",
            "--target-column",
            "target",
        ],
    )

    assert result.exit_code == 0
    assert "Imported 1 new mappings" in result.stdout


def test_export_discovers_parent_project_and_keeps_output_relative_to_shell_directory(
    tmp_path: Path, monkeypatch,
):
    project = init_project(tmp_path / "project")
    nested = project / "reports" / "daily"
    nested.mkdir(parents=True)
    seed_mappings(project, [("colour", "color")])
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["export", "mappings.csv"])

    assert result.exit_code == 0
    assert (nested / "mappings.csv").read_text(encoding="utf-8") == (
        "raw_text,normalized_text\ncolour,color\n"
    )
    assert not (project / "mappings.csv").exists()


def test_suggest_uses_nearest_project_from_nested_directory(tmp_path: Path, monkeypatch):
    outer = init_project(tmp_path / "outer")
    inner = init_project(tmp_path / "inner")
    nested = inner / "input" / "incoming"
    nested.mkdir(parents=True)
    seed_mappings(outer, [("colour", "outer")])
    seed_mappings(inner, [("colour", "color")])
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["suggest", "colour"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["suggestions"][0]["suggested_text"] == "color"


def test_suggest_batch_keeps_input_and_output_relative_to_shell_directory(
    tmp_path: Path, monkeypatch,
):
    project = init_project(tmp_path / "project")
    nested = project / "input" / "incoming"
    nested.mkdir(parents=True)
    (nested / "records.csv").write_text("text\ncolour\n", encoding="utf-8")
    seed_mappings(project, [("colour", "color")])
    monkeypatch.chdir(nested)

    result = runner.invoke(
        app,
        [
            "suggest-batch",
            "records.csv",
            "--column",
            "text",
            "--output",
            "suggestions.csv",
        ],
    )

    assert result.exit_code == 0
    assert (nested / "suggestions.csv").read_text(encoding="utf-8") == (
        "text,normalized_text\ncolour,color\n"
    )
    assert not (project / "suggestions.csv").exists()


def test_review_list_discovers_project_from_nested_directory(tmp_path: Path, monkeypatch):
    project = init_project(tmp_path / "project")
    nested = project / "output" / "reports"
    nested.mkdir(parents=True)
    import_blank_review_items(project, ["colr"])
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["review", "list", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)[0]["raw_text"] == "colr"


def test_review_accept_uses_active_project(tmp_path: Path, monkeypatch):
    project = init_project(tmp_path / "project")
    import_blank_review_items(project, ["colr"])
    monkeypatch.chdir(project)

    result = runner.invoke(app, [
        "review",
        "accept",
        "--review-item-id",
        "1",
        "--normalized-text",
        "color",
    ])

    assert result.exit_code == 0
    assert json.loads(runner.invoke(app, ["review", "list", "--json"]).stdout) == []


def test_review_accept_with_replacement_uses_active_project(tmp_path: Path, monkeypatch):
    project = init_project(tmp_path / "project")
    import_blank_review_items(project, ["colr"])
    monkeypatch.chdir(project)

    result = runner.invoke(
        app,
        [
            "review",
            "accept",
            "--review-item-id",
            "1",
            "--normalized-text",
            "Color",
        ],
    )

    assert result.exit_code == 0
    suggestion = json.loads(runner.invoke(app, ["suggest", "colr"]).stdout)
    assert suggestion["suggestions"][0]["suggested_text"] == "Color"


@patch("normflow.semantic_index._ensure_model")
def test_index_build_uses_active_project(mock_ensure, tmp_path: Path, monkeypatch):
    model = MagicMock()
    model.encode.return_value = [[1.0, 0.0, 0.0]]
    model.get_sentence_embedding_dimension.return_value = 3
    mock_ensure.return_value = model
    project = init_project(tmp_path / "project")
    seed_mappings(project, [("colour", "color")])
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["index", "build"])

    assert result.exit_code == 0
    assert "Index built with 1 entries" in result.stdout


def test_index_clear_uses_active_project(tmp_path: Path, monkeypatch):
    project = init_project(tmp_path / "project")
    index_dir = project / ".normflow" / "faiss_index"
    index_dir.mkdir(parents=True)
    (index_dir / "stale").write_text("data", encoding="utf-8")
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["index", "clear"])

    assert result.exit_code == 0
    assert not index_dir.exists()


@pytest.mark.parametrize(
    "command",
    [
        ["import", "input.csv", "--source-column", "raw", "--target-column", "clean"],
        ["export", "output.csv"],
        ["suggest", "colour"],
        ["suggest-batch", "input.csv", "--column", "raw"],
        ["review", "list"],
        ["index", "clear"],
    ],
)
def test_data_commands_report_stable_error_outside_project(
    command: list[str], tmp_path: Path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, command)

    assert result.exit_code == 1
    assert result.stdout == (
        f"Error: No NormFlow Project found from {tmp_path.resolve()}. "
        "Run `normflow init` from the directory you want to use as a Project.\n"
    )


@pytest.mark.parametrize(
    "command",
    [
        ["export", "output.csv"],
        ["review", "list"],
        ["index", "clear"],
    ],
)
def test_representative_data_groups_stop_at_damaged_nearest_project(
    command: list[str], tmp_path: Path, monkeypatch,
):
    outer = init_project(tmp_path / "outer")
    damaged = outer / "nested"
    damaged.mkdir()
    database = damaged / "normflow.db"
    database.write_text("damaged", encoding="utf-8")
    monkeypatch.chdir(damaged)

    result = runner.invoke(app, command)

    assert result.exit_code == 1
    assert str(database) in result.stdout
    assert str(outer / "normflow.db") not in result.stdout
    assert "recover" in result.stdout.lower()


@pytest.mark.parametrize(
    "command",
    [
        ["import", "input.csv", "--source-column", "raw", "--target-column", "clean"],
        ["export", "output.csv"],
        ["suggest", "colour"],
        ["suggest-batch", "input.csv", "--column", "raw"],
        ["review", "list"],
        ["index", "clear"],
    ],
)
def test_data_commands_reject_removed_workspace_option(
    command: list[str], tmp_path: Path, monkeypatch,
):
    project = init_project(tmp_path / "project")
    monkeypatch.chdir(project)

    result = runner.invoke(app, [*command, "--workspace", str(project)])

    assert result.exit_code == 2
