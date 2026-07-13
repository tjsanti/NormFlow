"""Tests for LLM matching fallback in lookup."""

import json
import tempfile
from contextlib import chdir
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from tests.helpers import seed_mappings
from normflow.cli import app
from normflow.mapping_service import MappingService
from normflow.semantic_index import SemanticIndex
from normflow.workspace import init_workspace as _init_project

_active_project: Path | None = None


def init_workspace(path: str | Path) -> Path:
    global _active_project
    _active_project = _init_project(path)
    return _active_project


class ProjectCliRunner(CliRunner):
    def invoke(self, cli, args=None, **kwargs):
        if (
            args
            and args[0] in {"suggest", "index"}
            and _active_project is not None
            and _active_project.is_dir()
        ):
            with chdir(_active_project):
                return super().invoke(cli, args, **kwargs)
        return super().invoke(cli, args, **kwargs)


runner = ProjectCliRunner()


def _make_mock_encoder(low_sim: bool = False):
    """Build a mock SentenceTransformer that returns controlled embeddings.

    If low_sim is True, the query vector is orthogonal to indexed vectors
    (cosine ~0). If False, everything is identical (cosine = 1.0).
    """
    mock_model = MagicMock()
    call_count = [0]

    def encode_side_effect(texts, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: building index — return distinct unit vectors per text
            return [[1.0, 0.0, 0.0] for _ in texts]
        else:
            # Second call: query
            if low_sim:
                # Orthogonal to [1,0,0] → cosine = 0
                return [[0.0, 1.0, 0.0] for _ in texts]
            else:
                # Same direction → cosine = 1.0
                return [[1.0, 0.0, 0.0] for _ in texts]

    mock_model.encode.side_effect = encode_side_effect
    mock_model.get_sentence_embedding_dimension.return_value = 3
    return mock_model


class TestSuggestLLMFallback:
    """LLM step fires when semantic search is below threshold."""

    @patch("normflow.llm_matcher.build_client")
    def test_llm_fires_when_semantic_below_threshold(self, mock_build_client):
        with patch("normflow.semantic_index._ensure_model") as mock_ensure:
            mock_ensure.return_value = _make_mock_encoder(low_sim=True)

            # LLM mock
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="realized"))]
            )
            mock_build_client.return_value = mock_client

            with tempfile.TemporaryDirectory() as tmpdir:
                ws_path = Path(tmpdir) / "proj"
                from normflow.workspace import init_workspace
                init_workspace(str(ws_path))
                seed_mappings(ws_path, [
                    ("colour", "color"),
                    ("centre", "center"),
                    ("organised", "organized"),
                ])

                idx = SemanticIndex(str(ws_path))
                idx.build([
                    ("colour", "color"),
                    ("centre", "center"),
                    ("organised", "organized"),
                ])

                suggestions = MappingService(str(ws_path)).lookup(
                    "orgnisd",
                    semantic=True,
                    threshold=0.85,
                    llm=True,
                )

                assert len(suggestions) == 1
                assert suggestions[0].method == "llm"
                assert suggestions[0].suggested_text == "realized"

    def test_no_llm_flag_skips_llm_step(self):
        with patch("normflow.semantic_index._ensure_model") as mock_ensure:
            mock_ensure.return_value = _make_mock_encoder(low_sim=True)

            with tempfile.TemporaryDirectory() as tmpdir:
                ws_path = Path(tmpdir) / "proj"
                from normflow.workspace import init_workspace
                init_workspace(str(ws_path))
                seed_mappings(ws_path, [("colour", "color")])

                idx = SemanticIndex(str(ws_path))
                idx.build([("colour", "color")])

                suggestions = MappingService(str(ws_path)).lookup(
                    "colr", semantic=True, threshold=0.85, llm=False,
                )

                assert suggestions == []

    @patch("normflow.llm_matcher.build_client")
    def test_semantic_above_threshold_skips_llm(self, mock_build_client):
        with patch("normflow.semantic_index._ensure_model") as mock_ensure:
            mock_ensure.return_value = _make_mock_encoder(low_sim=False)

            mock_client = MagicMock()
            mock_build_client.return_value = mock_client

            with tempfile.TemporaryDirectory() as tmpdir:
                ws_path = Path(tmpdir) / "proj"
                from normflow.workspace import init_workspace
                init_workspace(str(ws_path))
                seed_mappings(ws_path, [("colour", "color")])

                idx = SemanticIndex(str(ws_path))
                idx.build([("colour", "color")])

                suggestions = MappingService(str(ws_path)).lookup(
                    "colur", semantic=True, threshold=0.5, llm=True,
                )

                assert len(suggestions) == 1
                assert suggestions[0].method == "semantic"
                mock_client.chat.completions.create.assert_not_called()

    @patch("normflow.llm_matcher.build_client")
    def test_llm_error_falls_through_gracefully(self, mock_build_client):
        with patch("normflow.semantic_index._ensure_model") as mock_ensure:
            mock_ensure.return_value = _make_mock_encoder(low_sim=True)

            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = Exception("API error")
            mock_build_client.return_value = mock_client

            with tempfile.TemporaryDirectory() as tmpdir:
                ws_path = Path(tmpdir) / "proj"
                from normflow.workspace import init_workspace
                init_workspace(str(ws_path))
                seed_mappings(ws_path, [("colour", "color")])

                idx = SemanticIndex(str(ws_path))
                idx.build([("colour", "color")])

                suggestions = MappingService(str(ws_path)).lookup(
                    "colr", semantic=True, threshold=0.85, llm=True,
                )

                assert suggestions == []

    def test_cli_no_llm_flag(self):
        """CLI --no-llm flag prevents LLM fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "proj"
            init_workspace(str(ws_path))
            seed_mappings(ws_path, [("colour", "color")])

            result = runner.invoke(
                app,
                ["suggest", "colr", "--no-semantic", "--no-llm"],
            )
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert data["suggestions"] == []
