"""MappingService — single seam over all NormFlow domain operations.

Consolidates CSV import/export, suggest (exact + semantic), review,
workspace info, and FAISS index build/clear. SQLModel, sessions, and
model imports are internal.
"""

import csv
import io
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

from pydantic import BaseModel, Field
from sqlmodel import Field as SField, SQLModel, Session, create_engine, func, select

from .semantic_index import SemanticIndex

# ---------------------------------------------------------------------------
# Internal models
# ---------------------------------------------------------------------------


class ExampleMapping(SQLModel, table=True):
    """A raw_text -> normalized_text mapping pair."""

    id: int | None = SField(default=None, primary_key=True)
    raw_text: str = SField(index=True)
    normalized_text: str


class Suggestion(SQLModel, table=True):
    """A system-generated candidate for a raw_text record."""

    id: int | None = SField(default=None, primary_key=True)
    raw_text: str = SField(index=True)
    suggested_text: str
    status: str = SField(default="pending")
    created_at: datetime = SField(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SuggestionItem(BaseModel):
    """A single suggestion returned by lookup."""

    suggested_text: str
    method: str
    confidence: float = Field(ge=0.0, le=1.0)


@cache
def _make_engine(db_url: str):
    return create_engine(f"sqlite:///{db_url}")


# ---------------------------------------------------------------------------
# MappingService
# ---------------------------------------------------------------------------


class MappingService:
    """Single seam over all NormFlow workspace operations."""

    def __init__(self, workspace_path: str):
        self._path = Path(workspace_path).expanduser().resolve()
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

    # ------------------------------------------------------------------
    # Workspace info
    # ------------------------------------------------------------------

    def workspace_info(self) -> dict:
        with self.session() as session:
            mapping_count = session.exec(
                select(func.count(ExampleMapping.id))
            ).one()
            suggestion_count = session.exec(
                select(func.count(Suggestion.id))
            ).one()

        return {
            "workspace": str(self._path),
            "database": str(self._db_path),
            "mappings": mapping_count,
            "suggestions": suggestion_count,
        }

    # ------------------------------------------------------------------
    # CSV import / export
    # ------------------------------------------------------------------

    def import_mappings(
        self,
        csv_path: str,
        source_column: str,
        target_column: str,
    ) -> tuple[int, int]:
        csv_file = Path(csv_path).expanduser().resolve()
        if not csv_file.exists():
            msg = f"CSV file not found: {csv_file}"
            raise FileNotFoundError(msg)

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

        with self.session() as session:
            # ponytail: load existing raw_texts into set — O(1) lookup vs O(n) queries
            existing = session.exec(select(ExampleMapping.raw_text)).all()

            imported = 0
            skipped = 0
            for row in rows:
                raw_text = row[source_column].strip()
                normalized_text = row[target_column].strip()

                if not raw_text or not normalized_text:
                    continue

                if raw_text in existing:
                    skipped += 1
                else:
                    session.add(ExampleMapping(raw_text=raw_text, normalized_text=normalized_text))
                    imported += 1

            session.commit()

        return imported, skipped

    def export_mappings(
        self,
        csv_path: str,
        source_column: str = "raw_text",
        target_column: str = "normalized_text",
    ) -> int:
        with self.session() as session:
            mappings = session.exec(select(ExampleMapping)).all()
            count = len(mappings)

        output_path = Path(csv_path).expanduser().resolve()
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[source_column, target_column])
            writer.writeheader()
            for m in mappings:
                writer.writerow({source_column: m.raw_text, target_column: m.normalized_text})

        return count

    # ------------------------------------------------------------------
    # Suggest
    # ------------------------------------------------------------------

    def lookup(
        self,
        raw_text: str,
        *,
        semantic: bool = True,
        threshold: float = 0.85,
        limit: int = 1,
    ) -> list[SuggestionItem]:
        suggestions: list[SuggestionItem] = []

        with self.session() as session:
            mapping = session.exec(
                select(ExampleMapping).where(ExampleMapping.raw_text == raw_text)
            ).first()

            if mapping:
                suggestions.append(SuggestionItem(
                    suggested_text=mapping.normalized_text,
                    method="exact",
                    confidence=1.0,
                ))

        if not suggestions and semantic:
            idx = SemanticIndex(str(self._path))
            if idx.exists():
                semantic_results = idx.search(raw_text, limit=limit, threshold=threshold)
                for sr in semantic_results:
                    suggestions.append(SuggestionItem(
                        suggested_text=sr["normalized_text"],
                        method="semantic",
                        confidence=sr["score"],
                    ))

        return suggestions[:limit]

    def lookup_batch(
        self,
        csv_path: str,
        column: str,
        output_column: str = "normalized_text",
        *,
        semantic: bool = True,
        threshold: float = 0.85,
    ) -> str:
        input_file = Path(csv_path).expanduser().resolve()
        if not input_file.exists():
            msg = f"CSV file not found: {input_file}"
            raise FileNotFoundError(msg)

        with open(input_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                msg = "CSV file is empty or has no header row"
                raise ValueError(msg)

            available = list(reader.fieldnames)
            if column not in available:
                msg = f"CSV does not contain a column named '{column}'. Available columns: {', '.join(available)}"
                raise ValueError(msg)

            rows = list(reader)

        out_fieldnames = list(rows[0].keys()) if rows else []
        if output_column not in out_fieldnames:
            out_fieldnames.append(output_column)

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=out_fieldnames, lineterminator="\n")
        writer.writeheader()

        for row in rows:
            if all(row.get(col, "").strip() == "" for col in out_fieldnames):
                continue

            raw_text = row.get(column, "").strip()
            out_row = dict(row)

            if raw_text:
                results = self.lookup(raw_text, semantic=semantic, threshold=threshold, limit=1)
                if results:
                    out_row[output_column] = results[0].suggested_text
                else:
                    out_row[output_column] = ""
            else:
                out_row[output_column] = ""

            writer.writerow(out_row)

        return output.getvalue()

    # ------------------------------------------------------------------
    # Review
    # ------------------------------------------------------------------

    def list_pending_suggestions(self) -> list[dict]:
        with self.session() as session:
            suggestions = session.exec(
                select(Suggestion).where(Suggestion.status == "pending")
            ).all()

        return [
            {
                "id": s.id,
                "raw_text": s.raw_text,
                "suggested_text": s.suggested_text,
            }
            for s in suggestions
        ]

    def _process_suggestion(self, record_id: int, status: str, normalized_text: str | None) -> None:
        with self.session() as session:
            suggestion = session.exec(
                select(Suggestion).where(Suggestion.id == record_id)
            ).first()

            if suggestion is None:
                msg = f"Suggestion with id {record_id} not found"
                raise ValueError(msg)

            if suggestion.status != "pending":
                msg = f"Suggestion {record_id} already reviewed with status '{suggestion.status}'"
                raise ValueError(msg)

            suggestion.status = status
            session.add(
                ExampleMapping(
                    raw_text=suggestion.raw_text,
                    normalized_text=normalized_text if normalized_text is not None else suggestion.suggested_text,
                )
            )
            session.commit()

    def accept_suggestion(self, record_id: int) -> None:
        self._process_suggestion(record_id, "accepted", None)

    def edit_suggestion(self, record_id: int, normalized_text: str) -> None:
        self._process_suggestion(record_id, "accepted_edited", normalized_text)

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------

    def build_index(self) -> int:
        with self.session() as session:
            mappings = session.exec(select(ExampleMapping)).all()
        mapping_pairs = [(m.raw_text, m.normalized_text) for m in mappings]
        idx = SemanticIndex(str(self._path))
        return idx.build(mapping_pairs)

    def clear_index(self) -> None:
        idx = SemanticIndex(str(self._path))
        idx.clear()
