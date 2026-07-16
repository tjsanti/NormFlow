"""Installed-package contract tests."""

from email.parser import BytesParser
from email.policy import default
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).parents[1]


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents)
    path.chmod(0o755)


@pytest.fixture(scope="module")
def release_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp_path = tmp_path_factory.mktemp("release")
    subprocess.run(
        [str(ROOT / "scripts" / "build-wheel"), str(tmp_path)],
        cwd=ROOT,
        check=True,
    )
    return next(tmp_path.glob("normflow-*.whl"))


def test_release_build_command_builds_frontend_and_wheel(release_wheel: Path):
    with zipfile.ZipFile(release_wheel) as archive:
        names = archive.namelist()

    assert "normflow/static/index.html" in names
    assert any(
        name.startswith("normflow/static/assets/") and name.endswith(".js")
        for name in names
    )
    assert any(
        name.startswith("normflow/static/assets/") and name.endswith(".css")
        for name in names
    )


def test_release_build_fails_when_frontend_build_produces_no_assets(tmp_path: Path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    build_script = ROOT / "scripts" / "build-wheel"
    if build_script.exists():
        shutil.copy2(build_script, scripts / "build-wheel")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "npm", "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "uv",
        "#!/bin/sh\ntouch \"$PWD/uv-was-called\"\nexit 0\n",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"

    result = subprocess.run(
        ["sh", str(scripts / "build-wheel")],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "required browser asset is missing" in result.stderr
    assert not (checkout / "uv-was-called").exists()


def test_release_build_fails_when_wheel_omits_browser_assets(tmp_path: Path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "build-wheel", scripts / "build-wheel")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "npm",
        """#!/bin/sh
mkdir -p src/normflow/static/assets
touch src/normflow/static/index.html
touch src/normflow/static/assets/app.js
touch src/normflow/static/assets/app.css
""",
    )
    _write_executable(
        fake_bin / "uv",
        f"""#!{sys.executable}
from pathlib import Path
import zipfile

wheel = Path("dist/normflow-0.1.0-py3-none-any.whl")
wheel.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(wheel, "w") as archive:
    archive.writestr("normflow/__init__.py", "")
""",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"

    result = subprocess.run(
        ["sh", str(scripts / "build-wheel")],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "wheel is missing required browser asset" in result.stderr


def test_release_build_fails_when_wheel_browser_assets_differ_from_build(
    tmp_path: Path,
):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "build-wheel", scripts / "build-wheel")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "npm",
        """#!/bin/sh
mkdir -p src/normflow/static/assets
printf 'current index' > src/normflow/static/index.html
printf 'current script' > src/normflow/static/assets/app.js
printf 'current style' > src/normflow/static/assets/app.css
""",
    )
    _write_executable(
        fake_bin / "uv",
        f"""#!{sys.executable}
from pathlib import Path
import zipfile

wheel = Path("dist/normflow-0.1.0-py3-none-any.whl")
wheel.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(wheel, "w") as archive:
    archive.writestr("normflow/static/index.html", "stale index")
    archive.writestr("normflow/static/assets/app.js", "stale script")
    archive.writestr("normflow/static/assets/app.css", "stale style")
""",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"

    result = subprocess.run(
        ["sh", str(scripts / "build-wheel")],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "wheel browser assets do not match frontend build" in result.stderr


def test_wheel_declares_ui_stack_and_contains_browser_build(release_wheel: Path):
    with zipfile.ZipFile(release_wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_name))

    requirements = [value.lower() for value in metadata.get_all("Requires-Dist", [])]
    assert any(value.startswith("fastapi") for value in requirements)
    assert any(value.startswith("uvicorn") for value in requirements)
    assert any(value.startswith("python-multipart") for value in requirements)
    assert not any("extra == 'server'" in value or 'extra == "server"' in value for value in requirements)
    assert "normflow/static/index.html" in names
    assert any(
        name.startswith("normflow/static/assets/") and name.endswith(".js")
        for name in names
    )
    assert any(
        name.startswith("normflow/static/assets/") and name.endswith(".css")
        for name in names
    )


def test_wheel_can_import_ui_server_stack_from_a_normal_environment(
    release_wheel: Path, tmp_path: Path
):
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
        [sys.executable, "-c", check, str(release_wheel)],
        cwd=tmp_path,
        check=True,
    )


def test_wheel_browser_build_uses_unified_review_item_acceptance(release_wheel: Path):
    with zipfile.ZipFile(release_wheel) as archive:
        javascript = b"\n".join(
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("normflow/static/assets/") and name.endswith(".js")
        )

    assert b"/edit-and-accept" not in javascript
    assert b"/accept" in javascript


def test_isolated_wheel_install_can_import_package_and_fetch_browser_ui(
    release_wheel: Path, tmp_path: Path
):
    environment = tmp_path / "environment"
    subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
    python = environment / "bin" / "python"
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(release_wheel)],
        check=True,
    )
    smoke_test = """
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import normflow

static = Path(normflow.__file__).with_name("static")
server = ThreadingHTTPServer(
    ("127.0.0.1", 0),
    partial(SimpleHTTPRequestHandler, directory=static),
)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    with urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as response:
        page = response.read()
finally:
    server.shutdown()
    thread.join()
    server.server_close()

assert b"<title>NormFlow</title>" in page
assert b'/assets/' in page
"""
    subprocess.run([str(python), "-c", smoke_test], cwd=tmp_path, check=True)
