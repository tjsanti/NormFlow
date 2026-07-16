"""Behavioral tests for the CLI update-notification adapter."""

import json
from pathlib import Path

from typer.testing import CliRunner

from normflow.cli import CliUpdateBoundaries, app
from normflow.project_service import init_project
from normflow.update_check import INSTALL_COMMAND, UpdateNotice


class AvailableUpdateService:
    def check(self) -> UpdateNotice:
        return UpdateNotice(
            installed_version="0.1.0",
            latest_version="0.2.0",
            install_command=INSTALL_COMMAND,
        )


class RecordingCheck:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, installed_version, environment) -> UpdateNotice:
        self.calls += 1
        return AvailableUpdateService().check()


def test_interactive_human_command_prints_update_notice_only_on_stderr(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    boundaries = CliUpdateBoundaries(
        is_interactive=lambda: True,
        check_for_update=lambda installed_version, environment: (
            AvailableUpdateService().check()
        ),
    )

    result = CliRunner().invoke(app, ["init"], obj=boundaries)

    assert result.exit_code == 0
    assert "update" not in result.stdout.lower()
    assert result.stderr == (
        "NormFlow update available: 0.1.0 → 0.2.0\n"
        f"Install it explicitly with:\n{INSTALL_COMMAND}\n"
    )


def test_redirected_command_does_not_start_an_update_check(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    check = RecordingCheck()
    boundaries = CliUpdateBoundaries(
        is_interactive=lambda: False,
        check_for_update=check,
    )

    result = CliRunner().invoke(app, ["init"], obj=boundaries)

    assert result.exit_code == 0
    assert result.stderr == ""
    assert check.calls == 0


def test_interactive_review_table_is_a_human_notification_context(
    tmp_path: Path, monkeypatch,
) -> None:
    project = init_project(tmp_path / "project")
    monkeypatch.chdir(project)
    boundaries = CliUpdateBoundaries(
        is_interactive=lambda: True,
        check_for_update=lambda installed_version, environment: (
            AvailableUpdateService().check()
        ),
    )

    result = CliRunner().invoke(
        app, ["review", "list"], obj=boundaries
    )

    assert result.exit_code == 0
    assert "NormFlow update available: 0.1.0 → 0.2.0" in result.stderr


def test_interactive_machine_output_and_installer_verification_skip_check(
    tmp_path: Path, monkeypatch,
) -> None:
    project = init_project(tmp_path / "project")
    monkeypatch.chdir(project)
    check = RecordingCheck()
    boundaries = CliUpdateBoundaries(
        is_interactive=lambda: True,
        check_for_update=check,
    )
    runner = CliRunner()
    records = project / "records.csv"
    records.write_text("text\nvalue\n", encoding="utf-8")

    review = runner.invoke(
        app, ["review", "list", "--json"], obj=boundaries
    )
    csv_output = runner.invoke(
        app,
        [
            "suggest-batch",
            str(records),
            "--column",
            "text",
            "--no-semantic",
            "--no-llm",
        ],
        obj=boundaries,
    )
    version = runner.invoke(app, ["--version"], obj=boundaries)

    assert review.exit_code == 0
    assert json.loads(review.stdout) == []
    assert review.stderr == ""
    assert csv_output.stdout == "text,normalized_text\nvalue,\n"
    assert csv_output.stderr == ""
    assert version.stdout == "0.1.0\n"
    assert version.stderr == ""
    assert check.calls == 0
