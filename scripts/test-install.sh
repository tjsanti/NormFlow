#!/bin/bash
set -eu

APP_HOME=${APP_HOME:-"$HOME/.local/share/normflow"}
BIN_DIR=${BIN_DIR:-"$HOME/.local/bin"}
RELEASE_URL=${RELEASE_URL:-""}
PLATFORM="${PLATFORM:-}"
TEMP_DIR=

fail() {
    printf '%s\n' "installer smoke: $*" >&2
    exit 1
}

cleanup() {
    [ -z "$TEMP_DIR" ] || rm -rf "$TEMP_DIR"
}
trap cleanup EXIT HUP INT TERM

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "requires $1"
}

select_sha256() {
    if command -v shasum >/dev/null 2>&1; then
        SHA256='shasum -a 256'
    elif command -v sha256sum >/dev/null 2>&1; then
        SHA256=sha256sum
    else
        fail "requires shasum or sha256sum"
    fi
}
sha256() {
    $SHA256 "$1" | awk '{print $1}'
}
download() {
    curl --fail --silent --show-error --location --output "$2" "$1"
}

if [ -z "$RELEASE_URL" ]; then
    fail "RELEASE_URL not set"
fi
require_command curl
require_command awk
require_command tar
select_sha256

TEMP_DIR=$(mktemp -d) || fail "could not create temp dir"
MANIFEST="$TEMP_DIR/manifest.json"
download "$RELEASE_URL/normflow-payload-$PLATFORM.json" "$MANIFEST"

version=$(awk -F '"' '/"version"[[:space:]]*:/ { print $4; exit }' "$MANIFEST")
case "$version" in ''|'.'|'..'|*[!0-9A-Za-z.+-]*) fail "invalid version in manifest" ;; esac
PLATFORM_ACTUAL=$(awk -F '"' '/"platform"[[:space:]]*:/ { print $4; exit }' "$MANIFEST")
[ "$PLATFORM_ACTUAL" = "$PLATFORM" ] || fail "manifest platform $PLATFORM_ACTUAL != expected $PLATFORM"

mkdir -p "$APP_HOME" "$BIN_DIR"

# Parse assets from manifest
assets=()
while IFS= read -r line; do
    assets+=("$line")
done < <(awk -v platform="$PLATFORM" '
    /^[[:space:]]*\{/ { filename=""; kind=""; digest="" }
    /"filename"[[:space:]]*:/ { filename=$0; sub(/.*"filename"[[:space:]]*:[[:space:]]*"/, "", filename); sub(/".*/, "", filename); }
    /"kind"[[:space:]]*:/ { kind=$0; sub(/.*"kind"[[:space:]]*:[[:space:]]*"/, "", kind); sub(/".*/, "", kind); }
    /"sha256"[[:space:]]*:/ { digest=$0; sub(/.*"sha256"[[:space:]]*:[[:space:]]*"/, "", digest); sub(/".*/, "", digest); }
    /^[[:space:]]*\}[,]?[[:space:]]*$/ {
        if (kind == "wheel" || kind == "model" || kind == "constraints") {
            print filename "|" digest "|" kind
        }
        filename=""; kind=""; digest=""
    }
' "$MANIFEST")

for record in "${assets[@]}"; do
    fname="${record%%|*}"
    rest="${record#*|}"
    digest="${rest%%|*}"
    kind="${rest#*|}"

    dest="$TEMP_DIR/$fname"
    download "$RELEASE_URL/$fname" "$dest"
    actual=$(sha256 "$dest")
    [ "$actual" = "$digest" ] || fail "$kind checksum mismatch"
    echo "downloaded and verified: $kind ($fname)"
done

# Install uv (minimal, same as install.sh)
UV_VERSION=0.6.14
case "$PLATFORM" in
    macos-aarch64-py313)
        UV_TARGET=aarch64-apple-darwin
        EXPECTED_SHA=4ea4731010fbd1bc8e790e07f1989755a5c7c2c732e9b77f85e302b0bee61b756
        ;;
    *)
        fail "unsupported platform for smoke test"
        ;;
esac

UV="$APP_HOME/uv/$UV_VERSION/bin/uv"
if [ ! -x "$UV" ]; then
    archive="$TEMP_DIR/uv.tar.gz"
    download "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_TARGET.tar.gz" "$archive"
    ACTUAL_SHA=$(sha256 "$archive")
    [ "$ACTUAL_SHA" = "$EXPECTED_SHA" ] || fail "uv bootstrap checksum mismatch"
    tar -xzf "$archive" -C "$TEMP_DIR"
    mv "$TEMP_DIR/uv-$UV_TARGET/uv" "$UV"
    chmod 755 "$UV"
fi

# Create venv and install
STAGING="$TEMP_DIR/runtime"
UV_PYTHON_INSTALL_DIR="$APP_HOME/python" "$UV" python install 3.13
UV_PYTHON_INSTALL_DIR="$APP_HOME/python" "$UV" venv --python 3.13 "$STAGING"
"$UV" pip install --python "$STAGING/bin/python" --constraint "$TEMP_DIR/normflow-${version}-constraints-${PLATFORM}.txt" "$TEMP_DIR/normflow-${version}-py3-none-any.whl"

# Extract model
MODEL_TAR=$(ls "$TEMP_DIR"/normflow-*-model-*.tar.gz | head -1)
mkdir -p "$STAGING/share/normflow/models"
tar -xzf "$MODEL_TAR" -C "$STAGING/share/normflow/models"
printf '%s\n' "$(basename "$MODEL_TAR" .tar.gz)" > "$STAGING/share/normflow/model-identity"

# Smoke test
scripts/release_smoke_test.sh "$STAGING/bin/python" "${PLATFORM}-smoke"
echo ""
# Test CLI
"$STAGING/bin/normflow" --version
"$STAGING/bin/normflow" -V

echo "${PLATFORM} smoke test passed"

# Repeated install - should detect current
echo "--- Repeated install test ---"
UV_PYTHON_INSTALL_DIR="$APP_HOME/python" "$UV" pip install --python "$STAGING/bin/python" --constraint "$TEMP_DIR/normflow-${version}-constraints-${PLATFORM}.txt" "$TEMP_DIR/normflow-${version}-py3-none-any.whl"
echo "repeated install succeeded (idempotent)"
