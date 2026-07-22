#!/bin/sh
set -eu
# Smoke test: verify NormFlow loads, answers API requests, and runs offline.
# Shared between install.sh and CI installer smoke tests.
# Usage: scripts/release_smoke_test.sh <staging_python> [test_label]

staging_python="${1:?staging python path required}"
label="${2:-smoke test}"

# Derive the staging directory from the python interpreter path.
staging_dir=$(cd "$(dirname "$staging_python")/.." && pwd)
normflow="$staging_dir/bin/normflow"

# Quick CLI version check
"$normflow" --version > /dev/null 2>&1 || exit 1
"$normflow" -V > /dev/null 2>&1 || exit 1

# Offline integration smoke test
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 NORMFLOW_DISABLE_NETWORK=1 \
    "$staging_python" -c "
from fastapi.testclient import TestClient
from normflow.api import create_app
from normflow.embedding_model import load_embedding_model
from normflow.project import project_at
from normflow.project_service import init_project
from pathlib import Path
import tempfile
with tempfile.TemporaryDirectory(prefix='normflow-smoke-') as tmp:
    project = init_project(Path(tmp) / 'project')
    with TestClient(create_app(project_at(project))) as client:
        assert client.get('/').status_code == 200
    result = load_embedding_model().encode(['${label}'], normalize_embeddings=True)
    assert len(result) == 1
"

echo "smoke test passed"
