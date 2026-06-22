"""Tests for semantic matching integration in suggest_service and CLI."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from normflow.cli import app
from normflow.models import ExampleMapping
from normflow.workspace import WorkspaceService, init_workspace

runner = CliRunner()


def _seed_mappings(ws_path: Path, pairs: list[tuple[str, str]]) -> None:
    """Insert ExampleMapping rows into the workspace."""
    ws = WorkspaceService(str(ws_path))
    with ws.session() as session:
        for raw, norm in pairs:
            session.add(ExampleMapping(raw_text=raw, normalized_text=norm))
        session.commit()


# ---------------------------------------------------------------------------
# suggest_service integration tests
# ---------------------------------------------------------------------------


class TestSuggestSemanticFallback:
    """suggest_exact falls through to semantic when exact match fails."""

    @patch("normflow.semantic_index.SentenceTransformer")
    def test_semantic_fallback_when_no_exact_match(self, mock_model_cls):
        from normflow.suggest_service import suggest

        mock_model = MagicMock()
        call_count = [0]
        def encode_side_effect(texts, **kw):
            call_count[0] += 1
            return [[0.577, 0.577, 0.577] for _ in texts]
        mock_model.encode.side_effect = encode_side_effect
        mock_model_cls.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            init_workspace(str(ws_path))
            _seed_mappings(ws_path, [("colour", "color")])

            # Build the index
            from normflow.semantic_index import SemanticIndex
            idx = SemanticIndex(str(ws_path))
            idx.build()

            # Query something that has no exact match
            result = suggest(str(ws_path), "colr", semantic=True, semantic_threshold=0.5)

            assert len(result.suggestions) > 0
            assert result.suggestions[0].method == "semantic"
            assert result.suggestions[0].confidence < 1.0

    @patch("normflow.semantic_index.SentenceTransformer")
    def test_exact_match_takes_priority(self, mock_model_cls):
        from normflow.suggest_service import suggest

        mock_model = MagicMock()
        def encode_side_effect(texts, **kw):
            return [[0.577, 0.577, 0.577] for _ in texts]
        mock_model.encode.side_effect = encode_side_effect
        mock_model_cls.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            init_workspace(str(ws_path))
            _seed_mappings(ws_path, [("colour", "color")])

            # Build index
            from normflow.semantic_index import SemanticIndex
            idx = SemanticIndex(str(ws_path))
            idx.build()

            # Exact match query
            result = suggest(str(ws_path), "colour", semantic=True)

            assert len(result.suggestions) == 1
            assert result.suggestions[0].method == "exact"
            assert result.suggestions[0].confidence == 1.0

    def test_no_semantic_flag_returns_empty_on_miss(self):
        from normflow.suggest_service import suggest

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            init_workspace(str(ws_path))
            _seed_mappings(ws_path, [("colour", "color")])

            result = suggest(str(ws_path), "colr", semantic=False)

            assert result.suggestions == []

    def test_no_index_returns_empty_on_miss(self):
        from normflow.suggest_service import suggest

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            init_workspace(str(ws_path))
            _seed_mappings(ws_path, [("colour", "color")])

            # Don't build index — semantic should degrade gracefully
            result = suggest(str(ws_path), "colr", semantic=True)

            assert result.suggestions == []

    @patch("normflow.semantic_index.SentenceTransformer")
    def test_threshold_filters_results(self, mock_model_cls):
        from normflow.suggest_service import suggest

        mock_model = MagicMock()
        # Build: returns [0,1,0] for each mapping; Search: returns [1,0,0] for query
        # Cosine between [1,0,0] and [0,1,0] = 0 (orthogonal)
        call_count = [0]
        def encode_side_effect(texts, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return [[0.0, 1.0, 0.0] for _ in texts]
            else:
                return [[1.0, 0.0, 0.0] for _ in texts]
        mock_model.encode.side_effect = encode_side_effect
        mock_model_cls.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            init_workspace(str(ws_path))
            _seed_mappings(ws_path, [("colour", "color"), ("centre", "center")])

            from normflow.semantic_index import SemanticIndex
            idx = SemanticIndex(str(ws_path))
            idx.build()

            result = suggest(str(ws_path), "colr", semantic=True, semantic_threshold=0.85)

            # Cosine = 0, below threshold, so no results
            assert result.suggestions == []


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestSuggestCLI:
    """CLI suggest command with semantic flags."""

    @patch("normflow.semantic_index.SentenceTransformer")
    def test_suggest_returns_semantic_suggestion(self, mock_model_cls):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.577, 0.577, 0.577] for _ in texts
        ]
        mock_model_cls.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            runner.invoke(app, ["init", "--workspace", str(ws_path)])
            _seed_mappings(ws_path, [("colour", "color")])

            # Build index
            result = runner.invoke(app, ["index", "build", "--workspace", str(ws_path)])
            assert result.exit_code == 0

            # Query with no exact match
            result = runner.invoke(
                app,
                ["suggest", "--workspace", str(ws_path), "colr", "--semantic-threshold", "0.5"],
            )
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert len(data["suggestions"]) > 0
            assert data["suggestions"][0]["method"] == "semantic"

    def test_no_semantic_flag_disables_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            runner.invoke(app, ["init", "--workspace", str(ws_path)])
            _seed_mappings(ws_path, [("colour", "color")])

            result = runner.invoke(
                app,
                ["suggest", "--workspace", str(ws_path), "colr", "--no-semantic"],
            )
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert data["suggestions"] == []

    def test_default_limit_is_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            runner.invoke(app, ["init", "--workspace", str(ws_path)])
            _seed_mappings(ws_path, [("colour", "color")])

            result = runner.invoke(
                app,
                ["suggest", "--workspace", str(ws_path), "colour"],
            )
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert len(data["suggestions"]) == 1


class TestIndexCLI:
    """CLI index build/clear commands."""

    @patch("normflow.semantic_index.SentenceTransformer")
    def test_index_build_succeeds(self, mock_model_cls):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_model_cls.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            runner.invoke(app, ["init", "--workspace", str(ws_path)])
            _seed_mappings(ws_path, [("colour", "color"), ("centre", "center")])

            result = runner.invoke(app, ["index", "build", "--workspace", str(ws_path)])
            assert result.exit_code == 0
            assert "2" in result.stdout  # shows count

    @patch("normflow.semantic_index.SentenceTransformer")
    def test_index_clear_succeeds(self, mock_model_cls):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_model_cls.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            runner.invoke(app, ["init", "--workspace", str(ws_path)])
            _seed_mappings(ws_path, [("colour", "color")])

            # Build then clear
            runner.invoke(app, ["index", "build", "--workspace", str(ws_path)])
            result = runner.invoke(app, ["index", "clear", "--workspace", str(ws_path)])
            assert result.exit_code == 0
            assert "cleared" in result.stdout.lower() or result.exit_code == 0

    def test_index_build_invalid_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(app, ["index", "build", "--workspace", tmpdir])
            assert result.exit_code != 0
