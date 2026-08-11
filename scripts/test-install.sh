#!/bin/sh
set -eu

RELEASE_URL=${RELEASE_URL:?RELEASE_URL not set}
PLATFORM=${PLATFORM:?PLATFORM not set}
TEMP_DIR=

fail() {
    printf '%s\n' "installer smoke: $*" >&2
    exit 1
}

cleanup() {
    [ -z "$TEMP_DIR" ] || rm -rf "$TEMP_DIR"
}
trap cleanup EXIT HUP INT TERM

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/normflow-release-smoke.XXXXXX") || \
    fail "could not create an isolated home"
HOME="$TEMP_DIR/home"
XDG_DATA_HOME="$TEMP_DIR/data"
XDG_BIN_HOME="$TEMP_DIR/bin"
TMPDIR="$TEMP_DIR/tmp"
NORMFLOW_RELEASE_URL=$RELEASE_URL
export HOME XDG_DATA_HOME XDG_BIN_HOME TMPDIR NORMFLOW_RELEASE_URL
mkdir -p "$HOME" "$XDG_DATA_HOME" "$XDG_BIN_HOME" "$TMPDIR"

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
repository=$(dirname "$script_dir")
installer="$repository/install.sh"

first_install=$(sh "$installer")
printf '%s\n' "$first_install"

normflow="$XDG_BIN_HOME/normflow"
version=$("$normflow" --version)
short_version=$("$normflow" -V)
[ -n "$version" ] || fail "normflow --version returned no version"
[ "$short_version" = "$version" ] || fail "normflow version flags disagree"
printf '%s\n' "$version" "$short_version"

runtime="$XDG_DATA_HOME/normflow/current"
runtime_python="$runtime/bin/python"
durable_runtime=$(readlink "$runtime" 2>/dev/null || true)
case "$durable_runtime" in
    "$XDG_DATA_HOME/normflow/runtimes/"*) ;;
    *) fail "installer did not activate a durable runtime for $PLATFORM" ;;
esac
[ -x "$runtime_python" ] || fail "installed runtime has no Python interpreter"
[ -z "$(find "$TMPDIR" -name 'normflow-install.*' -print -quit)" ] || \
    fail "installer staging directory survived cleanup"

"$repository/scripts/release_smoke_test.sh" "$runtime_python" "${PLATFORM}-smoke"

second_install=$(sh "$installer")
printf '%s\n' "$second_install"
case "$second_install" in
    *"already current"*) ;;
    *) fail "repeated installer invocation did not reuse the current release" ;;
esac
[ "$(readlink "$runtime")" = "$durable_runtime" ] || \
    fail "repeated installer invocation replaced the current runtime"
[ "$("$normflow" --version)" = "$version" ] || \
    fail "repeated installer invocation changed the installed version"

printf '%s\n' "${PLATFORM} installer smoke test passed"
