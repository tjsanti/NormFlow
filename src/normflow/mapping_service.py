"""MappingService — single seam over all NormFlow domain operations.

Consolidates CSV import/export, suggest (exact + semantic), review,
workspace info, and FAISS index build/clear. SQLModel, sessions, and
model imports are internal.
"""

import csv
import io
import pickle
import shutil
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import faiss
import numpy as np
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer
from sqlmodel import Field as SField, SQLModel, Session, create_engine, func, select

# ---------------------------------------------------------------------------
# Internal models
# ---------------------------------------------------------------------------


class _ExampleMapping(SQLModel, table=True):
    """A raw_text -> normalized_text mapping pair."""

    id: int | None = SField(default=None, primary_key=True)
    raw_text: str = SField(index=True)
    normalized_text: str


class _Suggestion(SQLModel, table=True):
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


# ponytail: 90MB model — lazy singleton so test patches work before first use
_MODEL = None

def _ensure_model():
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _MODEL


@lru_cache(maxsize=32)
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

    def _session(self):
        """Context manager for database sessions."""
        return Session(self._engine)

    # ------------------------------------------------------------------
    # Workspace info
    # ------------------------------------------------------------------

    def workspace_info(self) -> dict:
        with self._session() as session:
            mapping_count = session.exec(
                select(func.count(_ExampleMapping.id))
            ).one()
            suggestion_count = session.exec(
                select(func.count(_Suggestion.id))
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

        with self._session() as session:
            # ponytail: load existing raw_texts into set — O(1) lookup vs O(n) queries
            existing = session.exec(select(_ExampleMapping.raw_text)).all()

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
                    session.add(_ExampleMapping(raw_text=raw_text, normalized_text=normalized_text))
                    imported += 1

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
        threshold: float = 0.85,
        limit: int = 1,
    ) -> list[SuggestionItem]:
        suggestions: list[SuggestionItem] = []

        with self._session() as session:
            mapping = session.exec(
                select(_ExampleMapping).where(_ExampleMapping.raw_text == raw_text)
            ).first()

            if mapping:
                suggestions.append(SuggestionItem(
                    suggested_text=mapping.normalized_text,
                    method="exact",
                    confidence=1.0,
                ))

        if not suggestions and semantic:
            idx = _SemanticIndex(str(self._path))
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
        with self._session() as session:
            suggestions = session.exec(
                select(_Suggestion).where(_Suggestion.status == "pending")
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
        with self._session() as session:
            suggestion = session.exec(
                select(_Suggestion).where(_Suggestion.id == record_id)
            ).first()

            if suggestion is None:
                msg = f"Suggestion with id {record_id} not found"
                raise ValueError(msg)

            if suggestion.status != "pending":
                msg = f"Suggestion {record_id} already reviewed with status '{suggestion.status}'"
                raise ValueError(msg)

            suggestion.status = status
            session.add(
                _ExampleMapping(
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
        idx = _SemanticIndex(str(self._path))
        return idx.build(self._engine)

    def clear_index(self) -> None:
        idx = _SemanticIndex(str(self._path))
        idx.clear()


# ---------------------------------------------------------------------------
# Internal: SemanticIndex (uses MappingService engine to avoid double-open)
# ---------------------------------------------------------------------------


class _SemanticIndex:
    """Build, persist, and query a FAISS index over workspace mappings."""

    def __init__(self, workspace_path: str):
        self._workspace_path = Path(workspace_path).expanduser().resolve()
        self._index_dir = self._workspace_path / ".normflow" / "faiss_index"

    def exists(self) -> bool:
        return (self._index_dir / "index.faiss").exists()

    def _save(self, index, mapping_table):
        self._index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self._index_dir / "index.faiss"))
        with open(self._index_dir / "mapping_table.pkl", "wb") as f:
            pickle.dump(mapping_table, f)

    def load(self):
        if not self.exists():
            return None
        index = faiss.read_index(str(self._index_dir / "index.faiss"))
        with open(self._index_dir / "mapping_table.pkl", "rb") as f:
            mapping_table = pickle.load(f)  # noqa: S301
        return index, mapping_table

    def clear(self):
        if self._index_dir.exists():
            shutil.rmtree(self._index_dir)

    def build(self, engine=None) -> int:
        eng = engine or _make_engine(str(self._workspace_path / "normflow.db"))
        with Session(eng) as session:
            mappings = session.exec(select(_ExampleMapping)).all()

        seen = set()
        raw_texts: list[str] = []
        table: list[tuple[str, str]] = []

        for m in mappings:
            rt = m.raw_text.strip()
            if not rt or rt in seen:
                continue
            seen.add(rt)
            raw_texts.append(rt)
            table.append((rt, m.normalized_text))

        if not raw_texts:
            dim = _ensure_model().get_sentence_embedding_dimension()
            index = faiss.IndexFlatIP(dim)
            self._save(index, table)
            return 0

        embeddings = _ensure_model().encode(raw_texts, normalize_embeddings=True)
        embeddings = np.asarray(embeddings, dtype="float32")

        dim = embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings)

        self._save(index, table)
        return len(table)

    def search(self, query_text: str, limit: int = 1, threshold: float = 0.85):
        loaded = self.load()
        if loaded is None:
            return []

        index, table = loaded

        query_embedding = _ensure_model().encode([query_text], normalize_embeddings=True)
        query_vec = np.asarray(query_embedding, dtype="float32")

        scores, faiss_ids = index.search(query_vec, min(limit, index.ntotal))

        results = []
        for score, fid in zip(scores[0], faiss_ids[0]):
            if fid < 0:
                continue
            if float(score) < threshold:
                continue
            raw_text, normalized_text = table[fid]
            results.append({
                "raw_text": raw_text,
                "normalized_text": normalized_text,
                "score": round(float(score), 4),
            })

        return results
