"""Shared test helpers."""

from pathlib import Path

from normflow.mapping_service import ExampleMapping, MappingService


def seed_mappings(ws_path: Path, pairs: list[tuple[str, str]]) -> None:
    """Insert ExampleMapping rows into the workspace."""
    ms = MappingService(str(ws_path))
    with ms.session() as session:
        for raw, norm in pairs:
            session.add(ExampleMapping(raw_text=raw, normalized_text=norm))
        session.commit()
