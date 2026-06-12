"""Unit tests for WorkspaceService."""

import tempfile
from pathlib import Path

import pytest

from normflow.workspace import WorkspaceService, init_workspace


def test_raises_on_missing_database():
    """WorkspaceService should raise ValueError immediately when the path has no normflow.db."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError, match="Not a NormFlow workspace"):
            WorkspaceService(tmpdir)


def test_accepts_initialized_workspace():
    """WorkspaceService should accept a path that was just initialized."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_workspace(tmpdir)
        service = WorkspaceService(tmpdir)
        assert service._path.resolve() == Path(tmpdir).resolve()


def test_session_can_write_and_read_mappings():
    """WorkspaceService.session() should yield a session that can read/write mappings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_workspace(tmpdir)
        service = WorkspaceService(tmpdir)

        from sqlmodel import Session, select
        from normflow.models import ExampleMapping

        # Write a mapping
        with service.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        # Read it back
        with service.session() as session:
            mapping = session.exec(
                select(ExampleMapping).where(ExampleMapping.raw_text == "colour")
            ).first()

        assert mapping is not None
        assert mapping.normalized_text == "color"
