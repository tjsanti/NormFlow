"""Semantic search index — FAISS + SentenceTransformer."""

from __future__ import annotations

import pickle
import shutil
from functools import cache
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer


@cache
def _ensure_model() -> SentenceTransformer:
    return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


class SemanticIndex:
    """Build, persist, and query a FAISS index over Project Mappings."""

    def __init__(self, project_path: str) -> None:
        self._project_path = Path(project_path).expanduser().resolve()
        self._index_dir = self._project_path / ".normflow" / "faiss_index"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        return (self._index_dir / "index.faiss").exists()

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

    # ------------------------------------------------------------------
    # Internal: persistence
    # ------------------------------------------------------------------

    def load(self) -> tuple[faiss.Index, list[tuple[str, str]]] | None:
        if not self.exists():
            return None
        index = faiss.read_index(str(self._index_dir / "index.faiss"))
        with open(self._index_dir / "mapping_table.pkl", "rb") as f:
            mapping_table = pickle.load(f)  # noqa: S301
        return index, mapping_table

    def _save(self, index: faiss.Index, mapping_table: list[tuple[str, str]]) -> None:
        self._index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self._index_dir / "index.faiss"))
        with open(self._index_dir / "mapping_table.pkl", "wb") as f:
            pickle.dump(mapping_table, f)
