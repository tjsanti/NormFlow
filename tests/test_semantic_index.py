"""Tests for the semantic index service."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from normflow.mapping_service import MappingService
from normflow.semantic_index import SemanticIndex
from tests.helpers import seed_mappings
from normflow.project_service import init_project

SEED_PAIRS = [
    ("colour", "color"),
    ("centre", "center"),
    ("realise", "realize"),
    ("o2 sensor", "O2 Sensor"),
    ("oxygen sensor", "Oxygen Sensor"),
]

_INDEX_PATCH = "normflow.semantic_index._ensure_model"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Create a project with seed mappings."""
    project = init_project(str(tmp_path))
    seed_mappings(project, SEED_PAIRS)
    return project


# ---------------------------------------------------------------------------
# SemanticIndex tests (mocked model)
# ---------------------------------------------------------------------------


class TestSemanticIndexBuild:
    """SemanticIndex.build() creates an index from mapping pairs."""

    @patch(_INDEX_PATCH)
    def test_build_creates_index(self, mock_ensure, project):
        mock = MagicMock()
        mock.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_ensure.return_value = mock

        idx = SemanticIndex(str(project))
        idx.build(SEED_PAIRS)

        assert idx.exists()

    @patch(_INDEX_PATCH)
    def test_build_persists_to_disk(self, mock_ensure, project):
        mock = MagicMock()
        mock.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_ensure.return_value = mock

        idx = SemanticIndex(str(project))
        idx.build(SEED_PAIRS)

        index_dir = project / ".normflow" / "faiss_index"
        assert index_dir.exists()

    @patch(_INDEX_PATCH)
    def test_build_skips_empty_raw_text(self, mock_ensure, project):
        mock = MagicMock()
        mock.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_ensure.return_value = mock

        pairs = SEED_PAIRS + [
            ("", "something"),
            ("   ", "something2"),
        ]

        idx = SemanticIndex(str(project))
        count = idx.build(pairs)

        # Only 5 valid mappings indexed (not the 2 blank ones)
        assert count == 5

    @patch(_INDEX_PATCH)
    def test_rebuild_keeps_only_current_and_previous_generation(self, mock_ensure, project):
        mock = MagicMock()
        mock.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_ensure.return_value = mock
        idx = SemanticIndex(str(project))

        idx.build(SEED_PAIRS)
        idx.build(SEED_PAIRS)
        idx.build(SEED_PAIRS)

        generations = project / ".normflow" / "faiss_index" / "generations"
        assert len([path for path in generations.iterdir() if path.is_dir()]) == 2


class TestSemanticIndexLoad:
    """SemanticIndex.load() restores a persisted index."""

    @patch(_INDEX_PATCH)
    def test_load_returns_index(self, mock_ensure, project):
        mock = MagicMock()
        mock.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_ensure.return_value = mock

        idx = SemanticIndex(str(project))
        idx.build(SEED_PAIRS)

        idx2 = SemanticIndex(str(project))
        loaded = idx2.load()

        assert loaded is not None

    def test_load_returns_none_when_no_index(self, project):
        idx = SemanticIndex(str(project))
        loaded = idx.load()

        assert loaded is None

    def test_exists_false_when_no_index(self, project):
        idx = SemanticIndex(str(project))
        assert idx.exists() is False

    @patch(_INDEX_PATCH)
    def test_mapping_table_round_trips_through_json(self, mock_ensure, project):
        model = MagicMock()
        model.encode.return_value = [[1.0, 0.0, 0.0] for _ in SEED_PAIRS]
        mock_ensure.return_value = model
        idx = SemanticIndex(str(project))

        idx.build(SEED_PAIRS)

        index_dir = project / ".normflow" / "faiss_index"
        generation = (index_dir / "current").read_text(encoding="utf-8").strip()
        active_dir = index_dir / "generations" / generation
        assert json.loads((active_dir / "mapping_table.json").read_text(encoding="utf-8")) == [
            list(pair) for pair in SEED_PAIRS
        ]
        assert not (active_dir / "mapping_table.pkl").exists()
        assert idx.load()[1] == SEED_PAIRS


class TestSemanticIndexMarkers:
    """Freshness markers publish atomically without sharing temporary files."""

    def test_marker_publications_use_distinct_temporary_files(self, project):
        index_file = project / ".normflow" / "faiss_index" / "index.faiss"
        index_file.parent.mkdir(parents=True)
        index_file.touch()
        idx = SemanticIndex(str(project))

        with patch("normflow.semantic_index.os.replace", wraps=os.replace) as replace:
            idx.mark_refresh_required()
            idx.mark_refresh_required()
            idx.mark_refresh_failed()
            idx.mark_refresh_failed()

        temporary_paths = [Path(call.args[0]) for call in replace.call_args_list]
        assert len(set(temporary_paths)) == 4
        assert all(not path.exists() for path in temporary_paths)

    @pytest.mark.parametrize("method_name", ["mark_refresh_required", "mark_refresh_failed"])
    def test_failed_marker_publication_removes_temporary_file(self, project, method_name):
        index_file = project / ".normflow" / "faiss_index" / "index.faiss"
        index_file.parent.mkdir(parents=True)
        index_file.touch()
        idx = SemanticIndex(str(project))

        with (
            patch("normflow.semantic_index.os.replace", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            getattr(idx, method_name)()

        marker_temporary_files = (project / ".normflow").glob(".semantic_index_*.tmp")
        assert list(marker_temporary_files) == []


class TestSemanticIndexSearch:
    """SemanticIndex.search() returns results above threshold."""

    @patch(_INDEX_PATCH)
    def test_search_empty_index_returns_no_results(self, mock_ensure, project):
        model = MagicMock()
        model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = model
        index = SemanticIndex(str(project))
        index.build([])

        results = index.search("anything")

        assert results == []
        model.encode.assert_not_called()

    @patch(_INDEX_PATCH)
    def test_search_returns_close_matches(self, mock_ensure, project):
        mock = MagicMock()
        # All vectors are the same direction -- cosine similarity = 1.0
        mock.encode.side_effect = lambda texts, **kw: [
            [0.577, 0.577, 0.577] for _ in texts
        ]
        mock_ensure.return_value = mock

        idx = SemanticIndex(str(project))
        idx.build(SEED_PAIRS)

        results = idx.search("colour", limit=3, threshold=0.5)

        assert len(results) > 0
        for r in results:
            assert "raw_text" in r
            assert "normalized_text" in r
            assert "score" in r
            assert r["score"] >= 0.5

    @patch(_INDEX_PATCH)
    def test_search_filters_by_threshold(self, mock_ensure, project):
        mock = MagicMock()
        # Query vector [1,0,0], stored vectors [0,1,0] -- cosine = 0
        mock.encode.side_effect = lambda texts, **kw: [
            [1.0, 0.0, 0.0] if len(texts) == 1 else [0.0, 1.0, 0.0]
        ]
        mock_ensure.return_value = mock

        idx = SemanticIndex(str(project))
        idx.build(SEED_PAIRS)

        results = idx.search("something", limit=5, threshold=0.85)

        assert len(results) == 0

    @patch(_INDEX_PATCH)
    def test_search_respects_limit(self, mock_ensure, project):
        mock = MagicMock()
        mock.encode.side_effect = lambda texts, **kw: [
            [0.577, 0.577, 0.577] for _ in texts
        ]
        mock_ensure.return_value = mock

        idx = SemanticIndex(str(project))
        idx.build(SEED_PAIRS)

        results = idx.search("something", limit=2, threshold=0.0)

        assert len(results) <= 2

    @patch(_INDEX_PATCH)
    def test_search_returns_results_sorted_by_score_desc(self, mock_ensure, project):
        mock = MagicMock()
        call_count = [0]
        def encode_side_effect(texts, **kw):
            call_count[0] += 1
            if len(texts) == 1:
                return [[1.0, 0.0, 0.0]]  # query
            else:
                vectors = [[1.0, 0.0, 0.0]]
                vectors += [[0.0, 1.0, 0.0]] * (len(texts) - 1)
                return vectors
        mock.encode.side_effect = encode_side_effect
        mock_ensure.return_value = mock

        idx = SemanticIndex(str(project))
        idx.build(SEED_PAIRS)

        results = idx.search("something", limit=5, threshold=0.0)

        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"]


class TestSemanticIndexClear:
    """SemanticIndex.clear() removes persisted index."""

    @patch(_INDEX_PATCH)
    def test_clear_removes_index(self, mock_ensure, project):
        mock = MagicMock()
        mock.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_ensure.return_value = mock

        idx = SemanticIndex(str(project))
        idx.build(SEED_PAIRS)
        assert idx.exists()

        idx.clear()
        assert idx.exists() is False
