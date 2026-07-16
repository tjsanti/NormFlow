"""Fresh-process import boundaries for lightweight CLI entry points."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]
HEAVY_MODULE_ROOTS = (
    "faiss",
    "fastapi",
    "normflow.api",
    "normflow.batch_import",
    "normflow.llm_config",
    "normflow.llm_matcher",
    "normflow.mapping_service",
    "normflow.semantic_index",
    "openai",
    "sentence_transformers",
    "sqlmodel",
    "torch",
    "uvicorn",
)


def _run_fresh_cli(arguments: list[str]) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    script = f"""
import json
import sys

from normflow.cli import app

app(args={arguments!r}, prog_name="normflow", standalone_mode=False)
heavy_roots = {HEAVY_MODULE_ROOTS!r}
loaded = sorted(
    name
    for name in sys.modules
    if any(name == root or name.startswith(f"{{root}}.") for root in heavy_roots)
)
print("__NORMFLOW_LOADED__=" + json.dumps(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    output, loaded_json = completed.stdout.rsplit("__NORMFLOW_LOADED__=", 1)
    completed.stdout = output
    return completed, json.loads(loaded_json)


def test_importing_cli_keeps_normflow_service_boundaries_unloaded():
    script = """
import json
import sys

import normflow.cli

heavy_roots = (
    "normflow.api",
    "normflow.batch_import",
    "normflow.llm_config",
    "normflow.llm_matcher",
    "normflow.mapping_service",
    "normflow.semantic_index",
)
loaded = sorted(
    name
    for name in sys.modules
    if any(name == root or name.startswith(f"{root}.") for root in heavy_roots)
)
print(json.dumps(loaded))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []


def test_top_level_help_does_not_load_heavy_packages():
    completed, loaded = _run_fresh_cli(["--help"])

    assert completed.returncode == 0
    assert "Usage: normflow [OPTIONS] COMMAND [ARGS]" in completed.stdout
    assert loaded == []


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flags_do_not_load_heavy_packages(flag: str):
    completed, loaded = _run_fresh_cli([flag])

    assert completed.returncode == 0
    assert completed.stdout == "0.1.0\n"
    assert completed.stderr == ""
    assert loaded == []


def test_project_command_loads_service_stack_only_after_dispatch(tmp_path: Path):
    from normflow.project_service import init_project

    project = init_project(tmp_path / "project")
    script = """
import json
import sys

from normflow.cli import app

before_dispatch = "normflow.mapping_service" in sys.modules
app(args=["info"], prog_name="normflow", standalone_mode=False)
after_dispatch = "normflow.mapping_service" in sys.modules
print("__NORMFLOW_IMPORT_STATE__=" + json.dumps({
    "before_dispatch": before_dispatch,
    "after_dispatch": after_dispatch,
}))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    output, state_json = completed.stdout.rsplit("__NORMFLOW_IMPORT_STATE__=", 1)

    assert "Mappings:   0" in output
    assert json.loads(state_json) == {
        "before_dispatch": False,
        "after_dispatch": True,
    }
