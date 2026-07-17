"""End-to-end contract tests for the managed release installer."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[1]
UV_ARCHIVE_SHA256 = {
    "aarch64-apple-darwin": (
        "4ea4731010fbd1bc8e790e07f199f55a5c7c2c732e9b77f85e302b0bee61b756"
    ),
    "x86_64-unknown-linux-gnu": (
        "0aaf451c391d3913823bfb8ed354b446dcfd0553a32ed8266611e4181c61fd51"
    ),
}


def _executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _release_assets(
    path: Path,
    platform: str,
    *,
    version: str = "0.1.0",
    model: bytes = b"model",
) -> None:
    assets = {
        f"normflow-{version}-py3-none-any.whl": b"wheel",
        f"normflow-{version}-constraints-linux-x86_64-py313.txt": b"constraints",
        f"normflow-{version}-model-all-MiniLM-L6-v2-test.tar.gz": model,
    }
    for name, contents in assets.items():
        (path / name).write_bytes(contents)
    manifest = {
        "version": version,
        "platform": platform,
        "assets": [
            {
                "kind": kind,
                "filename": name,
                "sha256": hashlib.sha256(contents).hexdigest(),
            }
            for kind, (name, contents) in zip(("wheel", "constraints", "model"), assets.items())
        ],
    }
    (path / f"normflow-payload-{platform}.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def _installer_environment(
    tmp_path: Path,
    *,
    platform: str = "linux-x86_64-py313",
    system: str = "Linux",
    machine: str = "x86_64",
    macos_version: str = "14.0",
    uv_target: str = "x86_64-unknown-linux-gnu",
    version: str = "0.1.0",
    model: bytes = b"model",
) -> tuple[dict[str, str], Path]:
    assets = tmp_path / "assets"
    assets.mkdir()
    _release_assets(assets, platform, version=version, model=model)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    record = tmp_path / "record"
    real_mv = shutil.which("mv")
    assert real_mv

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
  uv-*) if [ "${NORMFLOW_TEST_CORRUPT_UV:-}" = 1 ]; then printf 'corrupt uv' > "$destination"; else printf 'trusted uv' > "$destination"; fi ;;
  *) cp "$NORMFLOW_TEST_ASSETS/$last" "$destination" ;;
esac
printf '%s\n' "$last" >> "$NORMFLOW_TEST_CURL_RECORD"
""",
    )
    _executable(
        fake_bin / "uname",
        f"#!/bin/sh\n[ \"$1\" = -s ] && echo {system} || echo {machine}\n",
    )
    _executable(fake_bin / "getconf", "#!/bin/sh\necho 'glibc 2.39'\n")
    _executable(fake_bin / "sw_vers", f"#!/bin/sh\necho {macos_version}\n")
    for command in ("python", "python3"):
        _executable(fake_bin / command, "#!/bin/sh\nexit 97\n")
    real_shasum = shutil.which("shasum")
    real_sha256sum = shutil.which("sha256sum")
    assert real_shasum or real_sha256sum
    _executable(
        fake_bin / "shasum",
        """#!/bin/sh
set -eu
for argument do file=$argument; done
case "$file" in
  *uv-*) if [ "${NORMFLOW_TEST_CORRUPT_UV:-}" = 1 ]; then printf '%064d  %s\n' 0 "$file"; else printf '%s  %s\n' "$NORMFLOW_TEST_UV_SHA256" "$file"; fi ;;
  *) if [ -n "${NORMFLOW_REAL_SHASUM:-}" ]; then exec "$NORMFLOW_REAL_SHASUM" "$@"; else exec "$NORMFLOW_REAL_SHA256SUM" "$@"; fi ;;
esac
""",
    )
    _executable(
        fake_bin / "tar",
        """#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$NORMFLOW_TEST_TAR_RECORD"
destination=
expect_destination=0
for argument do
  if [ "$expect_destination" = 1 ]; then destination=$argument; expect_destination=0
  elif [ "$argument" = -C ]; then expect_destination=1; fi
done
case " $* " in
  *uv-*) mkdir -p "$destination/uv-$NORMFLOW_TEST_UV_TARGET"; cp "$NORMFLOW_TEST_UV" "$destination/uv-$NORMFLOW_TEST_UV_TARGET/uv"; chmod +x "$destination/uv-$NORMFLOW_TEST_UV_TARGET/uv" ;;
  *model*) exit 0 ;;
  *) command tar "$@" ;;
esac
""",
    )
    _executable(
        fake_bin / "mv",
        """#!/bin/sh
set -eu
if [ "${NORMFLOW_TEST_FAIL_DURABLE_MOVE:-}" = 1 ]; then
  case "${1:-}:${2:-}" in */runtime:*/runtimes/*) exit 1 ;; esac
fi
exec "$NORMFLOW_REAL_MV" "$@"
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
       for argument do case "$argument" in *.whl) wheel=$argument ;; esac; done
       version=$(basename "$wheel" | sed 's/^normflow-//; s/-py3-none-any.whl$//')
       cat > "$environment/bin/normflow" <<'COMMAND'
#!/bin/sh
printf '%s\n' "$*" >> "$NORMFLOW_TEST_NORMFLOW_RECORD"
case "$1" in --version|-V) echo "VERSION" ;; ui) exit 0 ;; esac
COMMAND
       sed -i.bak "s/VERSION/$version/" "$environment/bin/normflow"
       rm "$environment/bin/normflow.bak"
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
        "NORMFLOW_TEST_UV_TARGET": uv_target,
        "NORMFLOW_TEST_UV_SHA256": UV_ARCHIVE_SHA256[uv_target],
        "NORMFLOW_REAL_SHASUM": real_shasum or "",
        "NORMFLOW_REAL_SHA256SUM": real_sha256sum or "",
        "NORMFLOW_TEST_RECORD": str(record),
        "NORMFLOW_TEST_NORMFLOW_RECORD": str(tmp_path / "normflow-record"),
        "NORMFLOW_TEST_CURL_RECORD": str(tmp_path / "curl-record"),
        "NORMFLOW_TEST_TAR_RECORD": str(tmp_path / "tar-record"),
        "NORMFLOW_REAL_MV": real_mv,
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


def test_install_sh_reports_a_healthy_target_release_as_current_without_reinstalling(
    tmp_path: Path,
):
    environment, record = _installer_environment(tmp_path)

    installed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert installed.returncode == 0, installed.stderr
    record.unlink()
    (tmp_path / "curl-record").unlink()
    (tmp_path / "tar-record").unlink()
    executable = tmp_path / "user-bin" / "normflow"
    original_target = executable.readlink()

    rerun = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert rerun.returncode == 0, rerun.stderr
    assert "already current" in rerun.stdout
    assert executable.readlink() == original_target
    assert record.read_text(encoding="utf-8").startswith("-c ")
    assert "pip install" not in record.read_text(encoding="utf-8")
    assert (tmp_path / "curl-record").read_text(encoding="utf-8").splitlines() == [
        "normflow-payload-linux-x86_64-py313.json"
    ]
    assert not (tmp_path / "tar-record").exists()


def test_install_sh_keeps_the_previous_release_callable_when_an_upgrade_fails(
    tmp_path: Path,
):
    environment, _record = _installer_environment(tmp_path, version="0.1.0")
    installed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert installed.returncode == 0, installed.stderr
    original_target = (tmp_path / "user-bin" / "normflow").resolve()

    _release_assets(
        tmp_path / "assets", "linux-x86_64-py313", version="0.2.0", model=b"new-model"
    )
    environment["NORMFLOW_TEST_FAIL_SMOKE"] = "1"
    failed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    executable = tmp_path / "user-bin" / "normflow"
    assert failed.returncode != 0
    assert executable.resolve() == original_target
    environment.pop("NORMFLOW_TEST_FAIL_SMOKE")
    assert subprocess.run([executable, "--version"], env=environment).returncode == 0
    assert not (tmp_path / "data" / "normflow" / "releases" / "0.2.0").exists()
    assert not list((tmp_path / "data" / "normflow").glob("*.staging-*"))


def test_install_sh_keeps_active_release_callable_when_durable_activation_fails(
    tmp_path: Path,
):
    environment, _record = _installer_environment(tmp_path, version="0.1.0")
    installed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert installed.returncode == 0, installed.stderr
    executable = tmp_path / "user-bin" / "normflow"
    original_target = executable.resolve()

    _release_assets(tmp_path / "assets", "linux-x86_64-py313", version="0.2.0")
    environment["NORMFLOW_TEST_FAIL_DURABLE_MOVE"] = "1"
    failed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert failed.returncode != 0
    assert executable.resolve() == original_target
    assert subprocess.run([executable, "--version"], env=environment).returncode == 0
    assert not (tmp_path / "data" / "normflow" / "releases" / "0.2.0").exists()


def test_install_sh_repairs_a_damaged_target_release_transactionally(tmp_path: Path):
    environment, record = _installer_environment(tmp_path)
    installed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert installed.returncode == 0, installed.stderr

    runtime = tmp_path / "data" / "normflow" / "releases" / "0.1.0"
    (runtime / "bin" / "normflow").unlink()
    record.unlink()

    repaired = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert repaired.returncode == 0, repaired.stderr
    assert (runtime / "bin" / "normflow").is_file()
    assert not (runtime / "runtime").exists()
    assert "pip install" in record.read_text(encoding="utf-8")


def test_install_sh_repairs_an_executable_target_that_fails_version_smoke(
    tmp_path: Path,
):
    environment, record = _installer_environment(tmp_path)
    installed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert installed.returncode == 0, installed.stderr

    runtime = tmp_path / "data" / "normflow" / "releases" / "0.1.0"
    _executable(runtime / "bin" / "normflow", "#!/bin/sh\nexit 1\n")
    record.unlink()

    repaired = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert repaired.returncode == 0, repaired.stderr
    assert "pip install" in record.read_text(encoding="utf-8")
    assert subprocess.run([runtime / "bin" / "normflow", "--version"], env=environment).returncode == 0


def test_install_sh_refuses_to_downgrade_a_newer_managed_release(tmp_path: Path):
    environment, _record = _installer_environment(tmp_path, version="0.2.0")
    installed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert installed.returncode == 0, installed.stderr
    original_target = (tmp_path / "user-bin" / "normflow").resolve()

    _release_assets(tmp_path / "assets", "linux-x86_64-py313", version="0.1.0")
    failed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    executable = tmp_path / "user-bin" / "normflow"
    assert failed.returncode != 0
    assert "newer managed release" in failed.stderr
    assert executable.resolve() == original_target
    assert not (tmp_path / "data" / "normflow" / "releases" / "0.1.0").exists()


def test_install_sh_refuses_to_replace_a_final_release_with_a_prerelease(
    tmp_path: Path,
):
    environment, _record = _installer_environment(tmp_path, version="1.0.0")
    installed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert installed.returncode == 0, installed.stderr
    original_target = (tmp_path / "user-bin" / "normflow").resolve()

    _release_assets(tmp_path / "assets", "linux-x86_64-py313", version="1.0.0-rc.1")
    failed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert failed.returncode != 0
    assert "newer managed release" in failed.stderr
    assert (tmp_path / "user-bin" / "normflow").resolve() == original_target


def test_install_sh_reuses_an_exact_verified_model_bundle_when_repairing(
    tmp_path: Path,
):
    environment, _record = _installer_environment(tmp_path)
    installed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert installed.returncode == 0, installed.stderr

    runtime = tmp_path / "data" / "normflow" / "releases" / "0.1.0"
    (runtime / "bin" / "normflow").unlink()
    (tmp_path / "curl-record").unlink()

    repaired = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert repaired.returncode == 0, repaired.stderr
    downloads = (tmp_path / "curl-record").read_text(encoding="utf-8")
    assert "model-all-MiniLM" not in downloads
    assert (runtime / "bin" / "normflow").is_file()


def test_install_sh_stages_and_switches_model_data_when_bundle_changes(
    tmp_path: Path,
):
    environment, _record = _installer_environment(tmp_path, model=b"old-model")
    installed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert installed.returncode == 0, installed.stderr

    _release_assets(
        tmp_path / "assets", "linux-x86_64-py313", version="0.1.0", model=b"new-model"
    )
    (tmp_path / "curl-record").unlink()
    upgraded = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    runtime = tmp_path / "data" / "normflow" / "releases" / "0.1.0"
    assert upgraded.returncode == 0, upgraded.stderr
    assert "model-all-MiniLM" in (tmp_path / "curl-record").read_text(encoding="utf-8")
    model_checksum = hashlib.sha256(b"new-model").hexdigest()
    assert (runtime / "share" / "normflow" / "model-identity").read_text(
        encoding="utf-8"
    ).endswith(f" {model_checksum}\n")


def test_install_sh_keeps_active_release_when_an_upgrade_checksum_mismatches(
    tmp_path: Path,
):
    environment, _record = _installer_environment(tmp_path, version="0.1.0")
    installed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert installed.returncode == 0, installed.stderr
    original_target = (tmp_path / "user-bin" / "normflow").resolve()

    _release_assets(tmp_path / "assets", "linux-x86_64-py313", version="0.2.0")
    manifest = tmp_path / "assets" / "normflow-payload-linux-x86_64-py313.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("ba599261", "00000000"),
        encoding="utf-8",
    )
    failed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert failed.returncode != 0
    assert "wheel checksum verification failed" in failed.stderr
    assert (tmp_path / "user-bin" / "normflow").resolve() == original_target
    assert not (tmp_path / "data" / "normflow" / "releases" / "0.2.0").exists()


@pytest.mark.parametrize(
    ("system", "machine", "getconf", "macos_version", "message"),
    [
        ("Darwin", "arm64", "", "13.0", "macOS 14"),
        ("Darwin", "x86_64", "", "14.0", "Apple Silicon"),
        ("Linux", "aarch64", "", "14.0", "Linux ARM"),
        ("Linux", "x86_64", "exit 1", "14.0", "musl/Alpine"),
        ("MINGW64_NT", "x86_64", "", "14.0", "supports only"),
    ],
)
def test_install_sh_rejects_unsupported_platform_before_downloading(
    tmp_path: Path,
    system: str,
    machine: str,
    getconf: str,
    macos_version: str,
    message: str,
):
    environment, _record = _installer_environment(
        tmp_path,
        system=system,
        machine=machine,
        macos_version=macos_version,
    )
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


def test_install_sh_installs_a_verified_macos_release(tmp_path: Path):
    environment, record = _installer_environment(
        tmp_path,
        platform="macos-aarch64-py313",
        system="Darwin",
        machine="arm64",
        uv_target="aarch64-apple-darwin",
    )

    result = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "user-bin" / "normflow").is_symlink()
    assert "uv-aarch64-apple-darwin.tar.gz" in (
        tmp_path / "curl-record"
    ).read_text(encoding="utf-8")
    assert "python install 3.13" in record.read_text(encoding="utf-8")


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


def test_install_sh_rejects_a_corrupted_uv_bootstrap_before_extraction(
    tmp_path: Path,
):
    environment, _record = _installer_environment(tmp_path)
    environment["NORMFLOW_TEST_CORRUPT_UV"] = "1"

    result = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "uv bootstrap checksum verification failed" in result.stderr
    assert not (tmp_path / "tar-record").exists()
    assert not (
        tmp_path / "data" / "normflow" / "uv" / "0.6.14" / "bin" / "uv"
    ).exists()


@pytest.mark.parametrize("version", [".", ".."])
def test_install_sh_rejects_dot_path_versions(tmp_path: Path, version: str):
    environment, _record = _installer_environment(tmp_path)
    manifest = tmp_path / "assets" / "normflow-payload-linux-x86_64-py313.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('"0.1.0"', f'"{version}"'),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "invalid version" in result.stderr
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
