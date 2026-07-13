"""Workspace operations: init."""

from pathlib import Path

from sqlmodel import SQLModel

from .mapping_service import _make_engine, ExampleMapping, ReviewItem
from .project import Project, ProjectNotFoundError, ProjectNestingError, project_at, resolve_project


def _descendant_project(root: Path) -> Project | None:
    for database in sorted(root.rglob("normflow.db")):
        if database.parent != root:
            return project_at(database.parent)
    return None


def init_workspace(path: str | Path) -> Path:
    """Create a new project workspace at the given path."""
    ws = Path(path).expanduser().resolve()

    db_path = ws / "normflow.db"
    if db_path.exists() or db_path.is_symlink():
        project_at(ws)
    else:
        try:
            ancestor = resolve_project(ws.parent)
        except ProjectNotFoundError:
            pass
        else:
            msg = (
                f"Cannot initialize a nested Project at {ws}; "
                f"the directory is already inside the Project at {ancestor.root}."
            )
            raise ProjectNestingError(msg)

        descendant = _descendant_project(ws) if ws.is_dir() else None
        if descendant is not None:
            msg = (
                f"Cannot initialize the Project at {ws}; it would contain the "
                f"nested Project at {descendant.root}."
            )
            raise ProjectNestingError(msg)

    ws.mkdir(parents=True, exist_ok=True)

    for d in ("input", "output", "samples", ".normflow"):
        (ws / d).mkdir(exist_ok=True)

    engine = _make_engine(str(db_path))
    SQLModel.metadata.create_all(engine)

    return ws
