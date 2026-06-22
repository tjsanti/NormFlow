"""Tests for the NormFlow CLI."""

import json
import tempfile
from pathlib import Path

from sqlmodel import Session
from typer.testing import CliRunner

from normflow.cli import app
from normflow.mapping_service import ExampleMapping, MappingService


runner = CliRunner()


def test_cli_help():
    """CLI help should exit cleanly with code 0."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "normflow" in result.stdout


def test_init_creates_workspace():
    """`normflow init` should create the expected structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "myproject"
        result = runner.invoke(app, ["init", "--workspace", str(ws_path)])

        assert result.exit_code == 0

        assert ws_path.is_dir()
        assert (ws_path / "normflow.db").is_file()
        assert (ws_path / "input").is_dir()
        assert (ws_path / "output").is_dir()
        assert (ws_path / "samples").is_dir()


def test_workspace_info():
    """`normflow workspace info` should report correct counts after init."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "myproject"

        # Initialize
        init_result = runner.invoke(app, ["init", "--workspace", str(ws_path)])
        assert init_result.exit_code == 0

        # Info
        info_result = runner.invoke(app, ["info", "--workspace", str(ws_path)])
        assert info_result.exit_code == 0

        assert "myproject" in info_result.stdout
        assert "Mappings:   0" in info_result.stdout
        assert "Suggestions: 0" in info_result.stdout


def test_workspace_info_errors_on_invalid_path():
    """`normflow info` should error when given a non-workspace path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(app, ["info", "--workspace", tmpdir])
        assert result.exit_code != 0


# ---- import tests ----


def _write_csv(path: Path, header: str, *rows: str) -> None:
    """Write a CSV file. Each row should be a full CSV line (e.g. 'hello,world')."""
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def test_import_creates_mappings():
    """`normflow import` should insert CSV rows as mappings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "mappings.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])
        _write_csv(csv_path, "source,target", " hello,world", "world,bar", "  foo  ,baz")

        result = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert result.exit_code == 0
        assert "Imported 3" in result.stdout


def test_import_skips_duplicates():
    """`normflow import` should skip rows where raw_text already exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "mappings.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])
        _write_csv(csv_path, "source,target", "hello,world", "foo,bar")

        # First import
        r1 = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert r1.exit_code == 0
        assert "Imported 2" in r1.stdout

        # Second import same file
        r2 = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert r2.exit_code == 0
        assert "0 new" in r2.stdout
        assert "2 skipped" in r2.stdout


def test_import_invalid_column():
    """`normflow import` should error when source column is missing from CSV."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "mappings.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])
        _write_csv(csv_path, "src,dst", "hello,world")

        result = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert result.exit_code != 0
        assert "source" in result.stdout.lower() or "column" in result.stdout.lower()


def test_import_skips_empty_rows():
    """`normflow import` should silently skip empty rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "mappings.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])
        csv_path.write_text("source,target\nhello,world\n\n\nfoo,bar\n")

        result = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(csv_path), "--source-column", "source", "--target-column", "target"],
        )
        assert result.exit_code == 0
        assert "Imported 2" in result.stdout


# ---- export tests ----


def test_export_writes_csv():
    """`normflow export` should write mappings to a CSV file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "out.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        # Insert mappings directly via the service
        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="hello", normalized_text="world"))
            session.add(ExampleMapping(raw_text="foo", normalized_text="bar"))
            session.commit()

        result = runner.invoke(
            app,
            ["export", "--workspace", str(ws_path), str(csv_path)],
        )
        assert result.exit_code == 0
        assert "Exported 2" in result.stdout
        assert csv_path.exists()
        content = csv_path.read_text()
        assert "raw_text" in content
        assert "normalized_text" in content
        assert "hello" in content
        assert "world" in content


def test_export_custom_columns():
    """`normflow export` should use custom column names when flags are provided."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "out.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="hello", normalized_text="world"))
            session.commit()

        result = runner.invoke(
            app,
            ["export", "--workspace", str(ws_path), str(csv_path), "--source-column", "src", "--target-column", "tgt"],
        )
        assert result.exit_code == 0
        content = csv_path.read_text()
        assert "src" in content
        assert "tgt" in content
        assert "raw_text" not in content


def test_import_export_round_trip():
    """Import a CSV, export it, and the contents should match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        input_csv = ws_path / "input.csv"
        output_csv = ws_path / "output.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])
        _write_csv(input_csv, "source,target", "hello,world", "foo,bar")

        runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(input_csv), "--source-column", "source", "--target-column", "target"],
        )

        result = runner.invoke(
            app,
            ["export", "--workspace", str(ws_path), str(output_csv)],
        )
        assert result.exit_code == 0

        # Round-trip: import again from exported file should be 0 new
        result2 = runner.invoke(
            app,
            ["import", "--workspace", str(ws_path), str(output_csv), "--source-column", "raw_text", "--target-column", "normalized_text"],
        )
        assert result2.exit_code == 0
        assert "0 new" in result2.stdout


# ---- suggest tests ----


def test_suggest_exact_match_found():
    """`normflow suggest` should return a suggestion when exact match exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        result = runner.invoke(
            app,
            ["suggest", "--workspace", str(ws_path), "colour"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert data["raw_text"] == "colour"
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["suggested_text"] == "color"
        assert data["suggestions"][0]["method"] == "exact"
        assert data["suggestions"][0]["confidence"] == 1.0


def test_suggest_no_match_found():
    """`normflow suggest` should return empty suggestions when no match exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        result = runner.invoke(
            app,
            ["suggest", "--workspace", str(ws_path), "colr"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert data["raw_text"] == "colr"
        assert data["suggestions"] == []


def test_suggest_limit_respected():
    """`normflow suggest --limit 0` should return empty suggestions even when match exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        result = runner.invoke(
            app,
            ["suggest", "--workspace", str(ws_path), "colour", "--limit", "0"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert data["suggestions"] == []


def test_suggest_limit_default():
    """`normflow suggest` with default limit should return the match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        result = runner.invoke(
            app,
            ["suggest", "--workspace", str(ws_path), "colour", "--limit", "5"],
        )
        assert result.exit_code == 0

        data = json.loads(result.stdout)
        assert len(data["suggestions"]) == 1


def test_suggest_invalid_workspace():
    """`normflow suggest` should error on non-workspace path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(
            app,
            ["suggest", "--workspace", tmpdir, "colour"],
        )
        assert result.exit_code != 0


# ---- suggest batch tests ----


def test_suggest_batch_basic():
    """`normflow suggest batch` should output CSV with normalized_text column."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "input.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        # Seed mappings
        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.add(ExampleMapping(raw_text="centre", normalized_text="center"))
            session.commit()

        # Input CSV with a raw text column
        _write_csv(csv_path, "id,item", "1,colour", "2,centre", "3,unknown")

        result = runner.invoke(
            app,
            ["suggest-batch", "--workspace", str(ws_path), str(csv_path), "--column", "item"],
        )
        assert result.exit_code == 0

        lines = result.stdout.strip().split("\n")
        header = lines[0]
        assert "id" in header
        assert "item" in header
        assert "normalized_text" in header

        # colour -> color, centre -> center, unknown -> blank
        assert "color" in result.stdout
        assert "center" in result.stdout


def test_suggest_batch_no_match_blank():
    """Rows with no match should have blank normalized_text."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "input.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        _write_csv(csv_path, "text", "colour", "nope")

        result = runner.invoke(
            app,
            ["suggest-batch", "--workspace", str(ws_path), str(csv_path), "--column", "text"],
        )
        assert result.exit_code == 0

        lines = result.stdout.strip().split("\n")
        # header + 2 data rows
        assert len(lines) == 3
        # second data row (nope) has blank normalized_text
        last_line = lines[2]
        assert "nope" in last_line


def test_suggest_batch_custom_output_column():
    """--output-column should rename the suggestion column."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "input.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        _write_csv(csv_path, "text", "colour")

        result = runner.invoke(
            app,
            ["suggest-batch", "--workspace", str(ws_path), str(csv_path), "--column", "text", "--output-column", "mapping"],
        )
        assert result.exit_code == 0
        assert "mapping" in result.stdout
        assert "normalized_text" not in result.stdout


def test_suggest_batch_output_to_file():
    """--output should write CSV to the specified file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "input.csv"
        out_path = ws_path / "output.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        _write_csv(csv_path, "text", "colour")

        result = runner.invoke(
            app,
            ["suggest-batch", "--workspace", str(ws_path), str(csv_path), "--column", "text", "--output", str(out_path)],
        )
        assert result.exit_code == 0
        assert out_path.exists()
        assert "color" in out_path.read_text()


def test_suggest_batch_excludes_entirely_blank_rows():
    """Rows where every column is blank should be excluded from output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "input.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        # Row 1: valid, Row 2: all blank, Row 3: valid
        csv_path.write_text("id,text\n1,colour\n,,\n3,centre\n")

        result = runner.invoke(
            app,
            ["suggest-batch", "--workspace", str(ws_path), str(csv_path), "--column", "text"],
        )
        assert result.exit_code == 0

        lines = result.stdout.strip().split("\n")
        # header + 2 data rows (blank row excluded)
        assert len(lines) == 3


def test_suggest_batch_includes_partial_rows_skips_processing():
    """Rows with some data but blank raw text column should appear in output with blank suggestion."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "input.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        # Row 1: valid, Row 2: has id but blank text, Row 3: valid
        csv_path.write_text("id,text\n1,colour\n2,\n3,centre\n")

        result = runner.invoke(
            app,
            ["suggest-batch", "--workspace", str(ws_path), str(csv_path), "--column", "text"],
        )
        assert result.exit_code == 0

        lines = result.stdout.strip().split("\n")
        # header + 3 data rows (partial row included)
        assert len(lines) == 4
        # middle row has id=2
        assert "2" in lines[2]


def test_suggest_batch_preserves_extra_columns():
    """All original columns should be preserved in the output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "input.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        ms = MappingService(str(ws_path))
        with ms.session() as session:
            session.add(ExampleMapping(raw_text="colour", normalized_text="color"))
            session.commit()

        _write_csv(csv_path, "id,category,text,notes", "1,UK,colour,primary")

        result = runner.invoke(
            app,
            ["suggest-batch", "--workspace", str(ws_path), str(csv_path), "--column", "text"],
        )
        assert result.exit_code == 0

        header = result.stdout.strip().split("\n")[0]
        assert "id" in header
        assert "category" in header
        assert "text" in header
        assert "notes" in header
        assert "normalized_text" in header


def test_suggest_batch_invalid_workspace():
    """`normflow suggest batch` should error on non-workspace path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = Path(tmpdir) / "input.csv"
        _write_csv(csv_path, "text", "hello")

        result = runner.invoke(
            app,
            ["suggest-batch", "--workspace", str(ws_path), str(csv_path), "--column", "text"],
        )
        assert result.exit_code != 0


def test_suggest_batch_missing_column():
    """`normflow suggest batch` should error when column is not in CSV."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        csv_path = ws_path / "input.csv"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])
        _write_csv(csv_path, "id,text", "1,hello")

        result = runner.invoke(
            app,
            ["suggest-batch", "--workspace", str(ws_path), str(csv_path), "--column", "missing"],
        )
        assert result.exit_code != 0


def test_suggest_batch_missing_input_file():
    """`normflow suggest batch` should error when input file does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"

        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        result = runner.invoke(
            app,
            ["suggest-batch", "--workspace", str(ws_path), "nonexistent.csv", "--column", "text"],
        )
        assert result.exit_code != 0


# ---- review tests ----


def _seed_suggestions(ws_path: Path, suggestions: list[tuple[str, str, str]]) -> None:
    """Seed Suggestion rows. Each tuple is (raw_text, suggested_text, status)."""
    from normflow.mapping_service import MappingService, Suggestion

    ms = MappingService(str(ws_path))
    with ms.session() as session:
        for raw_text, suggested_text, status in suggestions:
            session.add(Suggestion(
                raw_text=raw_text,
                suggested_text=suggested_text,
                status=status,
            ))
        session.commit()


def test_review_list_shows_pending_suggestions():
    """`normflow review list` should show only pending suggestions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        _seed_suggestions(ws_path, [
            ("o2 sensor", "O2 Sensor", "pending"),
            ("oxygen sensor", "Oxygen Sensor", "pending"),
            ("old part", "Old Part", "accepted"),
        ])

        result = runner.invoke(
            app,
            ["review", "list", "--workspace", str(ws_path)],
        )
        assert result.exit_code == 0
        assert "o2 sensor" in result.stdout
        assert "oxygen sensor" in result.stdout
        # accepted suggestion should not appear
        assert "old part" not in result.stdout


def test_review_list_empty_when_no_pending():
    """`normflow review list` should show empty when no pending suggestions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        result = runner.invoke(
            app,
            ["review", "list", "--workspace", str(ws_path)],
        )
        assert result.exit_code == 0


def test_review_list_json_output():
    """`normflow review list --json` should return valid JSON array."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        _seed_suggestions(ws_path, [
            ("o2 sensor", "O2 Sensor", "pending"),
        ])

        result = runner.invoke(
            app,
            ["review", "list", "--workspace", str(ws_path), "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["raw_text"] == "o2 sensor"
        assert data[0]["suggested_text"] == "O2 Sensor"


def test_review_accept_inserts_mapping():
    """`normflow review accept` should mark accepted and insert a mapping."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        _seed_suggestions(ws_path, [
            ("o2 sensor", "O2 Sensor", "pending"),
        ])

        result = runner.invoke(
            app,
            ["review", "accept", "--workspace", str(ws_path), "--record-id", "1"],
        )
        assert result.exit_code == 0

        # Verify mapping was inserted
        info_result = runner.invoke(
            app,
            ["info", "--workspace", str(ws_path)],
        )
        assert "Mappings:   1" in info_result.stdout


def test_review_edit_inserts_mapping_with_custom_text():
    """`normflow review edit` should mark accepted_edited and insert mapping with edited text."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        _seed_suggestions(ws_path, [
            ("o2 sensor", "O2 Sensor", "pending"),
        ])

        result = runner.invoke(
            app,
            ["review", "edit", "--workspace", str(ws_path), "--record-id", "1", "--normalized-text", "Oxygen Sensor"],
        )
        assert result.exit_code == 0

        # Verify mapping was inserted with edited text
        info_result = runner.invoke(
            app,
            ["info", "--workspace", str(ws_path)],
        )
        assert "Mappings:   1" in info_result.stdout

        # Verify the mapping has the custom text
        ms = MappingService(str(ws_path))
        with ms.session() as session:
            from sqlmodel import select
            mapping = session.exec(
                select(ExampleMapping).where(ExampleMapping.raw_text == "o2 sensor")
            ).first()
        assert mapping is not None
        assert mapping.normalized_text == "Oxygen Sensor"


def test_review_accept_already_reviewed_fails():
    """`normflow review accept` should fail when suggestion is already reviewed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        _seed_suggestions(ws_path, [
            ("o2 sensor", "O2 Sensor", "accepted"),
        ])

        result = runner.invoke(
            app,
            ["review", "accept", "--workspace", str(ws_path), "--record-id", "1"],
        )
        assert result.exit_code != 0


def test_review_edit_invalid_record_id_fails():
    """`normflow review edit` should fail when record id does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_path = Path(tmpdir) / "proj"
        runner.invoke(app, ["init", "--workspace", str(ws_path)])

        result = runner.invoke(
            app,
            ["review", "edit", "--workspace", str(ws_path), "--record-id", "999", "--normalized-text", "Something"],
        )
        assert result.exit_code != 0
