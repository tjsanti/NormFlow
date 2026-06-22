"""Semantic search index for NormFlow workspaces.

Embeds ExampleMapping raw_text values with all-MiniLM-L6-v2 and stores
them in a FAISS IndexFlatIP (cosine similarity via normalized vectors).
"""

import pickle
import shutil
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from .workspace import WorkspaceService

# ponytail: 90MB model — module-level singleton, not per-instance
_MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


class SemanticIndex:
    """Build, persist, and query a FAISS index over workspace mappings."""

    def __init__(self, workspace_path: str):
        self._workspace_path = Path(workspace_path).expanduser().resolve()
        self._index_dir = self._workspace_path / ".normflow" / "faiss_index"

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        """Return True if a persisted index is present on disk."""
        return (self._index_dir / "index.faiss").exists()

    def _save(self, index, mapping_table):
        """Persist index + mapping table to disk."""
        self._index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self._index_dir / "index.faiss"))
        with open(self._index_dir / "mapping_table.pkl", "wb") as f:
            pickle.dump(mapping_table, f)

    def _load(self):
        """Load persisted index + mapping table. Returns (index, table) or None."""
        if not self.exists():
            return None
        index = faiss.read_index(str(self._index_dir / "index.faiss"))
        with open(self._index_dir / "mapping_table.pkl", "rb") as f:
            mapping_table = pickle.load(f)  # noqa: S301
        return index, mapping_table

    def clear(self):
        """Remove the persisted index files."""
        if self._index_dir.exists():
            shutil.rmtree(self._index_dir)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> int:
        """Build the index from all ExampleMapping rows in the workspace.

        Returns the number of entries indexed.
        """
        ws = WorkspaceService(str(self._workspace_path))

        with ws.session() as session:
            from sqlmodel import select
            from .models import ExampleMapping

            mappings = session.exec(select(ExampleMapping)).all()

        # Collect non-blank raw_texts, deduplicating by first occurrence
        seen = set()
        raw_texts: list[str] = []
        table: list[tuple[str, str]] = []  # (raw_text, normalized_text)

        for m in mappings:
            rt = m.raw_text.strip()
            if not rt or rt in seen:
                continue
            seen.add(rt)
            raw_texts.append(rt)
            table.append((rt, m.normalized_text))

        if not raw_texts:
            # Nothing to index — create empty index
            dim = _MODEL.get_sentence_embedding_dimension()
            index = faiss.IndexFlatIP(dim)
            self._save(index, table)
            return 0

        # Embed, normalize, and store
        embeddings = _MODEL.encode(raw_texts, normalize_embeddings=True)
        embeddings = np.asarray(embeddings, dtype="float32")

        dim = embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings)

        self._save(index, table)
        return len(table)

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self):
        """Load the persisted index. Returns (index, table) or None."""
        return self._load()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query_text: str, limit: int = 1, threshold: float = 0.85):
        """Search for semantically similar mappings.

        Returns a list of dicts with keys: raw_text, normalized_text, score.
        Only results at or above *threshold* are returned.
        """
        loaded = self._load()
        if loaded is None:
            return []

        index, table = loaded

        query_embedding = _MODEL.encode(
            [query_text], normalize_embeddings=True
        )
        query_vec = np.asarray(query_embedding, dtype="float32")

        scores, faiss_ids = index.search(query_vec, min(limit, index.ntotal))

        results = []
        for score, fid in zip(scores[0], faiss_ids[0]):
            if fid < 0:
                continue  # FAISS returns -1 for missing entries
            if float(score) < threshold:
                continue
            raw_text, normalized_text = table[fid]
            results.append({
                "raw_text": raw_text,
                "normalized_text": normalized_text,
                "score": round(float(score), 4),
            })

        return results
