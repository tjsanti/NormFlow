"""Tests for the semantic index service."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from normflow.embedding_model import EMBEDDING_MODEL_IDENTITY
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
        generation = (index_dir / "current").read_text(encoding="utf-8").strip()
        assert (index_dir / "generations" / generation / "embeddings.npy").exists()

    @patch(_INDEX_PATCH)
    def test_build_persists_the_embedding_model_identity(self, mock_ensure, project):
        model = MagicMock()
        model.encode.return_value = [[1.0, 0.0, 0.0] for _ in SEED_PAIRS]
        mock_ensure.return_value = model

        SemanticIndex(str(project)).build(SEED_PAIRS)

        index_dir = project / ".normflow" / "faiss_index"
        generation = (index_dir / "current").read_text(encoding="utf-8").strip()
        active_dir = index_dir / "generations" / generation
        assert (active_dir / "model_identity").read_text(encoding="utf-8") == (
            f"{EMBEDDING_MODEL_IDENTITY}\n"
        )

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

    @pytest.mark.parametrize(
        ("embeddings", "message"),
        [
            ([1.0, 0.0, 0.0], "embeddings"),
            ([[1.0, 0.0, 0.0] for _ in SEED_PAIRS[:-1]], "embedding count"),
        ],
    )
    @patch(_INDEX_PATCH)
    def test_build_rejects_malformed_embeddings_before_publication(
        self, mock_ensure, project, embeddings, message
    ):
        model = MagicMock()
        model.encode.return_value = embeddings
        mock_ensure.return_value = model
        index = SemanticIndex(str(project))

        with pytest.raises(ValueError, match=message):
            index.build(SEED_PAIRS)

        assert not index.exists()
        assert not (project / ".normflow" / "faiss_index").exists()

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

    @patch(_INDEX_PATCH)
    def test_load_rejects_an_embedding_count_that_does_not_match_the_table(
        self, mock_ensure, project
    ):
        model = MagicMock()
        model.encode.return_value = [[1.0, 0.0, 0.0] for _ in SEED_PAIRS]
        mock_ensure.return_value = model
        index = SemanticIndex(str(project))
        index.build(SEED_PAIRS)
        index_dir = project / ".normflow" / "faiss_index"
        generation = (index_dir / "current").read_text(encoding="utf-8").strip()
        np.save(index_dir / "generations" / generation / "embeddings.npy", np.zeros((1, 3)))

        with pytest.raises(ValueError, match="embedding count"):
            index.load()

    @patch(_INDEX_PATCH)
    def test_search_rejects_complex_persisted_embeddings_before_encoding_query(
        self, mock_ensure, project
    ):
        model = MagicMock()
        model.encode.return_value = [[1.0, 0.0, 0.0] for _ in SEED_PAIRS]
        mock_ensure.return_value = model
        index = SemanticIndex(str(project))
        index.build(SEED_PAIRS)
        index_dir = project / ".normflow" / "faiss_index"
        generation = (index_dir / "current").read_text(encoding="utf-8").strip()
        np.save(
            index_dir / "generations" / generation / "embeddings.npy",
            np.ones((len(SEED_PAIRS), 3), dtype="complex64"),
        )
        model.reset_mock()

        with pytest.raises(ValueError, match="Invalid semantic index embeddings"):
            index.search("query")

        model.encode.assert_not_called()


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


class TestSemanticIndexIdentity:
    """Only generations built by the current embedding model are verified."""

    @patch(_INDEX_PATCH)
    def test_legacy_faiss_generation_with_fresh_markers_is_unverified(
        self, mock_ensure, project
    ):
        model = MagicMock()
        model.encode.return_value = [[1.0, 0.0, 0.0] for _ in SEED_PAIRS]
        mock_ensure.return_value = model
        index = SemanticIndex(str(project))
        index.build(SEED_PAIRS, mapping_revision=7)
        index_dir = project / ".normflow" / "faiss_index"
        generation = (index_dir / "current").read_text(encoding="utf-8").strip()
        active_dir = index_dir / "generations" / generation
        (active_dir / "embeddings.npy").rename(active_dir / "index.faiss")

        assert index.status(current_mapping_revision=7) == "unverified"

    @pytest.mark.parametrize("persisted_identity", [None, "different-model@revision"])
    @patch(_INDEX_PATCH)
    def test_missing_or_different_model_identity_is_unverified(
        self, mock_ensure, project, persisted_identity
    ):
        model = MagicMock()
        model.encode.return_value = [[1.0, 0.0, 0.0] for _ in SEED_PAIRS]
        mock_ensure.return_value = model
        index = SemanticIndex(str(project))
        index.build(SEED_PAIRS)
        index_dir = project / ".normflow" / "faiss_index"
        generation = (index_dir / "current").read_text(encoding="utf-8").strip()
        identity_path = index_dir / "generations" / generation / "model_identity"
        if persisted_identity is None:
            identity_path.unlink()
        else:
            identity_path.write_text(f"{persisted_identity}\n", encoding="utf-8")

        assert index.status() == "unverified"


class TestSemanticIndexRecovery:
    """A failed restore never removes the active semantic generation."""

    @patch(_INDEX_PATCH)
    def test_failed_restore_keeps_the_active_generation(self, mock_ensure, project):
        model = MagicMock()
        model.encode.return_value = [[1.0, 0.0, 0.0] for _ in SEED_PAIRS]
        model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = model
        index = SemanticIndex(str(project))
        index.build(SEED_PAIRS)
        snapshot = project / "snapshot"
        index.snapshot(snapshot)

        with (
            patch(
                "normflow.semantic_index.shutil.copytree",
                side_effect=OSError("restore interrupted"),
            ),
            pytest.raises(OSError, match="restore interrupted"),
        ):
            index.restore(snapshot)

        assert index.exists()
        assert index.load()[1] == SEED_PAIRS

    @patch(_INDEX_PATCH)
    def test_restore_keeps_generation_for_concurrent_reader(self, mock_ensure, project):
        model = MagicMock()
        model.encode.return_value = [[1.0, 0.0, 0.0] for _ in SEED_PAIRS]
        mock_ensure.return_value = model
        index = SemanticIndex(str(project))
        index.build(SEED_PAIRS)
        snapshot = project / "snapshot"
        index.snapshot(snapshot)
        reader_started = Event()
        continue_reader = Event()
        read_embeddings = np.load

        def delayed_read_embeddings(path, *args, **kwargs):
            reader_started.set()
            assert continue_reader.wait(timeout=5)
            return read_embeddings(path, *args, **kwargs)

        with (
            patch("normflow.semantic_index.np.load", side_effect=delayed_read_embeddings),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            loaded = executor.submit(index.load)
            assert reader_started.wait(timeout=5)
            index.restore(snapshot)
            continue_reader.set()

            assert loaded.result(timeout=5)[1] == SEED_PAIRS


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
        mock.encode.side_effect = lambda texts, **kw: (
            [[1.0, 0.0, 0.0] for _ in texts]
            if len(texts) == 1
            else [[0.0, 1.0, 0.0] for _ in texts]
        )
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
