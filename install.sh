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
    case "$UV_TARGET" in
        aarch64-apple-darwin)
            expected_sha256=4ea4731010fbd1bc8e790e07f199f55a5c7c2c732e9b77f85e302b0bee61b756
            ;;
        x86_64-unknown-linux-gnu)
            expected_sha256=0aaf451c391d3913823bfb8ed354b446dcfd0553a32ed8266611e4181c61fd51
            ;;
        *) fail "no pinned uv bootstrap is available for $UV_TARGET" ;;
    esac
    mkdir -p "$(dirname "$UV")"
    archive="$TEMP_DIR/uv-$UV_VERSION-$UV_TARGET.tar.gz"
    download "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_TARGET.tar.gz" "$archive"
    actual_sha256=$(sha256 "$archive")
    [ "$actual_sha256" = "$expected_sha256" ] || fail "uv bootstrap checksum verification failed"
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
    "$RUNTIME/bin/normflow" --version >/dev/null || return 1
    "$RUNTIME/bin/normflow" -V >/dev/null || return 1
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
    return $?
}

release_is_current() {
    [ -x "$1/bin/normflow" ] || return 1
    [ -f "$1/share/normflow/model-identity" ] || return 1
    [ "$(cat "$1/share/normflow/model-identity")" = "$MODEL_FILENAME $MODEL_SHA256" ] || return 1
    RUNTIME=$1
    smoke_test
}

installed_release_version() {
    if [ -x "$BIN_DIR/normflow" ]; then
        active_target=$(readlink "$BIN_DIR/normflow" 2>/dev/null || true)
        case "$active_target" in
            "$APP_HOME/current/bin/normflow"|"$APP_HOME"/releases/*/bin/normflow) executable="$BIN_DIR/normflow" ;;
            *) return 0 ;;
        esac
    else
        current_runtime=$(readlink "$APP_HOME/current" 2>/dev/null || true)
        case "$current_runtime" in
            "$APP_HOME"/runtimes/*|"$APP_HOME"/releases/*) ;;
            *) return 0 ;;
        esac
        executable="$current_runtime/bin/normflow"
        [ -x "$executable" ] || return 0
    fi
    installed_version=$("$executable" --version 2>/dev/null || true)
    case "$installed_version" in
        ''|*[!0-9A-Za-z.+-]*) return 0 ;;
    esac
    printf '%s\n' "$installed_version"
}

version_is_newer() {
    awk -v installed="$1" -v requested="$2" '
        function normalized(version) {
            sub(/^v/, "", version)
            sub(/\+.*/, "", version)
            return tolower(version)
        }
        function suffix_rank(suffix) {
            sub(/^[-._]*/, "", suffix)
            if (suffix == "") return 4
            if (suffix ~ /^post/) return 5
            if (suffix ~ /^dev/) return 0
            if (suffix ~ /^(a|alpha)/) return 1
            if (suffix ~ /^(b|beta)/) return 2
            if (suffix ~ /^(c|rc|pre|preview)/) return 3
            return -1
        }
        function suffix_number(suffix) {
            sub(/^[-._]*/, "", suffix)
            sub(/^(post|dev|alpha|beta|preview|pre|rc|a|b|c)[-._]*/, "", suffix)
            return suffix ~ /^[0-9]+$/ ? suffix + 0 : 0
        }
        BEGIN {
            installed = normalized(installed)
            requested = normalized(requested)
            if (!match(installed, /^[0-9]+(\.[0-9]+)*/) ||
                !match(requested, /^[0-9]+(\.[0-9]+)*/)) exit 1

            installed_release = substr(installed, RSTART, RLENGTH)
            installed_suffix = substr(installed, RLENGTH + 1)
            match(requested, /^[0-9]+(\.[0-9]+)*/)
            requested_release = substr(requested, RSTART, RLENGTH)
            requested_suffix = substr(requested, RLENGTH + 1)
            installed_count = split(installed_release, installed_parts, ".")
            requested_count = split(requested_release, requested_parts, ".")
            count = installed_count > requested_count ? installed_count : requested_count
            for (i = 1; i <= count; i++) {
                left = installed_parts[i] + 0
                right = requested_parts[i] + 0
                if (left > right) exit 0
                if (left < right) exit 1
            }
            installed_rank = suffix_rank(installed_suffix)
            requested_rank = suffix_rank(requested_suffix)
            if (installed_rank > requested_rank) exit 0
            if (installed_rank < requested_rank) exit 1
            installed_number = suffix_number(installed_suffix)
            requested_number = suffix_number(requested_suffix)
            if (installed_number > requested_number) exit 0
            if (installed_number < requested_number) exit 1
            exit 1
        }
    '
}

switch_link() {
    target=$1
    destination=$2
    link_candidate="$destination.normflow-new-$$"
    rm -f "$link_candidate"
    ln -s "$target" "$link_candidate" || return 1
    if [ -d "$destination" ] && [ ! -L "$destination" ]; then
        rm -f "$link_candidate"
        return 1
    fi
    if [ -L "$destination" ]; then
        mv -hf "$link_candidate" "$destination" 2>/dev/null || \
            mv -Tf "$link_candidate" "$destination" 2>/dev/null || {
                rm -f "$link_candidate"
                return 1
            }
    elif ! mv -f "$link_candidate" "$destination"; then
        rm -f "$link_candidate"
        return 1
    fi
}

restore_link() {
    target=$1
    destination=$2
    if [ -n "$target" ]; then
        [ "$(readlink "$destination" 2>/dev/null || true)" = "$target" ] || \
            switch_link "$target" "$destination"
    elif [ -L "$destination" ] || [ -f "$destination" ]; then
        rm -f "$destination"
    fi
}

activate_durable_runtime() {
    durable_runtime=$1
    previous_current=$(readlink "$APP_HOME/current" 2>/dev/null || true)
    previous_cli=$(readlink "$BIN_DIR/normflow" 2>/dev/null || true)
    switch_link "$durable_runtime" "$APP_HOME/current" || return 1
    mkdir -p "$BIN_DIR"
    if ! switch_link "$APP_HOME/current/bin/normflow" "$BIN_DIR/normflow"; then
        restore_link "$previous_current" "$APP_HOME/current" || return 1
        restore_link "$previous_cli" "$BIN_DIR/normflow" || return 1
        return 1
    fi

    RUNTIME=$durable_runtime
}

release_target() {
    if [ -L "$1" ]; then
        readlink "$1"
    else
        printf '%s\n' "$1"
    fi
}

activation_points_to() {
    desired_runtime=$1
    [ "$(readlink "$APP_HOME/current" 2>/dev/null || true)" = "$desired_runtime" ] || return 1
    [ "$(readlink "$BIN_DIR/normflow" 2>/dev/null || true)" = "$APP_HOME/current/bin/normflow" ] || return 1
    [ -x "$BIN_DIR/normflow" ]
}

activate_runtime() {
    candidate=$1
    durable_runtime="$APP_HOME/runtimes/$version-$MODEL_SHA256-$$"
    mkdir -p "$(dirname "$durable_runtime")"
    mv "$candidate" "$durable_runtime" || fail "could not preserve the verified release"

    previous_current=$(readlink "$APP_HOME/current" 2>/dev/null || true)
    previous_cli=$(readlink "$BIN_DIR/normflow" 2>/dev/null || true)
    previous_release=$(readlink "$release_runtime" 2>/dev/null || true)
    if [ -L "$release_runtime" ]; then
        previous_release_kind="link"
    elif [ -e "$release_runtime" ]; then
        previous_release_kind="path"
    else
        previous_release_kind="missing"
    fi

    activate_durable_runtime "$durable_runtime" || fail "could not activate the verified release"

    mkdir -p "$(dirname "$release_runtime")"
    if [ -e "$release_runtime" ] && [ ! -L "$release_runtime" ]; then
        retired_runtime="$APP_HOME/runtimes/retired-$version-$$"
        if ! mv "$release_runtime" "$retired_runtime"; then
            restore_link "$previous_current" "$APP_HOME/current" || true
            restore_link "$previous_cli" "$BIN_DIR/normflow" || true
            fail "could not preserve the previous release"
        fi
    fi
    if ! switch_link "$durable_runtime" "$release_runtime"; then
        if [ "$previous_release_kind" = "path" ] && [ -n "${retired_runtime:-}" ]; then
            mv "$retired_runtime" "$release_runtime" || true
        elif [ "$previous_release_kind" = "link" ]; then
            restore_link "$previous_release" "$release_runtime" || true
        fi
        restore_link "$previous_current" "$APP_HOME/current" || true
        restore_link "$previous_cli" "$BIN_DIR/normflow" || true
        fail "could not record the active release"
    fi
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
    case "$version" in ''|'.'|'..'|*[!0-9A-Za-z.+-]*) fail "release manifest has an invalid version" ;; esac
    manifest_platform=$(awk -F '"' '/"platform"[[:space:]]*:/ { print $4; exit }' "$MANIFEST")
    [ "$manifest_platform" = "$PLATFORM" ] || fail "release manifest is not for $PLATFORM"

    installed_version=$(installed_release_version)
    if [ -n "$installed_version" ] && version_is_newer "$installed_version" "$version"; then
        fail "refuses to run release $version over newer managed release $installed_version"
    fi

    model_record=$(read_asset model)
    set -- $model_record
    MODEL_FILENAME=$1
    MODEL_SHA256=$2
    MODEL_CACHE="$APP_HOME/models/$MODEL_FILENAME.$MODEL_SHA256"
    release_runtime="$APP_HOME/releases/$version"
    if release_is_current "$release_runtime"; then
        durable_runtime=$(release_target "$release_runtime")
        if activation_points_to "$durable_runtime"; then
            printf 'NormFlow %s is already current at %s\n' "$version" "$release_runtime"
            return
        fi
        activate_durable_runtime "$durable_runtime"
        NEW_TERMINAL=0
        update_path
        printf 'NormFlow %s activated at %s\n' "$version" "$RUNTIME"
        if [ "$NEW_TERMINAL" = 1 ]; then
            printf 'Open a new terminal, then run: normflow --version\n'
        else
            printf 'Run: normflow --version\n'
        fi
        return
    fi

    wheel=$(download_verified_asset wheel)
    constraints=$(download_verified_asset constraints)
    if [ -f "$MODEL_CACHE" ] && [ "$(sha256 "$MODEL_CACHE")" = "$MODEL_SHA256" ]; then
        model=$MODEL_CACHE
        cache_model=0
    else
        model=$(download_verified_asset model)
        cache_model=1
    fi
    install_uv

    staging="$TEMP_DIR/runtime"
    UV_PYTHON_INSTALL_DIR="$APP_HOME/python" "$UV" python install "$PYTHON_VERSION"
    UV_PYTHON_INSTALL_DIR="$APP_HOME/python" "$UV" venv --python "$PYTHON_VERSION" "$staging"
    "$UV" pip install --python "$staging/bin/python" --constraint "$constraints" --torch-backend cpu "$wheel"
    mkdir -p "$staging/share/normflow/models"
    tar -xzf "$model" -C "$staging/share/normflow/models"
    printf '%s %s\n' "$MODEL_FILENAME" "$MODEL_SHA256" > "$staging/share/normflow/model-identity"
    RUNTIME=$staging
    smoke_test
    if [ "$cache_model" = 1 ]; then
        mkdir -p "$(dirname "$MODEL_CACHE")"
        mv -f "$model" "$MODEL_CACHE"
    fi
    activate_runtime "$staging"
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
