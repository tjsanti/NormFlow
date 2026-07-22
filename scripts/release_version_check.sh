#!/bin/sh
set -eu
# Verify wheel metadata version and CLI version flags match expected version.
# Usage: scripts/release_version_check.sh <wheel_path> <expected_version>

wheel="$1"
expected_version="$2"

actual_version=$(python3 -c "
import zipfile
with zipfile.ZipFile('$wheel') as zf:
    for name in zf.namelist():
        if name.endswith('.dist-info/METADATA'):
            data = zf.read(name).decode()
            for line in data.splitlines():
                if line.startswith('Version: '):
                    print(line.split(':', 1)[1].strip())
                    break
            break
")

if [ "$actual_version" != "$expected_version" ]; then
    echo "error: wheel reports version $actual_version, expected $expected_version" >&2
    exit 1
fi

cli_version=$(UV_PYTHON_INSTALL_DIR="$(mktemp -d)" uv run --python 3.13 --frozen \
    normflow --version 2>/dev/null || true)
cli_version_long=$(UV_PYTHON_INSTALL_DIR="$(mktemp -d)" uv run --python 3.13 --frozen \
    normflow -V 2>/dev/null || true)
if [ "$cli_version" != "$expected_version" ]; then
    echo "error: normflow --version reports $cli_version, expected $expected_version" >&2
    exit 1
fi
if [ "$cli_version_long" != "$expected_version" ]; then
    echo "error: normflow -V reports $cli_version_long, expected $expected_version" >&2
    exit 1
fi
