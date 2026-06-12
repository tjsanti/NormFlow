"""CSV import/export operations for mappings."""

import csv
from pathlib import Path

from sqlmodel import select

from .models import ExampleMapping
from .workspace import WorkspaceService


def import_mappings(
    workspace_path: str,
    csv_path: str,
    source_column: str,
    target_column: str,
) -> tuple[int, int]:
    """Import mappings from a CSV file into the workspace database.

    Returns (imported, skipped) counts.
    """
    ws = WorkspaceService(workspace_path)

    csv_file = Path(csv_path).expanduser().resolve()
    if not csv_file.exists():
        msg = f"CSV file not found: {csv_file}"
        raise FileNotFoundError(msg)

    # Read CSV
    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            msg = "CSV file is empty or has no header row"
            raise ValueError(msg)

        available = list(reader.fieldnames)
        if source_column not in available:
            msg = f"CSV does not contain a column named '{source_column}'. Available columns: {', '.join(available)}"
            raise ValueError(msg)
        if target_column not in available:
            msg = f"CSV does not contain a column named '{target_column}'. Available columns: {', '.join(available)}"
            raise ValueError(msg)

        rows = list(reader)

    with ws.session() as session:
        imported = 0
        skipped = 0
        for row in rows:
            raw_text = row[source_column].strip()
            normalized_text = row[target_column].strip()

            # Skip empty rows
            if not raw_text or not normalized_text:
                continue

            # Check for duplicate (same raw_text)
            existing = session.exec(
                select(ExampleMapping).where(ExampleMapping.raw_text == raw_text)
            ).first()

            if existing:
                skipped += 1
            else:
                session.add(ExampleMapping(raw_text=raw_text, normalized_text=normalized_text))
                imported += 1

        session.commit()

    return imported, skipped


def export_mappings(
    workspace_path: str,
    csv_path: str,
    source_column: str = "raw_text",
    target_column: str = "normalized_text",
) -> int:
    """Export all mappings from the workspace database to a CSV file.

    Returns the number of mappings exported.
    """
    ws = WorkspaceService(workspace_path)

    with ws.session() as session:
        mappings = session.exec(select(ExampleMapping)).all()
        count = len(mappings)

    output_path = Path(csv_path).expanduser().resolve()
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[source_column, target_column])
        writer.writeheader()
        for m in mappings:
            writer.writerow({source_column: m.raw_text, target_column: m.normalized_text})

    return count
