#!/bin/sh
# Install a self-contained, CPU-only NormFlow release for a supported desktop.
set -eu

RELEASE_URL=${NORMFLOW_RELEASE_URL:-https://github.com/tjsanti/NormFlow/releases/latest/download}
UV_VERSION=0.6.14
PYTHON_VERSION=3.13
APP_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}/normflow
BIN_DIR=${XDG_BIN_HOME:-"$HOME/.local/bin"}
TEMP_DIR=

fail() {
    printf '%s\n' "normflow installer: $*" >&2
    exit 1
}

cleanup() {
    [ -z "$TEMP_DIR" ] || rm -rf "$TEMP_DIR"
}

trap cleanup EXIT HUP INT TERM

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "requires $1; install it and try again"
}

detect_platform() {
    system=$(uname -s 2>/dev/null || true)
    machine=$(uname -m 2>/dev/null || true)
    case "$system:$machine" in
        Darwin:arm64)
            require_command sw_vers
            macos_version=$(sw_vers -productVersion 2>/dev/null || true)
            case "$macos_version" in
                1[4-9].*|[2-9][0-9].*) PLATFORM=macos-aarch64-py313 ;;
                *) fail "requires Apple Silicon macOS 14 or newer (found macOS ${macos_version:-unknown})" ;;
            esac
            UV_TARGET=aarch64-apple-darwin
            ;;
        Darwin:*) fail "requires Apple Silicon macOS 14 or newer; Intel macOS is not supported" ;;
        Linux:x86_64|Linux:amd64)
            require_command getconf
            getconf GNU_LIBC_VERSION >/dev/null 2>&1 || fail "requires x86-64 glibc Linux; musl/Alpine is not supported"
            PLATFORM=linux-x86_64-py313
            UV_TARGET=x86_64-unknown-linux-gnu
            ;;
        Linux:*) fail "requires x86-64 glibc Linux; Linux ARM and musl/Alpine are not supported" ;;
        *) fail "supports only Apple Silicon macOS 14+ and x86-64 glibc Linux" ;;
    esac
}

select_sha256() {
    if command -v shasum >/dev/null 2>&1; then
        SHA256='shasum -a 256'
    elif command -v sha256sum >/dev/null 2>&1; then
        SHA256=sha256sum
    else
        fail "requires shasum -a 256 or sha256sum to verify release files"
    fi
}

sha256() {
    # Both supported implementations write the digest as the first field.
    $SHA256 "$1" | awk '{print $1}'
}

download() {
    curl --fail --silent --show-error --location --output "$2" "$1"
}

asset() {
    requested_kind=$1
    awk -v requested_kind="$requested_kind" '
        /^[[:space:]]*\{/ { filename=""; kind=""; digest="" }
        /"filename"[[:space:]]*:/ { value=$0; sub(/.*"filename"[[:space:]]*:[[:space:]]*"/, "", value); sub(/".*/, "", value); filename=value }
        /"kind"[[:space:]]*:/ { value=$0; sub(/.*"kind"[[:space:]]*:[[:space:]]*"/, "", value); sub(/".*/, "", value); kind=value }
        /"sha256"[[:space:]]*:/ { value=$0; sub(/.*"sha256"[[:space:]]*:[[:space:]]*"/, "", value); sub(/".*/, "", value); digest=value }
        /^[[:space:]]*\}[,]?[[:space:]]*$/ { if (kind == requested_kind) print filename " " digest; filename=""; kind=""; digest="" }
    ' "$MANIFEST"
}

read_asset() {
    kind=$1
    record=$(asset "$kind")
    [ "$(printf '%s\n' "$record" | wc -l | tr -d ' ')" = 1 ] || fail "release manifest must contain one $kind asset"
    filename=${record%% *}
    digest=${record#* }
    case "$filename" in
        ''|*'/'*|*'..'*|*' '*) fail "release manifest has an invalid $kind asset" ;;
    esac
    case "$digest" in
        *[!0123456789abcdef]*|'') fail "release manifest has an invalid $kind checksum" ;;
    esac
    [ "${#digest}" = 64 ] || fail "release manifest has an invalid $kind checksum"
    printf '%s\n%s\n' "$filename" "$digest"
}

download_verified_asset() {
    kind=$1
    record=$(read_asset "$kind") || exit $?
    set -- $record
    [ "$#" = 2 ] || fail "release manifest has an invalid $kind asset"
    filename=$1
    expected=$2
    destination="$TEMP_DIR/$filename"
    download "$RELEASE_URL/$filename" "$destination"
    actual=$(sha256 "$destination")
    [ "$actual" = "$expected" ] || fail "$kind checksum verification failed"
    printf '%s\n' "$destination"
}

install_uv() {
    UV="$APP_HOME/uv/$UV_VERSION/bin/uv"
    [ -x "$UV" ] && return
    mkdir -p "$(dirname "$UV")"
    archive="$TEMP_DIR/uv-$UV_VERSION-$UV_TARGET.tar.gz"
    download "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_TARGET.tar.gz" "$archive"
    tar -xzf "$archive" -C "$TEMP_DIR"
    candidate="$TEMP_DIR/uv-$UV_TARGET/uv"
    [ -x "$candidate" ] || fail "the pinned uv bootstrap archive is invalid"
    mv "$candidate" "$UV"
    chmod 755 "$UV"
}

update_path() {
    case ":$PATH:" in
        *":$BIN_DIR:"*) return ;;
    esac
    case ${SHELL:-} in
        */zsh) shell_config=$HOME/.zshrc ;;
        */bash) shell_config=$HOME/.bashrc ;;
        *) return ;;
    esac
    marker='# Added by NormFlow installer'
    grep -F "$marker" "$shell_config" >/dev/null 2>&1 || {
        mkdir -p "$(dirname "$shell_config")"
        {
            printf '\n%s\n' "$marker"
            printf 'export PATH="%s:$PATH"\n' "$BIN_DIR"
        } >> "$shell_config"
    }
    NEW_TERMINAL=1
}

smoke_test() {
    "$RUNTIME/bin/normflow" --version >/dev/null
    "$RUNTIME/bin/normflow" -V >/dev/null
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 NORMFLOW_DISABLE_NETWORK=1 \
        "$RUNTIME/bin/python" -c '
from fastapi.testclient import TestClient
from normflow.api import create_app
from normflow.embedding_model import load_embedding_model
from normflow.project import project_at
from normflow.project_service import init_project
from pathlib import Path
import tempfile
with tempfile.TemporaryDirectory(prefix="normflow-install-smoke-") as temporary:
    project = init_project(Path(temporary) / "project")
    with TestClient(create_app(project_at(project))) as client:
        assert client.get("/").status_code == 200
    assert len(load_embedding_model().encode(["NormFlow installer smoke test"], normalize_embeddings=True)) == 1
'
}

main() {
    detect_platform
    require_command curl
    require_command tar
    require_command awk
    select_sha256
    TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/normflow-install.XXXXXX") || fail "could not create a temporary directory"
    MANIFEST="$TEMP_DIR/normflow-payload-$PLATFORM.json"
    download "$RELEASE_URL/normflow-payload-$PLATFORM.json" "$MANIFEST"
    version=$(awk -F '"' '/"version"[[:space:]]*:/ { print $4; exit }' "$MANIFEST")
    case "$version" in ''|*[!0-9A-Za-z.+-]*) fail "release manifest has an invalid version" ;; esac
    manifest_platform=$(awk -F '"' '/"platform"[[:space:]]*:/ { print $4; exit }' "$MANIFEST")
    [ "$manifest_platform" = "$PLATFORM" ] || fail "release manifest is not for $PLATFORM"

    wheel=$(download_verified_asset wheel)
    constraints=$(download_verified_asset constraints)
    model=$(download_verified_asset model)
    install_uv

    release_runtime="$APP_HOME/releases/$version"
    RUNTIME=$release_runtime
    if [ ! -x "$RUNTIME/bin/normflow" ]; then
        staging="$TEMP_DIR/runtime"
        UV_PYTHON_INSTALL_DIR="$APP_HOME/python" "$UV" python install "$PYTHON_VERSION"
        UV_PYTHON_INSTALL_DIR="$APP_HOME/python" "$UV" venv --python "$PYTHON_VERSION" "$staging"
        "$UV" pip install --python "$staging/bin/python" --constraint "$constraints" --torch-backend cpu "$wheel"
        mkdir -p "$staging/share/normflow/models"
        tar -xzf "$model" -C "$staging/share/normflow/models"
        RUNTIME=$staging
        smoke_test
        mkdir -p "$(dirname "$release_runtime")"
        mv "$staging" "$release_runtime"
        RUNTIME=$release_runtime
    else
        smoke_test
    fi
    mkdir -p "$BIN_DIR"
    ln -sfn "$RUNTIME/bin/normflow" "$BIN_DIR/normflow"
    NEW_TERMINAL=0
    update_path
    printf 'NormFlow %s installed at %s\n' "$version" "$RUNTIME"
    if [ "$NEW_TERMINAL" = 1 ]; then
        printf 'Open a new terminal, then run: normflow --version\n'
    else
        printf 'Run: normflow --version\n'
    fi
}

main "$@"
