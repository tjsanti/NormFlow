"""Workspace operations: init."""

from pathlib import Path

from sqlmodel import SQLModel

from .mapping_service import _make_engine, ExampleMapping, ReviewItem


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
