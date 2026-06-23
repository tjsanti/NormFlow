"""Tests for semantic matching integration in suggest_service and CLI."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from tests.helpers import seed_mappings
from normflow.cli import app
from normflow.mapping_service import ExampleMapping, MappingService
from normflow.semantic_index import SemanticIndex
from normflow.workspace import init_workspace

runner = CliRunner()
_INDEX_PATCH = "normflow.semantic_index._ensure_model"


# ---------------------------------------------------------------------------
# suggest_service integration tests
# ---------------------------------------------------------------------------


class TestSuggestSemanticFallback:
    """suggest_exact falls through to semantic when exact match fails."""

    @patch(_INDEX_PATCH)
    def test_semantic_fallback_when_no_exact_match(self, mock_ensure):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.577, 0.577, 0.577] for _ in texts
        ]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            init_workspace(str(ws_path))
            seed_mappings(ws_path, [("colour", "color")])

            idx = SemanticIndex(str(ws_path))
            idx.build([("colour", "color")])

            suggestions = MappingService(str(ws_path)).lookup(
                "colr", semantic=True, threshold=0.5,
            )

            assert len(suggestions) > 0
            assert suggestions[0].method == "semantic"
            assert suggestions[0].confidence < 1.0

    @patch(_INDEX_PATCH)
    def test_exact_match_takes_priority(self, mock_ensure):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.577, 0.577, 0.577] for _ in texts
        ]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            init_workspace(str(ws_path))
            seed_mappings(ws_path, [("colour", "color")])

            idx = SemanticIndex(str(ws_path))
            idx.build([("colour", "color")])

            suggestions = MappingService(str(ws_path)).lookup("colour", semantic=True)

            assert len(suggestions) == 1
            assert suggestions[0].method == "exact"
            assert suggestions[0].confidence == 1.0

    def test_no_semantic_flag_returns_empty_on_miss(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            init_workspace(str(ws_path))
            seed_mappings(ws_path, [("colour", "color")])

            suggestions = MappingService(str(ws_path)).lookup("colr", semantic=False)

            assert suggestions == []

    def test_no_index_returns_empty_on_miss(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            init_workspace(str(ws_path))
            seed_mappings(ws_path, [("colour", "color")])

            # Don't build index -- semantic should degrade gracefully
            suggestions = MappingService(str(ws_path)).lookup("colr", semantic=True)

            assert suggestions == []

    @patch(_INDEX_PATCH)
    def test_threshold_filters_results(self, mock_ensure):
        mock_model = MagicMock()
        call_count = [0]
        def encode_side_effect(texts, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return [[0.0, 1.0, 0.0] for _ in texts]
            else:
                return [[1.0, 0.0, 0.0] for _ in texts]
        mock_model.encode.side_effect = encode_side_effect
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            init_workspace(str(ws_path))
            seed_mappings(ws_path, [("colour", "color"), ("centre", "center")])

            idx = SemanticIndex(str(ws_path))
            idx.build([("colour", "color"), ("centre", "center")])

            suggestions = MappingService(str(ws_path)).lookup(
                "colr", semantic=True, threshold=0.85,
            )

            assert suggestions == []


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestSuggestCLI:
    """CLI suggest command with semantic flags."""

    @patch(_INDEX_PATCH)
    def test_suggest_returns_semantic_suggestion(self, mock_ensure):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.577, 0.577, 0.577] for _ in texts
        ]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            runner.invoke(app, ["init", "--workspace", str(ws_path)])
            seed_mappings(ws_path, [("colour", "color")])

            result = runner.invoke(app, ["index", "build", "--workspace", str(ws_path)])
            assert result.exit_code == 0

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
            seed_mappings(ws_path, [("colour", "color")])

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
            seed_mappings(ws_path, [("colour", "color")])

            result = runner.invoke(
                app,
                ["suggest", "--workspace", str(ws_path), "colour"],
            )
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert len(data["suggestions"]) == 1


class TestIndexCLI:
    """CLI index build/clear commands."""

    @patch(_INDEX_PATCH)
    def test_index_build_succeeds(self, mock_ensure):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            runner.invoke(app, ["init", "--workspace", str(ws_path)])
            seed_mappings(ws_path, [("colour", "color"), ("centre", "center")])

            result = runner.invoke(app, ["index", "build", "--workspace", str(ws_path)])
            assert result.exit_code == 0
            assert "2" in result.stdout

    @patch(_INDEX_PATCH)
    def test_index_clear_succeeds(self, mock_ensure):
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [0.1 * i, 0.2 * i, 0.3 * i] for i in range(len(texts))
        ]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            runner.invoke(app, ["init", "--workspace", str(ws_path)])
            seed_mappings(ws_path, [("colour", "color")])

            runner.invoke(app, ["index", "build", "--workspace", str(ws_path)])
            result = runner.invoke(app, ["index", "clear", "--workspace", str(ws_path)])
            assert result.exit_code == 0

    def test_index_build_invalid_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(app, ["index", "build", "--workspace", tmpdir])
            assert result.exit_code != 0
