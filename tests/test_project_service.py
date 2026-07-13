"""Unit tests for MappingService."""

import tempfile
from pathlib import Path

import pytest

from normflow.mapping_service import ExampleMapping, MappingService
from normflow.project_service import init_project


def test_raises_on_missing_database():
    """MappingService should raise ValueError immediately when the path has no normflow.db."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError, match="Not a NormFlow Project"):
            MappingService(tmpdir)


def test_accepts_initialized_project():
    """MappingService should accept a path that was just initialized."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_project(tmpdir)
        service = MappingService(tmpdir)
        assert service._path.resolve() == Path(tmpdir).resolve()


def test_session_can_write_and_read_mappings():
    """MappingService.session() should yield a session that can read/write mappings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_project(tmpdir)
        service = MappingService(tmpdir)

        from sqlmodel import Session, select

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
