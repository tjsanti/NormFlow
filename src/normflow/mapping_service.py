"""MappingService — single seam over all NormFlow domain operations.

Consolidates CSV import/export, suggest (exact + semantic), review,
Project info, and FAISS index build/clear. SQLModel, sessions, and
model imports are internal.
"""

import csv
import io
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import TypedDict

from .batch_import import (
    BatchImportRun,
    BatchImportRuns,
    ProjectBusyError,
    coordinated_writer,
)

from sqlalchemy import delete, inspect, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import (
    Field as SField,
    Session as _Session,
    SQLModel as _SQLModel,
    create_engine,
    func,
    select,
)

from .semantic_index import SemanticIndex, SemanticIndexStatus
from .suggestion_lookup import (
    SuggestionItem,
    SuggestionLookup,
    SuggestionProviderError,
)


class ReviewItemNotFoundError(ValueError):
    """The requested Review Item is no longer pending."""


class BatchImportError(RuntimeError):
    """A Batch Import could not complete without partial Project changes."""


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
    semantic_index_status: SemanticIndexStatus
    semantic_index_warning: str | None


class ImportRecordsResult(TypedDict):
    """Batch Import routing counts plus semantic-index state."""

    auto_committed: int
    review_items: int
    skipped: int
    semantic_index_status: SemanticIndexStatus
    semantic_index_warning: str | None


class ReviewItemInfo(TypedDict):
    """Pending Review Item exposed at the Mapping interface."""

    id: int
    raw_text: str
    suggested_text: str


# ---------------------------------------------------------------------------
# Internal models
# ---------------------------------------------------------------------------


class _ExampleMapping(_SQLModel, table=True):
    """A raw_text -> normalized_text mapping pair."""

    __tablename__ = "examplemapping"

    id: int | None = SField(default=None, primary_key=True)
    raw_text: str = SField(index=True)
    normalized_text: str


class _ReviewItem(_SQLModel, table=True):
    """A raw text input awaiting human review."""

    __tablename__ = "reviewitem"
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = SField(default=None, primary_key=True)
    raw_text: str = SField(index=True)
    suggested_text: str
    created_at: datetime = SField(default_factory=lambda: datetime.now(timezone.utc))


class _MappingRevision(_SQLModel, table=True):
    """Persisted version of the Project's Mapping collection."""

    __tablename__ = "mappingrevision"

    id: int = SField(default=1, primary_key=True)
    revision: int = 0


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
        self._ensure_mapping_revision()

    @classmethod
    def initialize(cls, project_path: str | Path) -> "MappingService":
        """Create the Project schema behind the service boundary."""
        root = Path(project_path).expanduser().resolve()
        engine = _make_engine(str(root / "normflow.db"))
        _SQLModel.metadata.create_all(engine)
        return cls(root)

    def _migrate_legacy_suggestions(self) -> None:
        """Upgrade the former queue-specific Suggestion table in place."""
        if "suggestion" not in inspect(self._engine).get_table_names():
            return

        with self._engine.begin() as connection:
            _ReviewItem.__table__.create(connection, checkfirst=True)
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

    def _ensure_mapping_revision(self) -> None:
        """Add revision state to Projects created before revision tracking."""
        with self._engine.begin() as connection:
            _MappingRevision.__table__.create(connection, checkfirst=True)
            connection.execute(text(
                "INSERT OR IGNORE INTO mappingrevision (id, revision) VALUES (1, 0)"
            ))
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS unique_mapping_raw_text "
                "ON examplemapping(raw_text)"
            ))
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS unique_review_item_raw_text "
                "ON reviewitem(raw_text)"
            ))

    def validate(self) -> None:
        if not self._db_path.exists():
            msg = f"Not a NormFlow Project: no database found at {self._db_path}"
            raise ValueError(msg)

    def _session(self):
        """Open a session inside the Mapping implementation."""
        return _Session(self._engine)

    def _commit_mapping_changes(self, session: _Session) -> None:
        """Commit Mapping writes and their revision as one transaction."""
        index = SemanticIndex(str(self._path))
        session.exec(
            update(_MappingRevision)
            .where(_MappingRevision.id == 1)
            .values(revision=_MappingRevision.revision + 1)
        )
        try:
            index.mark_refresh_required()
            session.commit()
        except Exception:
            session.rollback()
            raise

    def _current_mapping_revision(self) -> int:
        with self._session() as session:
            revision = session.get(_MappingRevision, 1)
        if revision is None:
            raise RuntimeError("Project Mapping revision is missing")
        return revision.revision

    def _find_exact_mapping(self, raw_text: str) -> str | None:
        with self._session() as session:
            mapping = session.exec(
                select(_ExampleMapping).where(_ExampleMapping.raw_text == raw_text)
            ).first()
        return mapping.normalized_text if mapping else None

    # ------------------------------------------------------------------
    # Project info
    # ------------------------------------------------------------------

    def project_info(self) -> ProjectInfo:
        """Return canonical Project identity and current statistics."""
        with self._session() as session:
            mapping_count = session.exec(
                select(func.count(_ExampleMapping.id))
            ).one()
            review_item_count = session.exec(
                select(func.count(_ReviewItem.id))
            ).one()
            revision = session.get(_MappingRevision, 1)

        if revision is None:
            raise RuntimeError("Project Mapping revision is missing")

        index = SemanticIndex(str(self._path))
        return {
            "project": str(self._path),
            "database": str(self._db_path),
            "mappings": mapping_count,
            "review_items": review_item_count,
            "semantic_index_status": index.status(revision.revision),
            "semantic_index_warning": index.warning(revision.revision),
        }

    # ------------------------------------------------------------------
    # CSV import / export
    # ------------------------------------------------------------------

    def _read_csv(self, csv_path: str, required_columns: str | tuple[str, ...]) -> tuple[list[str], list[dict]]:
        csv_file = Path(csv_path).expanduser().resolve()
        if not csv_file.exists():
            msg = f"CSV file not found: {csv_file}"
            raise FileNotFoundError(msg)

        available: list[str] = []
        try:
            with open(csv_file, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, strict=True)
                if reader.fieldnames is None:
                    msg = "CSV file is empty or has no header row"
                    raise ValueError(msg)

                available = list(reader.fieldnames)
                required = (required_columns,) if isinstance(required_columns, str) else required_columns
                for column in required:
                    if column not in available:
                        msg = f"CSV does not contain a column named '{column}'. Available columns: {', '.join(available)}"
                        raise ValueError(msg)

                rows = list(reader)
                for row_number, row in enumerate(rows, start=2):
                    for column in required:
                        if row.get(column) is None:
                            raise ValueError(
                                f"CSV row {row_number} does not contain a value "
                                f"for selected column '{column}'"
                            )

                return available, rows
        except UnicodeDecodeError as error:
            raise ValueError("CSV must be UTF-8 text") from error
        except csv.Error as error:
            available_detail = (
                f" Available columns: {', '.join(available)}" if available else ""
            )
            raise ValueError(
                f"CSV could not be parsed: {error}.{available_detail}"
            ) from error

    @coordinated_writer
    def import_mappings(
        self,
        csv_path: str,
        source_column: str,
        target_column: str,
    ) -> tuple[int, int]:
        if source_column == target_column:
            raise ValueError("Source and target columns must differ")
        _, rows = self._read_csv(csv_path, (source_column, target_column))

        with self._session() as session:
            # ponytail: load existing raw_texts into set — O(1) lookup vs O(n) queries
            existing = set(session.exec(select(_ExampleMapping.raw_text)).all())

            imported = 0
            skipped = 0
            for row in rows:
                raw_value = row[source_column]
                normalized_value = row[target_column]
                raw_text = raw_value.strip()
                normalized_text = normalized_value.strip()

                if not raw_text or not normalized_text:
                    continue

                if raw_text in existing:
                    skipped += 1
                else:
                    session.add(_ExampleMapping(raw_text=raw_text, normalized_text=normalized_text))
                    existing.add(raw_text)
                    imported += 1

            if imported:
                self._commit_mapping_changes(session)
            else:
                session.commit()

        return imported, skipped

    def export_mappings(
        self,
        csv_path: str,
        source_column: str = "raw_text",
        target_column: str = "normalized_text",
    ) -> int:
        with self._session() as session:
            mappings = session.exec(select(_ExampleMapping)).all()
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
        if semantic or llm:
            self._refresh_semantic_index_if_needed()
        return self._lookup_with_current_index(
            raw_text, semantic=semantic, llm=llm, threshold=threshold, limit=limit,
        )

    def _lookup_with_current_index(
        self,
        raw_text: str,
        *,
        semantic: bool,
        llm: bool,
        threshold: float,
        limit: int,
        raise_provider_errors: bool = False,
    ) -> list[SuggestionItem]:
        """Look up a Suggestion after this operation's one refresh attempt."""
        return SuggestionLookup(
            exact_lookup=self._find_exact_mapping,
            index=SemanticIndex(str(self._path)),
        ).lookup(
            raw_text,
            semantic=semantic,
            llm=llm,
            threshold=threshold,
            limit=limit,
            raise_provider_errors=raise_provider_errors,
        )

    def _refresh_semantic_index_if_needed(self) -> None:
        if SemanticIndex(str(self._path)).status(self._current_mapping_revision()) == "fresh":
            return
        try:
            self.build_index()
        except ProjectBusyError:
            raise
        except Exception:
            # Preserve the previous index as a visible stale fallback. Adapters
            # report the still-non-fresh Project status to the user.
            return

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
        if semantic or llm:
            self._refresh_semantic_index_if_needed()
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
                results = self._lookup_with_current_index(
                    raw_text, semantic=semantic, llm=llm, threshold=threshold, limit=1,
                )
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

    def _stage_batch_csv(self, csv_path: str) -> Path:
        src = Path(csv_path).expanduser().resolve()
        descriptor, staged_name = tempfile.mkstemp(
            dir=self._batch_csv_dir(), prefix=".current-", suffix=".tmp",
        )
        os.close(descriptor)
        staged = Path(staged_name)
        try:
            shutil.copy2(src, staged)
        except Exception:
            staged.unlink(missing_ok=True)
            raise
        return staged

    def _suspend_batch_csv(self) -> Path | None:
        current = self._batch_csv_dir() / "current.csv"
        if not current.exists():
            return None
        descriptor, backup_name = tempfile.mkstemp(
            dir=self._batch_csv_dir(), prefix=".previous-", suffix=".tmp",
        )
        os.close(descriptor)
        backup = Path(backup_name)
        try:
            os.replace(current, backup)
        except Exception:
            backup.unlink(missing_ok=True)
            raise
        return backup

    def _restore_batch_csv(self, backup: Path | None) -> None:
        current = self._batch_csv_dir() / "current.csv"
        if backup is None:
            current.unlink(missing_ok=True)
        else:
            try:
                os.replace(backup, current)
            except Exception:
                current.unlink(missing_ok=True)
                backup.unlink(missing_ok=True)
                raise

    def _compensate_batch_changes(
        self,
        mapping_ids: list[int],
        review_item_ids: list[int],
        previous_mapping_revision: int | None,
    ) -> None:
        with self._session() as session:
            if mapping_ids:
                session.exec(
                    delete(_ExampleMapping).where(_ExampleMapping.id.in_(mapping_ids))
                )
            if review_item_ids:
                session.exec(
                    delete(_ReviewItem).where(_ReviewItem.id.in_(review_item_ids))
                )
            if previous_mapping_revision is not None:
                session.exec(
                    update(_MappingRevision)
                    .where(_MappingRevision.id == 1)
                    .values(revision=previous_mapping_revision)
                )
            session.commit()

    @coordinated_writer
    def import_records_for_review(
        self,
        csv_path: str,
        column: str,
        *,
        semantic: bool = True,
        llm: bool = True,
        threshold: float = 0.85,
        _on_published=None,
    ) -> ImportRecordsResult:
        if semantic or llm:
            self._refresh_semantic_index_if_needed()
        _, rows = self._read_csv(csv_path, column)
        staged_batch = self._stage_batch_csv(csv_path)

        values = [row.get(column, "").strip() for row in rows]
        unique_values = list(dict.fromkeys(raw_text for raw_text in values if raw_text))
        skipped = len(values) - len(unique_values)

        auto_committed = 0
        review_items = 0
        mappings_added = False
        added_mappings: list[_ExampleMapping] = []
        added_review_items: list[_ReviewItem] = []

        try:
            with self._session() as session:
                existing_raw = {r for r in session.exec(select(_ExampleMapping.raw_text)).all()}
                existing_review_items = {r for r in session.exec(select(_ReviewItem.raw_text)).all()}

                for raw_text in unique_values:
                    # Skip if already awaiting review from a prior import.
                    if raw_text in existing_review_items:
                        skipped += 1
                        continue

                    try:
                        results = self._lookup_with_current_index(
                            raw_text,
                            semantic=semantic,
                            llm=llm,
                            threshold=threshold,
                            limit=1,
                            raise_provider_errors=True,
                        )
                    except SuggestionProviderError as error:
                        raise BatchImportError(
                            "Batch Import failed because the LLM provider could not "
                            f"generate a Suggestion: {error}. Check the configured LLM "
                            "credentials, endpoint, model, and network connection; no "
                            "changes were made."
                        ) from error

                    if results:
                        result = results[0]
                        if result.method in ("exact", "semantic"):
                            # Auto-commit to library if not already there
                            if raw_text not in existing_raw:
                                mapping = _ExampleMapping(
                                    raw_text=raw_text,
                                    normalized_text=result.suggested_text,
                                )
                                session.add(mapping)
                                added_mappings.append(mapping)
                                existing_raw.add(raw_text)
                                mappings_added = True
                            auto_committed += 1
                        else:
                            # LLM suggestion — store it on a Review Item.
                            review_item = _ReviewItem(
                                raw_text=raw_text,
                                suggested_text=result.suggested_text,
                            )
                            session.add(review_item)
                            added_review_items.append(review_item)
                            review_items += 1
                    else:
                        # No match — create a Review Item for manual entry.
                        review_item = _ReviewItem(raw_text=raw_text, suggested_text="")
                        session.add(review_item)
                        added_review_items.append(review_item)
                        review_items += 1

                session.flush()
                mapping_ids = [
                    mapping.id for mapping in added_mappings if mapping.id is not None
                ]
                review_item_ids = [
                    item.id for item in added_review_items if item.id is not None
                ]
                revision = session.get(_MappingRevision, 1)
                previous_mapping_revision = (
                    revision.revision if mappings_added and revision else None
                )
                backup = self._suspend_batch_csv()
                try:
                    if mappings_added:
                        self._commit_mapping_changes(session)
                    else:
                        session.commit()
                except Exception:
                    self._restore_batch_csv(backup)
                    raise
                try:
                    os.replace(staged_batch, self._batch_csv_dir() / "current.csv")
                    info = self.project_info()
                    result: ImportRecordsResult = {
                        "auto_committed": auto_committed,
                        "review_items": review_items,
                        "skipped": skipped,
                        "semantic_index_status": info["semantic_index_status"],
                        "semantic_index_warning": info["semantic_index_warning"],
                    }
                    if _on_published:
                        _on_published(result)
                except Exception:
                    try:
                        self._compensate_batch_changes(
                            mapping_ids,
                            review_item_ids,
                            previous_mapping_revision,
                        )
                        self._restore_batch_csv(backup)
                    except Exception:
                        if backup is not None:
                            backup.unlink(missing_ok=True)
                        raise
                    raise
                if backup is not None:
                    backup.unlink(missing_ok=True)
        finally:
            staged_batch.unlink(missing_ok=True)
        return result

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
        with self._session() as session:
            mappings = {m.raw_text: m.normalized_text for m in session.exec(select(_ExampleMapping)).all()}

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

    def list_review_items(self) -> list[ReviewItemInfo]:
        with self._session() as session:
            items = session.exec(
                select(_ReviewItem).order_by(_ReviewItem.created_at, _ReviewItem.id)
            ).all()

        return [
            {
                "id": item.id,
                "raw_text": item.raw_text,
                "suggested_text": item.suggested_text,
            }
            for item in items
        ]

    @coordinated_writer
    def accept_review_item(
        self,
        review_item_id: int,
        normalized_text: str | None = None,
    ) -> None:
        with self._session() as session:
            item = session.exec(
                select(_ReviewItem).where(_ReviewItem.id == review_item_id)
            ).first()

            if item is None:
                msg = f"Review Item with id {review_item_id} not found"
                raise ReviewItemNotFoundError(msg)

            approved_text = (
                normalized_text if normalized_text is not None else item.suggested_text
            ).strip()
            if not approved_text:
                raise ValueError("Normalized text must not be blank")

            session.add(
                _ExampleMapping(
                    raw_text=item.raw_text,
                    normalized_text=approved_text,
                )
            )
            session.delete(item)
            self._commit_mapping_changes(session)

    @coordinated_writer
    def accept_review_items(self, record_ids: list[int]) -> BulkAcceptResult:
        if not record_ids:
            raise BulkAcceptError("Select at least one Review Item")
        if any(not isinstance(record_id, int) or isinstance(record_id, bool) or record_id <= 0
               for record_id in record_ids):
            raise BulkAcceptError("Review Item IDs must be positive integers")
        if len(set(record_ids)) != len(record_ids):
            raise BulkAcceptError("Review Item IDs must not contain duplicates")

        with self._session() as session:
            items = session.exec(
                select(_ReviewItem).where(_ReviewItem.id.in_(record_ids))
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
                session.add(_ExampleMapping(
                    raw_text=item.raw_text,
                    normalized_text=item.suggested_text.strip(),
                ))
                session.delete(item)
            try:
                self._commit_mapping_changes(session)
            except SQLAlchemyError as error:
                raise BulkAcceptPersistenceError(
                    "Could not accept selected Review Items; no changes were made"
                ) from error
        return BulkAcceptResult(accepted=len(items))

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------

    @coordinated_writer
    def build_index(self) -> int:
        with self._session() as session:
            revision = session.get(_MappingRevision, 1)
            mappings = session.exec(select(_ExampleMapping)).all()
        if revision is None:
            raise RuntimeError("Project Mapping revision is missing")
        mapping_pairs = [(m.raw_text, m.normalized_text) for m in mappings]
        idx = SemanticIndex(str(self._path))
        previous_status = idx.status(revision.revision)
        try:
            return idx.build(
                mapping_pairs,
                mapping_revision=revision.revision,
                current_mapping_revision=self._current_mapping_revision,
            )
        except Exception:
            if previous_status != "fresh":
                idx.mark_refresh_failed()
            raise

    @coordinated_writer
    def clear_index(self) -> None:
        idx = SemanticIndex(str(self._path))
        idx.clear()

    # ------------------------------------------------------------------
    # Durable Batch Import Runs
    # ------------------------------------------------------------------

    def run_batch_import(self, csv_path, column, **kwargs) -> BatchImportRun:
        """Run a Batch Import in the foreground and return its durable terminal state."""
        return BatchImportRuns(self).run(csv_path, column, **kwargs)

    def start_batch_import(self, csv_path, column, **kwargs) -> BatchImportRun:
        """Start a server-owned Batch Import and return its durable active state."""
        return BatchImportRuns(self).start_background(csv_path, column, **kwargs)

    def batch_import_status(self, run_id: str | None = None) -> BatchImportRun:
        """Observe an identified run, or the active/most recent run."""
        return BatchImportRuns(self).status(run_id)

    def retry_batch_import(self, run_id, csv_path, column, **kwargs) -> BatchImportRun:
        """Explicitly retry a failed/interrupted run with resubmitted input."""
        return BatchImportRuns(self).retry(run_id, csv_path, column, **kwargs)
