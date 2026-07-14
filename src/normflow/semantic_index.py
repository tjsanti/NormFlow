"""Semantic search index — FAISS + SentenceTransformer."""

from __future__ import annotations

import os
import pickle
import shutil
import tempfile
from functools import cache
from pathlib import Path
from typing import Literal
from uuid import uuid4

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer


SemanticIndexStatus = Literal["fresh", "refresh_required", "unverified", "missing"]


@cache
def _ensure_model() -> SentenceTransformer:
    return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


class SemanticIndex:
    """Build, persist, and query a FAISS index over Project Mappings."""

    def __init__(self, project_path: str) -> None:
        self._project_path = Path(project_path).expanduser().resolve()
        self._index_dir = self._project_path / ".normflow" / "faiss_index"
        self._generations_dir = self._index_dir / "generations"
        self._current_path = self._index_dir / "current"
        self._refresh_required_path = (
            self._project_path / ".normflow" / "semantic_index_refresh_required"
        )
        self._refresh_failed_path = (
            self._project_path / ".normflow" / "semantic_index_refresh_failed"
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        return (self._active_index_dir() / "index.faiss").exists()

    def _current_generation(self) -> str | None:
        try:
            generation = self._current_path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            return None
        candidate = self._generations_dir / generation
        if generation and (candidate / "index.faiss").exists():
            return generation
        return None

    def _active_index_dir(self) -> Path:
        """Resolve one atomically-published generation or the legacy layout."""
        generation = self._current_generation()
        return self._generations_dir / generation if generation else self._index_dir

    def status(self) -> SemanticIndexStatus:
        """Return the persisted freshness state of this Project's index."""
        if not self.exists():
            return "missing"
        if self._refresh_required_path.exists():
            return "refresh_required"
        if (self._active_index_dir() / "freshness").exists():
            return "fresh"
        return "unverified"

    def mark_refresh_required(self) -> None:
        """Record that existing index data predates the current Mappings."""
        if not self.exists():
            return
        self._refresh_required_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._refresh_required_path.with_suffix(".tmp")
        temporary.write_text("refresh required\n", encoding="utf-8")
        os.replace(temporary, self._refresh_required_path)

    def clear_refresh_required(self) -> None:
        """Restore a non-dirty index state after a Mapping transaction rolls back."""
        self._refresh_required_path.unlink(missing_ok=True)

    def mark_refresh_failed(self) -> None:
        """Persist an actionable warning after an automatic or manual failure."""
        self._refresh_failed_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._refresh_failed_path.with_suffix(".tmp")
        temporary.write_text("refresh failed\n", encoding="utf-8")
        os.replace(temporary, self._refresh_failed_path)

    def warning(self) -> str | None:
        """Describe non-fresh index behavior for adapters and users."""
        if self._refresh_failed_path.exists():
            if self.status() == "missing":
                return (
                    "Automatic semantic index refresh failed; semantic and LLM Suggestions "
                    "are unavailable. Exact matching remains available. Run `normflow index "
                    "build` to retry."
                )
            return (
                "Automatic semantic index refresh failed; Suggestions may use earlier "
                "Mappings. Run `normflow index build` to retry."
            )
        status = self.status()
        if status == "refresh_required":
            return "The semantic index will refresh before the next semantic Suggestion."
        if status == "unverified":
            return "The existing semantic index will be verified by rebuilding before use."
        if status == "missing":
            return "The semantic index will be built before the next semantic Suggestion."
        return None

    def build(self, mappings: list[tuple[str, str]]) -> int:
        """Build index from mapping pairs. Returns number of entries."""
        seen: set[str] = set()
        raw_texts: list[str] = []
        table: list[tuple[str, str]] = []

        for raw_text, normalized_text in mappings:
            rt = raw_text.strip()
            if not rt or rt in seen:
                continue
            seen.add(rt)
            raw_texts.append(rt)
            table.append((rt, normalized_text))

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

    def search(
        self,
        query_text: str,
        *,
        limit: int = 1,
        threshold: float = 0.85,
    ) -> list[dict[str, object]]:
        loaded = self.load()
        if loaded is None:
            return []

        index, table = loaded

        if index.ntotal == 0 or limit <= 0:
            return []

        query_embedding = _ensure_model().encode([query_text], normalize_embeddings=True)
        query_vec = np.asarray(query_embedding, dtype="float32")

        scores, faiss_ids = index.search(query_vec, min(limit, index.ntotal))

        results: list[dict[str, object]] = []
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

    def clear(self) -> None:
        if self._index_dir.exists():
            shutil.rmtree(self._index_dir)
        self._refresh_required_path.unlink(missing_ok=True)
        self._refresh_failed_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Internal: persistence
    # ------------------------------------------------------------------

    def load(self) -> tuple[faiss.Index, list[tuple[str, str]]] | None:
        active_dir = self._active_index_dir()
        if not (active_dir / "index.faiss").exists():
            return None
        index = faiss.read_index(str(active_dir / "index.faiss"))
        with open(active_dir / "mapping_table.pkl", "rb") as f:
            mapping_table = pickle.load(f)  # noqa: S301
        return index, mapping_table

    def _save(self, index: faiss.Index, mapping_table: list[tuple[str, str]]) -> None:
        self._generations_dir.mkdir(parents=True, exist_ok=True)
        previous_generation = self._current_generation()
        existing_generations = {
            path.name
            for path in self._generations_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        }
        generation = uuid4().hex
        temporary_dir = Path(tempfile.mkdtemp(prefix=".building-", dir=self._generations_dir))
        generation_dir = self._generations_dir / generation
        temporary_pointer = self._index_dir / f".current-{generation}"
        published = False

        try:
            faiss.write_index(index, str(temporary_dir / "index.faiss"))
            with open(temporary_dir / "mapping_table.pkl", "wb") as f:
                pickle.dump(mapping_table, f)
            (temporary_dir / "freshness").write_text("verified\n", encoding="utf-8")

            os.replace(temporary_dir, generation_dir)
            temporary_pointer.write_text(f"{generation}\n", encoding="utf-8")
            os.replace(temporary_pointer, self._current_path)
            published = True

            self._refresh_required_path.unlink(missing_ok=True)
            self._refresh_failed_path.unlink(missing_ok=True)
            for old_generation in existing_generations - {previous_generation}:
                shutil.rmtree(self._generations_dir / old_generation, ignore_errors=True)
        finally:
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir)
            temporary_pointer.unlink(missing_ok=True)
            if not published and generation_dir.exists():
                shutil.rmtree(generation_dir, ignore_errors=True)
