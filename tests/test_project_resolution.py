"""Public Project discovery behavior."""

import shutil
from pathlib import Path

import pytest

from normflow.project import (
    InvalidProjectError,
    Project,
    ProjectNotFoundError,
    resolve_project,
)
from normflow.workspace import init_workspace as init_project


def test_resolve_project_returns_canonical_identity_at_project_root(tmp_path: Path):
    project_root = init_project(str(tmp_path / "project"))

    project = resolve_project(project_root)

    assert project == Project(
        root=project_root.resolve(),
        database=(project_root / "normflow.db").resolve(),
    )


def test_resolve_project_selects_nearest_ancestor_marker(tmp_path: Path):
    outer = init_project(str(tmp_path / "outer"))
    # Model a legacy nested layout directly; current initialization rejects it.
    inner = outer / "folder" / "inner"
    inner.mkdir(parents=True)
    shutil.copy2(outer / "normflow.db", inner / "normflow.db")
    starting_directory = inner / "input" / "records"
    starting_directory.mkdir(parents=True)

    project = resolve_project(starting_directory)

    assert project.root == inner


def test_resolve_project_canonicalizes_symlinked_start(tmp_path: Path):
    project_root = init_project(str(tmp_path / "physical-project"))
    nested = project_root / "input" / "incoming"
    nested.mkdir(parents=True)
    symlink = tmp_path / "linked-input"
    symlink.symlink_to(nested, target_is_directory=True)

    project = resolve_project(symlink)

    assert project == Project(
        root=project_root,
        database=project_root / "normflow.db",
    )


def test_resolve_project_explains_how_to_recover_when_no_marker_exists(
    tmp_path: Path,
):
    starting_directory = tmp_path / "outside" / "nested"
    starting_directory.mkdir(parents=True)

    with pytest.raises(ProjectNotFoundError) as exc_info:
        resolve_project(starting_directory)

    message = str(exc_info.value)
    assert str(starting_directory.resolve()) in message
    assert "normflow init" in message


def test_resolve_project_stops_at_invalid_nearest_marker(tmp_path: Path):
    outer = init_project(str(tmp_path / "outer"))
    invalid_root = outer / "nested"
    invalid_root.mkdir()
    invalid_database = invalid_root / "normflow.db"
    invalid_database.write_text("not a sqlite database", encoding="utf-8")

    with pytest.raises(InvalidProjectError) as exc_info:
        resolve_project(invalid_root)

    message = str(exc_info.value)
    assert str(invalid_database) in message
    assert "recover" in message.lower()


def test_resolve_project_treats_dangling_database_symlink_as_nearest_marker(
    tmp_path: Path,
):
    outer = init_project(str(tmp_path / "outer"))
    damaged_root = outer / "nested"
    damaged_root.mkdir()
    damaged_database = damaged_root / "normflow.db"
    damaged_database.symlink_to(tmp_path / "missing-database")

    with pytest.raises(InvalidProjectError) as exc_info:
        resolve_project(damaged_root)

    assert str(damaged_database) in str(exc_info.value)
