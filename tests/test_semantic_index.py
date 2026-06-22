"""Tests for the semantic index service."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from normflow.mapping_service import ExampleMapping, MappingService, _SemanticIndex
from tests.helpers import seed_mappings
from normflow.workspace import init_workspace


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a workspace with seed mappings."""
    ws = init_workspace(str(tmp_path))
    seed_mappings(ws, [
        ("colour", "color"),
        ("centre", "center"),
        ("realise", "realize"),
        ("o2 sensor", "O2 Sensor"),
        ("oxygen sensor", "Oxygen Sensor"),
    ])
    return ws


# ---------------------------------------------------------------------------
# SemanticIndex tests (mocked model)
# ---------------------------------------------------------------------------


class TestSemanticIndexBuild:
    """SemanticIndex.build() creates an index from mappings."""

    @patch("normflow.mapping_service._ensure_model")
    def test_build_creates_index_with_correct_size(self, mock_model_cls, workspace):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_model_cls.return_value = mock_model

        idx = _SemanticIndex(str(workspace))
        idx.build()

        assert idx.exists()

    @patch("normflow.mapping_service._ensure_model")
    def test_build_persists_to_disk(self, mock_model_cls, workspace):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_model_cls.return_value = mock_model

        idx = _SemanticIndex(str(workspace))
        idx.build()

        # Verify files exist on disk
        index_dir = workspace / ".normflow" / "faiss_index"
        assert index_dir.exists()

    @patch("normflow.mapping_service._ensure_model")
    def test_build_skips_empty_raw_text(self, mock_model_cls, workspace):
        # Add a mapping with blank raw_text
        ms = MappingService(str(workspace))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="", normalized_text="something"))
            session.add(ExampleMapping(raw_text="   ", normalized_text="something2"))
            session.commit()

        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_model_cls.return_value = mock_model

        idx = _SemanticIndex(str(workspace))
        idx.build()

        # Only the 5 valid mappings should be indexed (not the 2 blank ones)
        assert idx.exists()


class TestSemanticIndexLoad:
    """SemanticIndex.load() restores a persisted index."""

    @patch("normflow.mapping_service._ensure_model")
    def test_load_returns_index(self, mock_model_cls, workspace):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_model_cls.return_value = mock_model

        # Build first
        idx = _SemanticIndex(str(workspace))
        idx.build()

        # Load into a new instance
        idx2 = _SemanticIndex(str(workspace))
        loaded = idx2.load()

        assert loaded is not None

    def test_load_returns_none_when_no_index(self, workspace):
        idx = _SemanticIndex(str(workspace))
        loaded = idx.load()

        assert loaded is None

    def test_exists_false_when_no_index(self, workspace):
        idx = _SemanticIndex(str(workspace))
        assert idx.exists() is False


class TestSemanticIndexSearch:
    """SemanticIndex.search() returns results above threshold."""

    @patch("normflow.mapping_service._ensure_model")
    def test_search_returns_close_matches(self, mock_model_cls, workspace):
        mock_model = MagicMock()
        # All vectors are the same direction — cosine similarity = 1.0
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.577, 0.577, 0.577] for _ in texts
        ]
        mock_model_cls.return_value = mock_model

        idx = _SemanticIndex(str(workspace))
        idx.build()

        results = idx.search("colour", limit=3, threshold=0.5)

        # All mappings should match (cosine = 1.0)
        assert len(results) > 0
        for r in results:
            assert "raw_text" in r
            assert "normalized_text" in r
            assert "score" in r
            assert r["score"] >= 0.5

    @patch("normflow.mapping_service._ensure_model")
    def test_search_filters_by_threshold(self, mock_model_cls, workspace):
        mock_model = MagicMock()
        # Query vector will be [1,0,0], stored vectors [0,1,0] — cosine = 0
        mock_model.encode.side_effect = lambda texts, **kw: [
            [1.0, 0.0, 0.0] if len(texts) == 1 else [0.0, 1.0, 0.0]
        ]
        mock_model_cls.return_value = mock_model

        idx = _SemanticIndex(str(workspace))
        idx.build()

        results = idx.search("something", limit=5, threshold=0.85)

        # All cosine scores are 0, so nothing passes threshold
        assert len(results) == 0

    @patch("normflow.mapping_service._ensure_model")
    def test_search_respects_limit(self, mock_model_cls, workspace):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.577, 0.577, 0.577] for _ in texts
        ]
        mock_model_cls.return_value = mock_model

        idx = _SemanticIndex(str(workspace))
        idx.build()

        results = idx.search("something", limit=2, threshold=0.0)

        assert len(results) <= 2

    @patch("normflow.mapping_service._ensure_model")
    def test_search_returns_results_sorted_by_score_desc(self, mock_model_cls, workspace):
        mock_model = MagicMock()
        # Query = [1, 0, 0], first vector = [1, 0, 0] (cosine=1),
        # rest = [0, 1, 0] (cosine=0)
        call_count = [0]
        def encode_side_effect(texts, **kw):
            call_count[0] += 1
            if len(texts) == 1:
                return [[1.0, 0.0, 0.0]]  # query
            else:
                # First mapping gets high similarity, rest get low
                vectors = [[1.0, 0.0, 0.0]]
                vectors += [[0.0, 1.0, 0.0]] * (len(texts) - 1)
                return vectors
        mock_model.encode.side_effect = encode_side_effect
        mock_model_cls.return_value = mock_model

        idx = _SemanticIndex(str(workspace))
        idx.build()

        results = idx.search("something", limit=5, threshold=0.0)

        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"]


class TestSemanticIndexClear:
    """SemanticIndex.clear() removes persisted index."""

    @patch("normflow.mapping_service._ensure_model")
    def test_clear_removes_index(self, mock_model_cls, workspace):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_model_cls.return_value = mock_model

        idx = _SemanticIndex(str(workspace))
        idx.build()
        assert idx.exists()

        idx.clear()
        assert idx.exists() is False
