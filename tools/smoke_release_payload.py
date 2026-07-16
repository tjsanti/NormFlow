"""Network-disabled smoke checks for a completed release payload."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import tarfile
import tempfile


def _disabled(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("network access is disabled during release smoke tests")


def main() -> None:
    if os.environ.get("NORMFLOW_DISABLE_NETWORK") != "1":
        raise RuntimeError("release smoke tests require disabled network access")
    socket.create_connection = _disabled  # type: ignore[assignment]
    socket.getaddrinfo = _disabled  # type: ignore[assignment]
    socket.socket.connect = _disabled  # type: ignore[method-assign]

    model_archive = Path(sys.argv[1]).resolve()
    version = sys.argv[2]

    import torch
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from normflow.api import create_app
    from normflow.cli import app
    from normflow.embedding_model import EMBEDDING_MODEL_BUNDLE, load_embedding_model
    from normflow.project import project_at
    from normflow.project_service import init_project

    assert torch.version.cuda is None
    assert not torch.cuda.is_available()
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == version

    with tempfile.TemporaryDirectory(prefix="normflow-smoke-") as temp:
        temporary = Path(temp)
        project_root = init_project(temporary / "project")
        with TestClient(create_app(project_at(project_root))) as client:
            response = client.get("/")
            assert response.status_code == 200
            assert "<title>NormFlow</title>" in response.text

        with tarfile.open(model_archive, "r:gz") as archive:
            archive.extractall(temporary / "model", filter="data")
        model = load_embedding_model(temporary / "model" / EMBEDDING_MODEL_BUNDLE)
        encoded = model.encode(["NormFlow release smoke test"], normalize_embeddings=True)
        assert len(encoded) == 1


if __name__ == "__main__":
    main()
