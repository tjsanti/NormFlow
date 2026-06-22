"""Workspace operations: init and info.

WorkspaceService is a thin shim over MappingService for backward compat.
"""

from pathlib import Path

from sqlmodel import SQLModel, Session

from .mapping_service import MappingService, _make_engine


def init_workspace(path: str) -> Path:
    """Create a new project workspace at the given path."""
    ws = Path(path).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)

    for d in ("input", "output", "samples", ".normflow"):
        (ws / d).mkdir(exist_ok=True)

    db_path = ws / "normflow.db"
    engine = _make_engine(str(db_path))

    # Import models from mapping_service to create tables
    from .mapping_service import _ExampleMapping, _Suggestion

    SQLModel.metadata.create_all(engine)

    return ws


class WorkspaceService:
    """Thin shim over MappingService for backward compat."""

    def __init__(self, path: str):
        self._ms = MappingService(path)
        self._path = self._ms._path
        self._db_path = self._ms._db_path
        self._engine = self._ms._engine

    def validate(self) -> None:
        self._ms.validate()

    def session(self):
        """Context manager for database sessions."""
        return Session(self._engine)
