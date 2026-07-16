"""Release-payload command contract tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile


ROOT = Path(__file__).parents[1]
VERSION = "0.1.0"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MODEL_BUNDLE = f"all-MiniLM-L6-v2-{REVISION}"
MODEL_SOURCE_PROVENANCE = "normflow-model-source.json"
MODEL_FILES = {
    "1_Pooling/config.json": "{}",
    "config.json": "{}",
    "config_sentence_transformers.json": "{}",
    "model.safetensors": "model weights",
    "modules.json": "[]",
    "sentence_bert_config.json": "{}",
    "special_tokens_map.json": "{}",
    "tokenizer.json": "{}",
    "tokenizer_config.json": "{}",
    "vocab.txt": "token\n",
}


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _release_checkout(
    tmp_path: Path, *, use_maintained_lock: bool = False
) -> tuple[Path, Path, dict[str, str]]:
    checkout = tmp_path / "checkout"
    shutil.copytree(
        ROOT,
        checkout,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            ".venv",
            "__pycache__",
            "dist",
            "node_modules",
            "static",
        ),
    )

    model_source = tmp_path / "model-source"
    for name, contents in MODEL_FILES.items():
        path = model_source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    (model_source / MODEL_SOURCE_PROVENANCE).write_text(
        json.dumps(
            {
                "model": {
                    "repository": "sentence-transformers/all-MiniLM-L6-v2",
                    "revision": REVISION,
                    "identity": (
                        f"sentence-transformers/all-MiniLM-L6-v2@{REVISION}"
                    ),
                    "bundle": MODEL_BUNDLE,
                },
                "files": {
                    name: hashlib.sha256(contents.encode()).hexdigest()
                    for name, contents in MODEL_FILES.items()
                },
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        checkout / "scripts" / "build-wheel",
        f"""#!{sys.executable}
from pathlib import Path
import sys
import zipfile

output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
wheel = output / "normflow-{VERSION}-py3-none-any.whl"
with zipfile.ZipFile(wheel, "w") as archive:
    archive.writestr("normflow/__init__.py", "")
    archive.writestr("normflow/static/index.html", "<title>NormFlow</title>")
    archive.writestr("normflow/static/assets/app.js", "")
    archive.writestr("normflow/static/assets/app.css", "")
    archive.writestr(
        "normflow-{VERSION}.dist-info/METADATA",
        "Metadata-Version: 2.4\\nName: normflow\\nVersion: {VERSION}\\n",
    )
""",
    )
    real_uv = shutil.which("uv")
    assert real_uv is not None
    export_behavior = (
        f'os.execv("{real_uv}", ["{real_uv}", *sys.argv[1:]])'
        if use_maintained_lock
        else 'print("sentence-transformers==5.6.0")\n    print("torch==2.12.1+cpu")'
    )
    fake_smoke_python = f"""#!{sys.executable}
import os
from pathlib import Path
Path(os.environ["NORMFLOW_TEST_SMOKE_RECORD"]).write_text("\\n".join([
    os.environ.get("HF_HUB_OFFLINE", ""),
    os.environ.get("TRANSFORMERS_OFFLINE", ""),
    os.environ.get("NORMFLOW_DISABLE_NETWORK", ""),
]))
"""
    _write_executable(
        fake_bin / "uv",
        f"""#!{sys.executable}
import os
from pathlib import Path
import sys

if sys.argv[1] == "export":
    {export_behavior}
elif sys.argv[1] == "venv":
    python = Path(sys.argv[-1]) / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text({fake_smoke_python!r})
    python.chmod(0o755)
elif sys.argv[1:3] == ["pip", "install"]:
    if "--index-strategy" not in sys.argv or "unsafe-best-match" not in sys.argv:
        raise SystemExit("CPU index must coexist with the primary package index")
    Path(os.environ["NORMFLOW_TEST_INSTALL_RECORD"]).write_text("installed")
elif sys.argv[1] == "run":
    record = Path(os.environ["NORMFLOW_TEST_SMOKE_RECORD"])
    record.write_text("\\n".join([
        os.environ.get("HF_HUB_OFFLINE", ""),
        os.environ.get("TRANSFORMERS_OFFLINE", ""),
        os.environ.get("NORMFLOW_DISABLE_NETWORK", ""),
    ]))
else:
    raise SystemExit(f"unexpected uv command: {{sys.argv}}")
""",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["NORMFLOW_MODEL_SOURCE"] = str(model_source)
    environment["NORMFLOW_TEST_SMOKE_RECORD"] = str(tmp_path / "smoke-record")
    environment["NORMFLOW_TEST_INSTALL_RECORD"] = str(tmp_path / "install-record")
    environment["UV_CACHE_DIR"] = str(tmp_path / "uv-cache")
    return checkout, model_source, environment


def test_release_payload_command_emits_one_versioned_payload(tmp_path: Path):
    checkout, _model_source, environment = _release_checkout(tmp_path)
    output = tmp_path / "payload"

    subprocess.run(
        [str(checkout / "scripts" / "build-release-payload"), str(output)],
        cwd=checkout,
        env=environment,
        check=True,
    )

    wheels = list(output.glob("normflow-*.whl"))
    constraints = list(output.glob("normflow-*-constraints-*.txt"))
    models = list(output.glob("normflow-*-model-*.tar.gz"))
    assert len(wheels) == len(constraints) == len(models) == 1
    manifest = json.loads((output / f"normflow-{VERSION}-payload.json").read_text())
    assert {path.name for path in output.iterdir()} == {
        f"normflow-{VERSION}-payload.json",
        *(asset["filename"] for asset in manifest["assets"]),
    }
    assert manifest["version"] == VERSION
    assert manifest["model"]["revision"] == REVISION
    assert manifest["model"]["bundle"] == MODEL_BUNDLE
    assert manifest["dependency_backend"] == "cpu"
    assert {asset["kind"] for asset in manifest["assets"]} == {
        "wheel",
        "constraints",
        "model",
    }
    for asset in manifest["assets"]:
        path = output / asset["filename"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == asset["sha256"]
    assert (tmp_path / "smoke-record").read_text().splitlines() == [
        "1",
        "1",
        "1",
    ]
    assert (tmp_path / "install-record").read_text() == "installed"


def test_release_payload_command_has_a_nonconflicting_default_output(tmp_path: Path):
    checkout, _model_source, environment = _release_checkout(tmp_path)

    subprocess.run(
        [str(checkout / "scripts" / "build-release-payload")],
        cwd=checkout,
        env=environment,
        check=True,
    )

    output = checkout / "dist" / "release-payload"
    manifest = json.loads((output / f"normflow-{VERSION}-payload.json").read_text())
    assert {path.name for path in output.iterdir()} == {
        f"normflow-{VERSION}-payload.json",
        *(asset["filename"] for asset in manifest["assets"]),
    }
    assert not list(output.glob("*.tar"))
    assert not list(output.glob("*.zip"))


def test_release_payload_exports_the_maintained_cpu_only_lock(tmp_path: Path):
    checkout, _model_source, environment = _release_checkout(
        tmp_path, use_maintained_lock=True
    )
    output = tmp_path / "payload"

    subprocess.run(
        [str(checkout / "scripts" / "build-release-payload"), str(output)],
        cwd=checkout,
        env=environment,
        check=True,
    )

    constraints_path = next(output.glob("normflow-*-constraints-*.txt"))
    constraints = constraints_path.read_text(encoding="utf-8").lower()
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in constraints
    assert re.search(r"(?m)^torch==[^\s;]+\+cpu(?:\s|;|$)", constraints)
    assert not any(
        term in constraints
        for term in (
            "cuda",
            "cudnn",
            "nvidia",
            "rocm",
            "triton",
            "xpu",
        )
    )
    assert not re.search(r"(?m)^colorama==", constraints)


def test_model_bundle_contains_only_inference_files_and_licensed_identity(
    tmp_path: Path,
):
    checkout, _model_source, environment = _release_checkout(tmp_path)
    output = tmp_path / "payload"
    subprocess.run(
        [str(checkout / "scripts" / "build-release-payload"), str(output)],
        cwd=checkout,
        env=environment,
        check=True,
    )

    model_archive = next(output.glob("normflow-*-model-*.tar.gz"))
    with tarfile.open(model_archive, "r:gz") as archive:
        names = {name.rstrip("/") for name in archive.getnames()}
        model_manifest = json.loads(
            archive.extractfile(f"{MODEL_BUNDLE}/normflow-model.json").read()
        )
        license_text = archive.extractfile(f"{MODEL_BUNDLE}/LICENSE").read().decode()
        attribution = archive.extractfile(
            f"{MODEL_BUNDLE}/ATTRIBUTION.md"
        ).read().decode()

    expected_files = {
        f"{MODEL_BUNDLE}/{name}" for name in MODEL_FILES
    } | {
        f"{MODEL_BUNDLE}/LICENSE",
        f"{MODEL_BUNDLE}/ATTRIBUTION.md",
        f"{MODEL_BUNDLE}/normflow-model.json",
    }
    assert names == expected_files | {MODEL_BUNDLE, f"{MODEL_BUNDLE}/1_Pooling"}
    assert model_manifest["payload_version"] == VERSION
    assert model_manifest["identity"] == (
        f"sentence-transformers/all-MiniLM-L6-v2@{REVISION}"
    )
    assert model_manifest["license"] == "Apache-2.0"
    assert "Apache License" in license_text
    assert REVISION in attribution


def test_release_payload_failure_leaves_no_partial_output(tmp_path: Path):
    checkout, model_source, environment = _release_checkout(tmp_path)
    (model_source / "model.safetensors").unlink()
    output = tmp_path / "payload"

    result = subprocess.run(
        [str(checkout / "scripts" / "build-release-payload"), str(output)],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "pinned model snapshot is incomplete" in result.stderr
    assert not output.exists()


def test_model_source_override_requires_verified_provenance(tmp_path: Path):
    checkout, model_source, environment = _release_checkout(tmp_path)
    (model_source / MODEL_SOURCE_PROVENANCE).unlink()
    output = tmp_path / "payload"

    result = subprocess.run(
        [str(checkout / "scripts" / "build-release-payload"), str(output)],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "model source provenance" in result.stderr
    assert not output.exists()


def test_model_source_override_rejects_bytes_not_bound_by_provenance(
    tmp_path: Path,
):
    checkout, model_source, environment = _release_checkout(tmp_path)
    (model_source / "model.safetensors").write_text("untrusted model weights")
    output = tmp_path / "payload"

    result = subprocess.run(
        [str(checkout / "scripts" / "build-release-payload"), str(output)],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "provenance does not match its contents" in result.stderr
    assert not output.exists()


def test_release_payload_rejects_drift_in_declared_model_identity(tmp_path: Path):
    checkout, _model_source, environment = _release_checkout(tmp_path)
    identity_source = checkout / "src" / "normflow" / "embedding_model.py"
    identity_source.write_text(
        identity_source.read_text().replace(
            'EMBEDDING_MODEL_BUNDLE = f"all-MiniLM-L6-v2-{EMBEDDING_MODEL_REVISION}"',
            'EMBEDDING_MODEL_BUNDLE = "drifted-model-bundle"',
        )
    )
    output = tmp_path / "payload"

    result = subprocess.run(
        [str(checkout / "scripts" / "build-release-payload"), str(output)],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "declared embedding-model identity is inconsistent" in result.stderr
    assert not output.exists()


def test_release_smoke_command_reports_usage_without_a_traceback():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "smoke_release_payload.py")],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "usage: smoke_release_payload.py MODEL_ARCHIVE VERSION" in result.stderr
    assert "Traceback" not in result.stderr


def test_release_smoke_command_normalizes_smoke_failures(tmp_path: Path):
    environment = os.environ.copy()
    environment.pop("NORMFLOW_DISABLE_NETWORK", None)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "smoke_release_payload.py"),
            str(tmp_path / "missing-model.tar.gz"),
            VERSION,
        ],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "release smoke failed: release smoke tests require disabled network" in (
        result.stderr
    )
    assert "Traceback" not in result.stderr
