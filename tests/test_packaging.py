"""Installed-package contract tests."""

from email.parser import BytesParser
from email.policy import default
from pathlib import Path
import subprocess
import sys
import zipfile


ROOT = Path(__file__).parents[1]


def _build_wheel(tmp_path: Path) -> Path:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
    )
    return next(tmp_path.glob("normflow-*.whl"))


def test_wheel_declares_ui_stack_and_contains_browser_build(tmp_path: Path):
    wheel = _build_wheel(tmp_path)

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_name))

    requirements = [value.lower() for value in metadata.get_all("Requires-Dist", [])]
    assert any(value.startswith("fastapi") for value in requirements)
    assert any(value.startswith("uvicorn") for value in requirements)
    assert any(value.startswith("python-multipart") for value in requirements)
    assert not any("extra == 'server'" in value or 'extra == "server"' in value for value in requirements)
    assert "normflow/static/index.html" in names
    assert any(name.startswith("normflow/static/assets/") and name.endswith(".js") for name in names)
    assert any(name.startswith("normflow/static/assets/") and name.endswith(".css") for name in names)


def test_wheel_can_import_ui_server_stack_from_a_normal_environment(tmp_path: Path):
    wheel = _build_wheel(tmp_path)
    check = """
import sys
sys.path.insert(0, sys.argv[1])
import fastapi
import multipart
import uvicorn
from normflow.api import create_app
from normflow.cli import app
assert callable(create_app)
assert app.info.name == 'normflow'
"""

    subprocess.run(
        [sys.executable, "-c", check, str(wheel)],
        cwd=tmp_path,
        check=True,
    )
