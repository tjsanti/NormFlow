"""Build and validate the complete NormFlow release payload."""

from __future__ import annotations

import ast
from email.parser import BytesParser
from email.policy import default
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).parents[1]
MODEL_INPUTS = (
    "1_Pooling/config.json",
    "config.json",
    "config_sentence_transformers.json",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)
FORBIDDEN_GPU_TERMS = (
    "cuda",
    "cudnn",
    "nvidia",
    "rocm",
    "triton",
    "xpu",
    "/whl/cu",
)


class PayloadError(RuntimeError):
    """The release payload cannot be produced consistently."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _literal_assignments(path: Path) -> dict[str, object]:
    assignments: dict[str, object] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                try:
                    assignments[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return assignments


def _release_identity() -> tuple[str, str, str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    model = _literal_assignments(ROOT / "src/normflow/embedding_model.py")
    repository = model.get("EMBEDDING_MODEL_REPOSITORY")
    revision = model.get("EMBEDDING_MODEL_REVISION")
    if not all(
        isinstance(value, str) and value
        for value in (version, repository, revision)
    ):
        raise PayloadError("release or embedding-model identity is missing")
    bundle = f"all-MiniLM-L6-v2-{revision}"
    return version, repository, revision, bundle


def _platform_tag() -> str:
    system = platform.system().lower()
    if system == "darwin":
        system = "macos"
    if system not in {"linux", "macos"}:
        raise PayloadError(f"unsupported release platform: {platform.system()}")
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    if machine not in {"x86_64", "aarch64"}:
        raise PayloadError(f"unsupported release architecture: {platform.machine()}")
    return f"{system}-{machine}-py{sys.version_info.major}{sys.version_info.minor}"


def _validate_wheel(wheel: Path, version: str) -> None:
    expected = f"normflow-{version}-py3-none-any.whl"
    if wheel.name != expected:
        raise PayloadError(f"wheel filename must be {expected}, got {wheel.name}")
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise PayloadError("wheel must contain exactly one metadata record")
        metadata = BytesParser(policy=default).parsebytes(
            archive.read(metadata_names[0])
        )
        required = {
            "normflow/static/index.html": (
                "normflow/static/index.html" in archive.namelist()
            ),
            "normflow/static/assets/*.js": any(
                name.startswith("normflow/static/assets/") and name.endswith(".js")
                for name in archive.namelist()
            ),
            "normflow/static/assets/*.css": any(
                name.startswith("normflow/static/assets/") and name.endswith(".css")
                for name in archive.namelist()
            ),
        }
    if metadata["Name"] != "normflow" or metadata["Version"] != version:
        raise PayloadError("wheel package metadata does not match the payload identity")
    missing = [name for name, present in required.items() if not present]
    if missing:
        raise PayloadError(f"wheel is missing bundled UI assets: {', '.join(missing)}")


def _build_wheel(staging: Path, version: str) -> Path:
    wheel_dir = staging / "wheel"
    subprocess.run([str(ROOT / "scripts/build-wheel"), str(wheel_dir)], check=True)
    wheels = list(wheel_dir.glob("normflow-*.whl"))
    if len(wheels) != 1:
        raise PayloadError("release build must produce exactly one NormFlow wheel")
    _validate_wheel(wheels[0], version)
    return wheels[0]


def _export_constraints(staging: Path, version: str, platform_tag: str) -> Path:
    result = subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--no-annotate",
            "--no-header",
            "--no-hashes",
            "--emit-index-url",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    constraints = result.stdout.strip() + "\n"
    lowered = constraints.lower()
    leaked = [term for term in FORBIDDEN_GPU_TERMS if term in lowered]
    if leaked:
        raise PayloadError(
            f"GPU dependency leaked into CPU-only constraints: {', '.join(leaked)}"
        )
    if not re.search(r"(?m)^torch==[^\s;]+\+cpu(?:\s|;|$)", constraints):
        raise PayloadError("constraints do not select the CPU-only Torch build")
    unpinned = [
        line
        for line in constraints.splitlines()
        if line and not line.startswith("--") and "==" not in line
    ]
    if unpinned:
        raise PayloadError(f"constraints contain unpinned dependencies: {unpinned[0]}")
    path = staging / f"normflow-{version}-constraints-{platform_tag}.txt"
    path.write_text(constraints, encoding="utf-8")
    return path


def _download_model(repository: str, revision: str, destination: Path) -> Path:
    source_override = os.environ.get("NORMFLOW_MODEL_SOURCE")
    if source_override:
        return Path(source_override).expanduser().resolve()
    script = (
        "from huggingface_hub import snapshot_download; "
        "snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], "
        "local_dir=sys.argv[3], allow_patterns=sys.argv[4:])"
    )
    subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "python",
            "-c",
            "import sys; " + script,
            repository,
            revision,
            str(destination),
            *MODEL_INPUTS,
        ],
        cwd=ROOT,
        check=True,
    )
    return destination


def _model_tree(
    staging: Path,
    version: str,
    repository: str,
    revision: str,
    bundle: str,
) -> Path:
    source = _download_model(repository, revision, staging / "model-download")
    missing = [name for name in MODEL_INPUTS if not (source / name).is_file()]
    if missing:
        raise PayloadError(f"pinned model snapshot is incomplete: {', '.join(missing)}")
    license_path = ROOT / "release/model/LICENSE"
    attribution_path = ROOT / "release/model/ATTRIBUTION.md"
    if "Apache License" not in license_path.read_text(encoding="utf-8"):
        raise PayloadError("embedding-model Apache-2.0 license is missing")
    attribution = attribution_path.read_text(encoding="utf-8")
    if repository not in attribution or revision not in attribution:
        raise PayloadError("embedding-model attribution does not match the pinned identity")

    root = staging / "model-tree" / bundle
    for name in MODEL_INPUTS:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    shutil.copyfile(license_path, root / "LICENSE")
    shutil.copyfile(attribution_path, root / "ATTRIBUTION.md")
    files = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "payload_version": version,
        "repository": repository,
        "revision": revision,
        "identity": f"{repository}@{revision}",
        "bundle": bundle,
        "license": "Apache-2.0",
        "files": files,
    }
    (root / "normflow-model.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return root


def _add_to_tar(archive: tarfile.TarFile, root: Path, path: Path) -> None:
    relative = path.relative_to(root.parent).as_posix()
    info = tarfile.TarInfo(relative + ("/" if path.is_dir() else ""))
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o755 if path.is_dir() else 0o644
    if path.is_dir():
        info.type = tarfile.DIRTYPE
        archive.addfile(info)
    else:
        contents = path.read_bytes()
        info.size = len(contents)
        archive.addfile(info, io.BytesIO(contents))


def _bundle_model(staging: Path, version: str, model_root: Path) -> Path:
    path = staging / f"normflow-{version}-model-{model_root.name}.tar.gz"
    with path.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                _add_to_tar(archive, model_root, model_root)
                for item in sorted(model_root.rglob("*")):
                    _add_to_tar(archive, model_root, item)
    return path


def _smoke(wheel: Path, constraints: Path, model: Path, version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="normflow-payload-smoke-") as temp:
        environment_root = Path(temp) / "environment"
        python = environment_root / "bin/python"
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(environment_root)],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--constraint",
                str(constraints),
                "--index-strategy",
                "unsafe-best-match",
                str(wheel),
            ],
            cwd=ROOT,
            check=True,
        )
        environment = os.environ.copy()
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "NORMFLOW_DISABLE_NETWORK": "1",
            }
        )
        subprocess.run(
            [
                str(python),
                str(ROOT / "tools/smoke_release_payload.py"),
                str(model),
                version,
            ],
            cwd=temp,
            env=environment,
            check=True,
        )


def build(output: Path) -> None:
    output = output.expanduser().resolve()
    if output.exists():
        raise PayloadError(f"output directory already exists: {output}")
    version, repository, revision, bundle = _release_identity()
    platform_tag = _platform_tag()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="normflow-payload-", dir=output.parent
    ) as temp:
        staging = Path(temp) / "payload"
        staging.mkdir()
        wheel = _build_wheel(staging, version)
        constraints = _export_constraints(staging, version, platform_tag)
        model_root = _model_tree(staging, version, repository, revision, bundle)
        model = _bundle_model(staging, version, model_root)
        _smoke(wheel, constraints, model, version)

        assets = []
        for kind, source in (
            ("wheel", wheel),
            ("constraints", constraints),
            ("model", model),
        ):
            destination = staging / source.name
            if source != destination:
                shutil.move(source, destination)
            assets.append(
                {
                    "kind": kind,
                    "filename": destination.name,
                    "sha256": _sha256(destination),
                }
            )
        shutil.rmtree(staging / "wheel")
        manifest = {
            "version": version,
            "platform": platform_tag,
            "dependency_backend": "cpu",
            "dependency_index_strategy": "unsafe-best-match",
            "model": {
                "repository": repository,
                "revision": revision,
                "identity": f"{repository}@{revision}",
                "bundle": bundle,
                "license": "Apache-2.0",
            },
            "assets": assets,
        }
        (staging / f"normflow-{version}-payload.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        shutil.rmtree(staging / "model-tree")
        download = staging / "model-download"
        if download.exists():
            shutil.rmtree(download)
        staging.rename(output)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: tools/release_payload.py OUTPUT_DIRECTORY", file=sys.stderr)
        return 2
    try:
        build(Path(sys.argv[1]))
    except (
        PayloadError,
        OSError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
    ) as error:
        print(f"release payload failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
