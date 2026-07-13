"""MappingService — single seam over all NormFlow domain operations.

Consolidates CSV import/export, suggest (exact + semantic), review,
Project info, and FAISS index build/clear. SQLModel, sessions, and
model imports are internal.
"""

import csv
import io
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import TypedDict

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Field as SField, SQLModel, Session, create_engine, func, select

from .semantic_index import SemanticIndex
from .suggestion_lookup import SuggestionItem, SuggestionLookup


class ReviewItemNotFoundError(ValueError):
    """The requested Review Item is no longer pending."""


class BulkAcceptError(ValueError):
    """A selected Review Item bulk acceptance could not be committed."""


class BulkAcceptStaleItemsError(BulkAcceptError):
    """One or more selected Review Items are no longer pending."""


class BulkAcceptPersistenceError(BulkAcceptError):
    """Mappings for selected Review Items could not be persisted."""


@dataclass(frozen=True)
class BulkAcceptResult:
    """Outcome of atomically accepting selected Review Items."""

    accepted: int


class ProjectInfo(TypedDict):
    """Canonical Project identity and current statistics."""

    project: str
    database: str
    mappings: int
    review_items: int

# ---------------------------------------------------------------------------
# Internal models
# ---------------------------------------------------------------------------


class ExampleMapping(SQLModel, table=True):
    """A raw_text -> normalized_text mapping pair."""

    id: int | None = SField(default=None, primary_key=True)
    raw_text: str = SField(index=True)
    normalized_text: str


class ReviewItem(SQLModel, table=True):
    """A raw text input awaiting human review."""

    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = SField(default=None, primary_key=True)
    raw_text: str = SField(index=True)
    suggested_text: str
    created_at: datetime = SField(default_factory=lambda: datetime.now(timezone.utc))


@cache
def _make_engine(db_url: str):
    return create_engine(f"sqlite:///{db_url}")


# ---------------------------------------------------------------------------
# MappingService
# ---------------------------------------------------------------------------


class MappingService:
    """Single seam over all NormFlow Project operations."""

    def __init__(self, project_path: str | Path):
        self._path = Path(project_path).expanduser().resolve()
        self._db_path = self._path / "normflow.db"
        self._engine = _make_engine(str(self._db_path))
        self.validate()
        self._migrate_legacy_suggestions()

    @classmethod
    def initialize(cls, project_path: str | Path) -> "MappingService":
        """Create the Project schema behind the service boundary."""
        root = Path(project_path).expanduser().resolve()
        engine = _make_engine(str(root / "normflow.db"))
        SQLModel.metadata.create_all(engine)
        return cls(root)

    def _migrate_legacy_suggestions(self) -> None:
        """Upgrade the former queue-specific Suggestion table in place."""
        if "suggestion" not in inspect(self._engine).get_table_names():
            return

        with self._engine.begin() as connection:
            ReviewItem.__table__.create(connection, checkfirst=True)
            connection.execute(text(
                """
                INSERT INTO reviewitem (id, raw_text, suggested_text, created_at)
                SELECT id, raw_text, suggested_text, created_at
                FROM suggestion
                WHERE status = 'pending'
                ORDER BY created_at, id
                """
            ))
            connection.execute(text("DROP TABLE suggestion"))

    def validate(self) -> None:
        if not self._db_path.exists():
            msg = f"Not a NormFlow Project: no database found at {self._db_path}"
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
    # Project info
    # ------------------------------------------------------------------

    def project_info(self) -> ProjectInfo:
        """Return canonical Project identity and current statistics."""
        with self.session() as session:
            mapping_count = session.exec(
                select(func.count(ExampleMapping.id))
            ).one()
            review_item_count = session.exec(
                select(func.count(ReviewItem.id))
            ).one()

        return {
            "project": str(self._path),
            "database": str(self._db_path),
            "mappings": mapping_count,
            "review_items": review_item_count,
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
        review_items = 0

        with self.session() as session:
            existing_raw = {r for r in session.exec(select(ExampleMapping.raw_text)).all()}
            existing_review_items = {r for r in session.exec(select(ReviewItem.raw_text)).all()}

            for raw_text in unique_values:
                # Skip if already awaiting review from a prior import.
                if raw_text in existing_review_items:
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
                        # LLM suggestion — store it on a Review Item.
                        session.add(ReviewItem(raw_text=raw_text, suggested_text=result.suggested_text))
                        review_items += 1
                else:
                    # No match — create a Review Item for manual entry.
                    session.add(ReviewItem(raw_text=raw_text, suggested_text=""))
                    review_items += 1

            session.commit()

        return {
            "auto_committed": auto_committed,
            "review_items": review_items,
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

    def list_review_items(self) -> list[dict]:
        with self.session() as session:
            items = session.exec(
                select(ReviewItem).order_by(ReviewItem.created_at, ReviewItem.id)
            ).all()

        return [
            {
                "id": item.id,
                "raw_text": item.raw_text,
                "suggested_text": item.suggested_text,
            }
            for item in items
        ]

    def _accept_review_item(self, record_id: int, normalized_text: str | None) -> None:
        with self.session() as session:
            item = session.exec(
                select(ReviewItem).where(ReviewItem.id == record_id)
            ).first()

            if item is None:
                msg = f"Review Item with id {record_id} not found"
                raise ReviewItemNotFoundError(msg)

            approved_text = (
                normalized_text if normalized_text is not None else item.suggested_text
            ).strip()
            if not approved_text:
                raise ValueError("Normalized text must not be blank")

            session.add(
                ExampleMapping(
                    raw_text=item.raw_text,
                    normalized_text=approved_text,
                )
            )
            session.delete(item)
            session.commit()

    def accept_review_item(self, record_id: int) -> None:
        self._accept_review_item(record_id, None)

    def accept_review_items(self, record_ids: list[int]) -> BulkAcceptResult:
        if not record_ids:
            raise BulkAcceptError("Select at least one Review Item")
        if any(not isinstance(record_id, int) or isinstance(record_id, bool) or record_id <= 0
               for record_id in record_ids):
            raise BulkAcceptError("Review Item IDs must be positive integers")
        if len(set(record_ids)) != len(record_ids):
            raise BulkAcceptError("Review Item IDs must not contain duplicates")

        with self.session() as session:
            items = session.exec(
                select(ReviewItem).where(ReviewItem.id.in_(record_ids))
            ).all()
            found_ids = {item.id for item in items}
            stale_ids = [record_id for record_id in record_ids if record_id not in found_ids]
            if stale_ids:
                joined_ids = ", ".join(str(record_id) for record_id in stale_ids)
                raise BulkAcceptStaleItemsError(
                    f"Review Items with IDs {joined_ids} are no longer pending"
                )
            blank_ids = [item.id for item in items if not item.suggested_text.strip()]
            if blank_ids:
                joined_ids = ", ".join(str(record_id) for record_id in blank_ids)
                raise BulkAcceptError(
                    f"Review Items with IDs {joined_ids} have blank Suggestions"
                )
            for item in items:
                session.add(ExampleMapping(
                    raw_text=item.raw_text,
                    normalized_text=item.suggested_text.strip(),
                ))
                session.delete(item)
            try:
                session.commit()
            except SQLAlchemyError as error:
                session.rollback()
                raise BulkAcceptPersistenceError(
                    "Could not accept selected Review Items; no changes were made"
                ) from error
        return BulkAcceptResult(accepted=len(items))

    def edit_and_accept_review_item(self, record_id: int, normalized_text: str) -> None:
        self._accept_review_item(record_id, normalized_text)

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
