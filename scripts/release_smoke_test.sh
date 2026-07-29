#!/bin/sh
set -eu
# Smoke test: verify NormFlow loads, answers API requests, and runs offline.
# Shared between install.sh and CI installer smoke tests.
# Usage: scripts/release_smoke_test.sh <runtime_python> [test_label]

runtime_python="${1:?runtime python path required}"
label="${2:-smoke test}"

# Derive the installed runtime from the Python interpreter path.
runtime_dir=$(cd "$(dirname "$runtime_python")/.." && pwd)
normflow="$runtime_dir/bin/normflow"

# Quick CLI version check
"$normflow" --version > /dev/null 2>&1 || exit 1
"$normflow" -V > /dev/null 2>&1 || exit 1

# Offline integration smoke test
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 NORMFLOW_DISABLE_NETWORK=1 \
    "$runtime_python" -c "
from fastapi.testclient import TestClient
from normflow.api import create_app
from normflow.embedding_model import load_embedding_model
from normflow.project import project_at
from normflow.project_service import init_project
from normflow.semantic_index import SemanticIndex
from pathlib import Path
import sys
import tempfile
label = sys.argv[1]
with tempfile.TemporaryDirectory(prefix='normflow-smoke-') as tmp:
    project = init_project(Path(tmp) / 'project')
    with TestClient(create_app(project_at(project))) as client:
        assert client.get('/').status_code == 200
    assert len(load_embedding_model().encode([label], normalize_embeddings=True)) == 1
    index = SemanticIndex(str(project))
    assert index.build([(label, 'smoke result')]) == 1
    results = index.search(label, threshold=0.0)
    assert results[0]['normalized_text'] == 'smoke result'
" "$label"

echo "smoke test passed"
