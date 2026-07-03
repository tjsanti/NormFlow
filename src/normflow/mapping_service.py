"""MappingService — single seam over all NormFlow domain operations.

Consolidates CSV import/export, suggest (exact + semantic), review,
workspace info, and FAISS index build/clear. SQLModel, sessions, and
model imports are internal.
"""

import csv
import io
import shutil
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

from sqlmodel import Field as SField, SQLModel, Session, create_engine, func, select

from .semantic_index import SemanticIndex
from .suggestion_lookup import SuggestionItem, SuggestionLookup

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

    def _find_exact_mapping(self, raw_text: str) -> str | None:
        with self.session() as session:
            mapping = session.exec(
                select(ExampleMapping).where(ExampleMapping.raw_text == raw_text)
            ).first()
        return mapping.normalized_text if mapping else None

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

    def _read_csv(self, csv_path: str, required_columns: str | tuple[str, ...]) -> tuple[list[str], list[dict]]:
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
            required = (required_columns,) if isinstance(required_columns, str) else required_columns
            for column in required:
                if column not in available:
                    msg = f"CSV does not contain a column named '{column}'. Available columns: {', '.join(available)}"
                    raise ValueError(msg)

            return available, list(reader)

    def import_mappings(
        self,
        csv_path: str,
        source_column: str,
        target_column: str,
    ) -> tuple[int, int]:
        _, rows = self._read_csv(csv_path, (source_column, target_column))

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
        llm: bool = True,
        threshold: float = 0.85,
        limit: int = 1,
    ) -> list[SuggestionItem]:
        return SuggestionLookup(
            exact_lookup=self._find_exact_mapping,
            index=SemanticIndex(str(self._path)),
        ).lookup(raw_text, semantic=semantic, llm=llm, threshold=threshold, limit=limit)

    def lookup_batch(
        self,
        csv_path: str,
        column: str,
        output_column: str = "normalized_text",
        *,
        semantic: bool = True,
        llm: bool = True,
        threshold: float = 0.85,
    ) -> str:
        available, rows = self._read_csv(csv_path, column)

        out_fieldnames = list(rows[0].keys()) if rows else available
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
                results = self.lookup(raw_text, semantic=semantic, llm=llm, threshold=threshold, limit=1)
                if results:
                    out_row[output_column] = results[0].suggested_text
                else:
                    out_row[output_column] = ""
            else:
                out_row[output_column] = ""

            writer.writerow(out_row)

        return output.getvalue()

    # ------------------------------------------------------------------
    # Batch import → review
    # ------------------------------------------------------------------

    def _batch_csv_dir(self) -> Path:
        d = self._path / ".batches"
        d.mkdir(exist_ok=True)
        return d

    def _store_batch_csv(self, csv_path: str) -> None:
        src = Path(csv_path).expanduser().resolve()
        dst = self._batch_csv_dir() / "current.csv"
        shutil.copy2(src, dst)

    def import_records_for_review(
        self,
        csv_path: str,
        column: str,
        *,
        semantic: bool = True,
        llm: bool = True,
        threshold: float = 0.85,
    ) -> dict:
        _, rows = self._read_csv(csv_path, column)

        self._store_batch_csv(csv_path)

        values = [row.get(column, "").strip() for row in rows]
        unique_values = list(dict.fromkeys(raw_text for raw_text in values if raw_text))
        skipped = len(values) - len(unique_values)

        auto_committed = 0
        pending = 0

        with self.session() as session:
            existing_raw = {r for r in session.exec(select(ExampleMapping.raw_text)).all()}
            existing_suggestions = {r for r in session.exec(select(Suggestion.raw_text)).all()}

            for raw_text in unique_values:
                # Skip if already has a suggestion (from prior import)
                if raw_text in existing_suggestions:
                    skipped += 1
                    continue

                results = self.lookup(raw_text, semantic=semantic, llm=llm, threshold=threshold, limit=1)

                if results:
                    result = results[0]
                    if result.method in ("exact", "semantic"):
                        # Auto-commit to library if not already there
                        if raw_text not in existing_raw:
                            session.add(ExampleMapping(raw_text=raw_text, normalized_text=result.suggested_text))
                            existing_raw.add(raw_text)
                        auto_committed += 1
                    else:
                        # LLM suggestion — store for review
                        session.add(Suggestion(raw_text=raw_text, suggested_text=result.suggested_text))
                        pending += 1
                else:
                    # No match — store empty suggestion for manual entry
                    session.add(Suggestion(raw_text=raw_text, suggested_text=""))
                    pending += 1

            session.commit()

        return {
            "auto_committed": auto_committed,
            "pending": pending,
            "skipped": skipped,
        }

    def export_normalized_csv(
        self,
        source_column: str = "raw_text",
        output_column: str = "normalized_text",
    ) -> str:
        batch_csv = self._batch_csv_dir() / "current.csv"
        if not batch_csv.exists():
            msg = "No batch CSV found. Import records first."
            raise ValueError(msg)

        # Build lookup from mappings
        with self.session() as session:
            mappings = {m.raw_text: m.normalized_text for m in session.exec(select(ExampleMapping)).all()}

        with open(batch_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list((reader.fieldnames or [])) + [output_column]
            rows = list(reader)

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            out_row = dict(row)
            raw_text = row.get(source_column, "").strip()
            out_row[output_column] = mappings.get(raw_text, "")
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
