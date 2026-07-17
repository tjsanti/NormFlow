"""End-to-end contract tests for the managed release installer."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parents[1]


def _executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _release_assets(path: Path) -> None:
    assets = {
        "normflow-0.1.0-py3-none-any.whl": b"wheel",
        "normflow-0.1.0-constraints-linux-x86_64-py313.txt": b"constraints",
        "normflow-0.1.0-model-all-MiniLM-L6-v2-test.tar.gz": b"model",
    }
    for name, contents in assets.items():
        (path / name).write_bytes(contents)
    manifest = {
        "version": "0.1.0",
        "platform": "linux-x86_64-py313",
        "assets": [
            {
                "kind": kind,
                "filename": name,
                "sha256": hashlib.sha256(contents).hexdigest(),
            }
            for kind, (name, contents) in zip(("wheel", "constraints", "model"), assets.items())
        ],
    }
    (path / "normflow-payload-linux-x86_64-py313.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def _installer_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    assets = tmp_path / "assets"
    assets.mkdir()
    _release_assets(assets)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    record = tmp_path / "record"

    _executable(
        fake_bin / "curl",
        """#!/bin/sh
set -eu
destination=
expect_destination=0
last=
for argument do
  if [ "$expect_destination" = 1 ]; then
    destination=$argument
    expect_destination=0
  elif [ "$argument" = --output ]; then
    expect_destination=1
  fi
  last=$argument
done
last=${last##*/}
case "$last" in
  uv-*) : > "$destination" ;;
  *) cp "$NORMFLOW_TEST_ASSETS/$last" "$destination" ;;
esac
printf '%s\n' "$last" >> "$NORMFLOW_TEST_CURL_RECORD"
""",
    )
    _executable(fake_bin / "uname", "#!/bin/sh\n[ \"$1\" = -s ] && echo Linux || echo x86_64\n")
    _executable(fake_bin / "getconf", "#!/bin/sh\necho 'glibc 2.39'\n")
    _executable(
        fake_bin / "tar",
        """#!/bin/sh
set -eu
destination=
expect_destination=0
for argument do
  if [ "$expect_destination" = 1 ]; then destination=$argument; expect_destination=0
  elif [ "$argument" = -C ]; then expect_destination=1; fi
done
case " $* " in
  *uv-*) mkdir -p "$destination/uv-x86_64-unknown-linux-gnu"; cp "$NORMFLOW_TEST_UV" "$destination/uv-x86_64-unknown-linux-gnu/uv"; chmod +x "$destination/uv-x86_64-unknown-linux-gnu/uv" ;;
  *model*) exit 0 ;;
  *) command tar "$@" ;;
esac
""",
    )
    fake_uv = tmp_path / "fake-uv"
    _executable(
        fake_uv,
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$NORMFLOW_TEST_RECORD"
case "$1" in
  python) exit 0 ;;
  venv) for environment do :; done; mkdir -p "$environment/bin"; cp "$0" "$environment/bin/python" ;;
  pip) python=; expect_python=0; for argument do
         if [ "$expect_python" = 1 ]; then python=$argument; expect_python=0
         elif [ "$argument" = --python ]; then expect_python=1; fi
       done
       environment=$(dirname "$(dirname "$python")")
       cat > "$environment/bin/normflow" <<'COMMAND'
#!/bin/sh
printf '%s\n' "$*" >> "$NORMFLOW_TEST_NORMFLOW_RECORD"
case "$1" in --version|-V) echo 0.1.0 ;; ui) exit 0 ;; esac
COMMAND
      chmod +x "$environment/bin/normflow" ;;
  -c) [ "${NORMFLOW_TEST_FAIL_SMOKE:-}" != 1 ] || exit 1; exit 0 ;;
esac
""",
    )
    environment = os.environ | {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_BIN_HOME": str(tmp_path / "user-bin"),
        "NORMFLOW_RELEASE_URL": "https://example.test/release",
        "NORMFLOW_TEST_ASSETS": str(assets),
        "NORMFLOW_TEST_UV": str(fake_uv),
        "NORMFLOW_TEST_UV_HOME": str(tmp_path / "data" / "normflow" / "uv" / "0.6.14" / "bin"),
        "NORMFLOW_TEST_RECORD": str(record),
        "NORMFLOW_TEST_NORMFLOW_RECORD": str(tmp_path / "normflow-record"),
        "NORMFLOW_TEST_CURL_RECORD": str(tmp_path / "curl-record"),
    }
    return environment, record


def test_install_sh_installs_a_verified_managed_release_without_ambient_python(
    tmp_path: Path,
):
    environment, record = _installer_environment(tmp_path)

    result = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    executable = tmp_path / "user-bin" / "normflow"
    assert executable.is_symlink()
    assert executable.resolve().parent.name == "bin"
    assert "python install 3.13" in record.read_text(encoding="utf-8")
    assert "pip install" in record.read_text(encoding="utf-8")
    assert "-c" in record.read_text(encoding="utf-8")
    assert (tmp_path / "normflow-record").read_text(encoding="utf-8").splitlines() == [
        "--version",
        "-V",
    ]


@pytest.mark.parametrize(
    ("system", "machine", "getconf", "message"),
    [
        ("Darwin", "x86_64", "", "Apple Silicon"),
        ("Linux", "aarch64", "", "Linux ARM"),
        ("Linux", "x86_64", "exit 1", "musl/Alpine"),
        ("MINGW64_NT", "x86_64", "", "supports only"),
    ],
)
def test_install_sh_rejects_unsupported_platform_before_downloading(
    tmp_path: Path, system: str, machine: str, getconf: str, message: str
):
    environment, _record = _installer_environment(tmp_path)
    _executable(
        tmp_path / "bin" / "uname",
        f"#!/bin/sh\n[ \"$1\" = -s ] && echo {system} || echo {machine}\n",
    )
    _executable(tmp_path / "bin" / "sw_vers", "#!/bin/sh\necho 14.0\n")
    if getconf:
        _executable(tmp_path / "bin" / "getconf", f"#!/bin/sh\n{getconf}\n")

    result = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert message in result.stderr
    assert not (tmp_path / "data" / "normflow").exists()
    assert not (tmp_path / "curl-record").exists()


def test_install_sh_does_not_activate_an_asset_with_a_bad_embedded_checksum(
    tmp_path: Path,
):
    environment, _record = _installer_environment(tmp_path)
    manifest = tmp_path / "assets" / "normflow-payload-linux-x86_64-py313.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("ba599261", "00000000"),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "wheel checksum verification failed" in result.stderr
    assert not (tmp_path / "user-bin" / "normflow").exists()


def test_install_sh_discards_a_runtime_that_fails_smoke_testing(tmp_path: Path):
    environment, _record = _installer_environment(tmp_path)
    environment["NORMFLOW_TEST_FAIL_SMOKE"] = "1"

    failed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    runtime = tmp_path / "data" / "normflow" / "releases" / "0.1.0"
    assert failed.returncode != 0
    assert not runtime.exists()

    environment.pop("NORMFLOW_TEST_FAIL_SMOKE")
    retried = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert retried.returncode == 0, retried.stderr
    assert (tmp_path / "user-bin" / "normflow").is_symlink()
