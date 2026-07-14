"""Tests for semantic matching integration in suggest_service and CLI."""

import csv
import json
import os
import shutil
import tempfile
from contextlib import chdir
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from tests.helpers import seed_mappings
from normflow.cli import app
from normflow.mapping_service import MappingService
from normflow.semantic_index import SemanticIndex
from normflow.project_service import init_project as _init_project

_active_project: Path | None = None


def init_project(path: str | Path) -> Path:
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
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            seed_mappings(project_path, [("colour", "color")])

            idx = SemanticIndex(str(project_path))
            idx.build([("colour", "color")])

            suggestions = MappingService(str(project_path)).lookup(
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
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            seed_mappings(project_path, [("colour", "color")])

            idx = SemanticIndex(str(project_path))
            idx.build([("colour", "color")])

            suggestions = MappingService(str(project_path)).lookup("colour", semantic=True)

            assert len(suggestions) == 1
            assert suggestions[0].method == "exact"
            assert suggestions[0].confidence == 1.0

    def test_exact_only_lookup_returns_empty_on_miss(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            seed_mappings(project_path, [("colour", "color")])

            suggestions = MappingService(str(project_path)).lookup(
                "colr", semantic=False, llm=False,
            )

            assert suggestions == []

    @patch(_INDEX_PATCH)
    def test_missing_index_is_built_before_semantic_lookup(self, mock_ensure):
        model = MagicMock()
        model.encode.return_value = [[1.0, 0.0, 0.0]]
        model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = model

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            seed_mappings(project_path, [("colour", "color")])

            service = MappingService(str(project_path))
            suggestions = service.lookup(
                "colr", semantic=True, llm=False, threshold=0.5,
            )

            assert suggestions[0].suggested_text == "color"
            assert service.project_info()["semantic_index_status"] == "fresh"

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
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            seed_mappings(project_path, [("colour", "color"), ("centre", "center")])

            idx = SemanticIndex(str(project_path))
            idx.build([("colour", "color"), ("centre", "center")])

            suggestions = MappingService(str(project_path)).lookup(
                "colr", semantic=True, threshold=0.85, llm=False,
            )

            assert suggestions == []

    @patch(_INDEX_PATCH)
    def test_mapping_change_is_lazily_included_in_next_semantic_lookup(self, mock_ensure):
        """Semantic lookup refreshes a dirty index through the MappingService seam."""
        vectors = {
            "colour": [1.0, 0.0, 0.0],
            "centre": [0.0, 1.0, 0.0],
            "centr": [0.0, 1.0, 0.0],
        }
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [vectors[text] for text in texts]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            service = MappingService(str(project_path))
            seed_mappings(project_path, [("colour", "color")])
            service.build_index()

            csv_path = project_path / "new-mapping.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=["raw", "clean"])
                writer.writeheader()
                writer.writerow({"raw": "centre", "clean": "center"})

            service.import_mappings(str(csv_path), "raw", "clean")
            assert service.project_info()["semantic_index_status"] == "refresh_required"

            suggestions = service.lookup(
                "centr", semantic=True, llm=False, threshold=0.5,
            )

            assert suggestions[0].suggested_text == "center"
            assert service.project_info()["semantic_index_status"] == "fresh"

    @patch(_INDEX_PATCH)
    def test_failed_lazy_refresh_preserves_stale_fallback_and_warning(self, mock_ensure):
        """A refresh failure keeps the previous index usable and visibly stale."""
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [
            [1.0, 0.0, 0.0] for _ in texts
        ]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            service = MappingService(str(project_path))
            seed_mappings(project_path, [("colour", "color")])
            service.build_index()

            csv_path = project_path / "new-mapping.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=["raw", "clean"])
                writer.writeheader()
                writer.writerow({"raw": "centre", "clean": "center"})
            service.import_mappings(str(csv_path), "raw", "clean")

            with patch(
                "normflow.semantic_index.faiss.write_index",
                side_effect=RuntimeError("disk full"),
            ):
                suggestions = service.lookup(
                    "colr", semantic=True, llm=False, threshold=0.5,
                )

            info = service.project_info()
            assert suggestions[0].suggested_text == "color"
            assert info["semantic_index_status"] == "refresh_required"
            assert "normflow index build" in info["semantic_index_warning"]

    @patch(_INDEX_PATCH)
    def test_exact_only_lookup_does_not_refresh_dirty_index(self, mock_ensure):
        """Callers that disable semantic and LLM lookup avoid rebuild work."""
        mock_model = MagicMock()
        mock_model.encode.return_value = [[1.0, 0.0, 0.0]]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            service = MappingService(str(project_path))
            seed_mappings(project_path, [("colour", "color")])
            service.build_index()

            csv_path = project_path / "new-mapping.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=["raw", "clean"])
                writer.writeheader()
                writer.writerow({"raw": "centre", "clean": "center"})
            service.import_mappings(str(csv_path), "raw", "clean")

            with patch.object(service, "build_index", side_effect=AssertionError("rebuilt")):
                suggestions = service.lookup("centre", semantic=False, llm=False)

            assert suggestions[0].suggested_text == "center"
            assert service.project_info()["semantic_index_status"] == "refresh_required"

    @patch(_INDEX_PATCH)
    def test_legacy_index_is_verified_by_first_semantic_lookup(self, mock_ensure):
        """Existing Projects migrate lazily without a separate command."""
        mock_model = MagicMock()
        mock_model.encode.return_value = [[1.0, 0.0, 0.0]]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            service = MappingService(str(project_path))
            seed_mappings(project_path, [("colour", "color")])
            service.build_index()
            index_dir = project_path / ".normflow" / "faiss_index"
            generation = (index_dir / "current").read_text(encoding="utf-8").strip()
            active_dir = index_dir / "generations" / generation
            shutil.copy2(active_dir / "index.faiss", index_dir / "index.faiss")
            shutil.copy2(active_dir / "mapping_table.pkl", index_dir / "mapping_table.pkl")
            (index_dir / "current").unlink()
            assert service.project_info()["semantic_index_status"] == "unverified"

            suggestions = service.lookup(
                "colr", semantic=True, llm=False, threshold=0.5,
            )

            assert suggestions[0].suggested_text == "color"
            assert service.project_info()["semantic_index_status"] == "fresh"

    @patch(_INDEX_PATCH)
    def test_batch_retries_failed_refresh_only_on_the_next_operation(self, mock_ensure):
        """One failed refresh attempt applies to the whole lookup batch."""
        mock_model = MagicMock()
        mock_model.encode.return_value = [[1.0, 0.0, 0.0]]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            service = MappingService(str(project_path))
            seed_mappings(project_path, [("colour", "color")])
            service.build_index()
            csv_path = project_path / "new-mapping.csv"
            csv_path.write_text("raw,clean\ncentre,center\n", encoding="utf-8")
            service.import_mappings(str(csv_path), "raw", "clean")
            records = project_path / "records.csv"
            records.write_text("name\ncolr\ncolur\n", encoding="utf-8")

            with patch.object(
                service, "build_index", side_effect=RuntimeError("model unavailable"),
            ) as rebuild:
                result = service.lookup_batch(
                    str(records), "name", semantic=True, llm=False, threshold=0.5,
                )

            assert rebuild.call_count == 1
            assert result == "name,normalized_text\ncolr,color\ncolur,color\n"

    @patch(_INDEX_PATCH)
    def test_failed_atomic_publication_keeps_previous_index_active(self, mock_ensure):
        """The active index changes through one atomic pointer publication."""
        vectors = {
            "colour": [1.0, 0.0, 0.0],
            "centre": [0.0, 1.0, 0.0],
            "colr": [1.0, 0.0, 0.0],
        }
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: [vectors[text] for text in texts]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            service = MappingService(str(project_path))
            seed_mappings(project_path, [("colour", "color")])
            service.build_index()
            generations_dir = project_path / ".normflow" / "faiss_index" / "generations"
            generations_before = {path.name for path in generations_dir.iterdir()}
            csv_path = project_path / "new-mapping.csv"
            csv_path.write_text("raw,clean\ncentre,center\n", encoding="utf-8")
            service.import_mappings(str(csv_path), "raw", "clean")

            real_replace = os.replace

            def fail_current_pointer(source, destination):
                if Path(destination).name == "current":
                    raise OSError("publication interrupted")
                return real_replace(source, destination)

            with patch("normflow.semantic_index.os.replace", side_effect=fail_current_pointer):
                suggestions = service.lookup(
                    "colr", semantic=True, llm=False, threshold=0.5,
                )

            assert suggestions[0].suggested_text == "color"
            assert service.project_info()["semantic_index_status"] == "refresh_required"
            assert {path.name for path in generations_dir.iterdir()} == generations_before


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
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            seed_mappings(project_path, [("colour", "color")])

            result = runner.invoke(app, ["index", "build"])
            assert result.exit_code == 0

            result = runner.invoke(
                app,
                ["suggest", "colr", "--semantic-threshold", "0.5"],
            )
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert len(data["suggestions"]) > 0
            assert data["suggestions"][0]["method"] == "semantic"

    def test_exact_only_flags_disable_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            seed_mappings(project_path, [("colour", "color")])

            result = runner.invoke(
                app,
                ["suggest", "colr", "--no-semantic", "--no-llm"],
            )
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert data["suggestions"] == []

    def test_default_limit_is_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            seed_mappings(project_path, [("colour", "color")])

            result = runner.invoke(
                app,
                ["suggest", "colour"],
            )
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert len(data["suggestions"]) == 1

    @patch(_INDEX_PATCH)
    def test_dirty_index_progress_uses_stderr_and_preserves_json(self, mock_ensure):
        mock_model = MagicMock()
        mock_model.encode.return_value = [[1.0, 0.0, 0.0]]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            service = MappingService(str(project_path))
            seed_mappings(project_path, [("colour", "color")])
            service.build_index()
            csv_path = project_path / "new-mapping.csv"
            csv_path.write_text("raw,clean\ncentre,center\n", encoding="utf-8")
            service.import_mappings(str(csv_path), "raw", "clean")

            result = runner.invoke(app, ["suggest", "colr", "--no-llm"])

            assert result.exit_code == 0
            assert json.loads(result.stdout)["suggestions"][0]["suggested_text"] == "color"
            assert "rebuilding before Suggestions" in result.stderr

    @patch(_INDEX_PATCH)
    def test_failed_cli_refresh_warns_without_corrupting_json(self, mock_ensure):
        mock_model = MagicMock()
        mock_model.encode.return_value = [[1.0, 0.0, 0.0]]
        mock_model.get_sentence_embedding_dimension.return_value = 3
        mock_ensure.return_value = mock_model

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            service = MappingService(str(project_path))
            seed_mappings(project_path, [("colour", "color")])
            service.build_index()
            csv_path = project_path / "new-mapping.csv"
            csv_path.write_text("raw,clean\ncentre,center\n", encoding="utf-8")
            service.import_mappings(str(csv_path), "raw", "clean")

            with patch(
                "normflow.semantic_index.faiss.write_index",
                side_effect=RuntimeError("disk full"),
            ):
                result = runner.invoke(app, ["suggest", "colr", "--no-llm"])

            assert result.exit_code == 0
            assert json.loads(result.stdout)["suggestions"][0]["suggested_text"] == "color"
            assert "normflow index build" in result.stderr


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
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            seed_mappings(project_path, [("colour", "color"), ("centre", "center")])

            result = runner.invoke(app, ["index", "build"])
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
            project_path = Path(tmpdir) / "proj"
            init_project(str(project_path))
            seed_mappings(project_path, [("colour", "color")])

            runner.invoke(app, ["index", "build"])
            result = runner.invoke(app, ["index", "clear"])
            assert result.exit_code == 0

    def test_index_build_outside_project(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(app, ["index", "build"])

        assert result.exit_code != 0
