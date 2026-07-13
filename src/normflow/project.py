"""Resolve the active NormFlow Project from a filesystem location."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path


class ProjectNotFoundError(ValueError):
    """No NormFlow Project marker exists at or above the searched location."""


class InvalidProjectError(ValueError):
    """The nearest Project marker is not a usable NormFlow database."""


class ProjectNestingError(ValueError):
    """Initialization would nest one Project inside another."""


@dataclass(frozen=True)
class Project:
    """Canonical identity of a NormFlow Project."""

    root: Path
    database: Path


def _validate_database(database: Path) -> None:
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
            check = connection.execute("PRAGMA quick_check").fetchone()
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
    except (OSError, sqlite3.Error) as exc:
        msg = (
            f"The nearest Project database at {database} is unreadable or invalid. "
            "Recover it from a backup or move it aside before running `normflow init`."
        )
        raise InvalidProjectError(msg) from exc

    supported_schema = "examplemapping" in tables and bool(
        {"reviewitem", "suggestion"} & tables
    )
    if check != ("ok",) or not supported_schema:
        msg = (
            f"The nearest Project database at {database} has an invalid or unsupported "
            "NormFlow schema. Recover it from a compatible backup."
        )
        raise InvalidProjectError(msg)


def project_at(root: str | Path) -> Project:
    """Return the valid Project rooted at exactly ``root``."""
    canonical_root = Path(root).expanduser().resolve()
    database = canonical_root / "normflow.db"
    if not (database.exists() or database.is_symlink()):
        raise ProjectNotFoundError(f"No NormFlow Project found at {canonical_root}.")
    _validate_database(database)
    return Project(root=canonical_root, database=database)


def resolve_project(start: str | Path) -> Project:
    """Return the nearest Project containing the canonical start location."""
    location = Path(start).expanduser().resolve()
    for candidate in (location, *location.parents):
        database = candidate / "normflow.db"
        if database.exists() or database.is_symlink():
            return project_at(candidate)

    msg = (
        f"No NormFlow Project found from {location}. "
        "Run `normflow init` from the directory you want to use as a Project."
    )
    raise ProjectNotFoundError(msg)
