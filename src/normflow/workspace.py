"""Workspace operations: init and info."""

from functools import lru_cache
from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine, select

from .models import ExampleMapping, Suggestion


def init_workspace(path: str) -> Path:
    """Create a new project workspace at the given path."""
    ws = Path(path).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)

    for d in ("input", "output", "samples", ".normflow"):
        (ws / d).mkdir(exist_ok=True)

    db_path = ws / "normflow.db"
    engine = _make_engine(str(db_path))
    SQLModel.metadata.create_all(engine)

    return ws


def workspace_info(path: str) -> dict:
    """Return info about an existing project workspace."""
    ws = WorkspaceService(path)

    with ws.session() as session:
        from sqlmodel import func
        mapping_count = session.exec(
            select(func.count(ExampleMapping.id))
        ).one()
        suggestion_count = session.exec(
            select(func.count(Suggestion.id))
        ).one()

    return {
        "workspace": str(ws._path),
        "database": str(ws._db_path),
        "mappings": mapping_count,
        "suggestions": suggestion_count,
    }


class WorkspaceService:
    """Work with an existing project workspace."""

    def __init__(self, path: str):
        self._path = Path(path).expanduser().resolve()
        self._db_path = self._path / "normflow.db"
        self._engine = _make_engine(str(self._db_path))
        self.validate()

    def validate(self) -> None:
        if not self._db_path.exists():
            msg = f"Not a NormFlow workspace: no database found at {self._db_path}"
            raise ValueError(msg)

    def session(self):
        """Context manager for database sessions."""
        return Session(self._engine)


@lru_cache(maxsize=32)
def _make_engine(db_url: str):
    return create_engine(f"sqlite:///{db_url}")
