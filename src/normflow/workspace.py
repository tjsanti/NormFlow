"""Workspace operations: init and info."""

from pathlib import Path

from sqlmodel import Session, select

from .models import ExampleMapping, Suggestion


def init_workspace(path: str) -> Path:
    """Create a new project workspace at the given path.

    Creates:
    - <path>/normflow.db  (SQLite database with tables)
    - <path>/input/       (raw records)
    - <path>/output/      (normalized results)
    - <path>/samples/     (portable flat files)
    """
    ws = Path(path).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)

    # Create directories
    (ws / "input").mkdir(exist_ok=True)
    (ws / "output").mkdir(exist_ok=True)
    (ws / "samples").mkdir(exist_ok=True)

    # Create database with tables
    db_path = ws / "normflow.db"
    engine = _make_engine(str(db_path))
    with engine.connect() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS examplemapping (
                id INTEGER PRIMARY KEY,
                raw_text TEXT NOT NULL,
                normalized_text TEXT NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_examplemapping_raw_text ON examplemapping(raw_text)"
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS suggestion (
                id INTEGER PRIMARY KEY,
                raw_text TEXT NOT NULL,
                suggested_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_suggestion_raw_text ON suggestion(raw_text)"
        )
        conn.commit()

    return ws


def workspace_info(path: str) -> dict:
    """Return info about an existing project workspace."""
    ws = WorkspaceService(path)

    with ws.session() as session:
        mapping_count = session.exec(
            select(ExampleMapping)
        ).all().__len__()
        suggestion_count = session.exec(
            select(Suggestion)
        ).all().__len__()

    return {
        "workspace": str(ws._path),
        "database": str(ws._db_path),
        "mappings": mapping_count,
        "suggestions": suggestion_count,
    }


class WorkspaceService:
    """Work with an existing project workspace.

    Validates the workspace exists and provides database sessions.
    """

    def __init__(self, path: str):
        self._path = Path(path).expanduser().resolve()
        self._db_path = self._path / "normflow.db"
        self.validate()

    def validate(self) -> None:
        """Raise ValueError if not a valid workspace."""
        if not self._db_path.exists():
            msg = f"Not a NormFlow workspace: no database found at {self._db_path}"
            raise ValueError(msg)

    def session(self):
        """Context manager for database sessions."""
        engine = _make_engine(str(self._db_path))
        return Session(engine)


def _make_engine(db_url: str):
    from sqlmodel import create_engine

    return create_engine(f"sqlite:///{db_url}")
