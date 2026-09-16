#!/usr/bin/env bash
# Contract: invoking the CLI from a custom install must target that install,
# while explicit installer-compatible path variables retain their precedence.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*"; exit 1; }

make_install() {
    local root="$1" version="$2" marker="$3"
    mkdir -p "$root/lib"
    cp "$ROOT_DIR/ods-cli" "$root/ods-cli"
    cp "$ROOT_DIR/lib/service-registry.sh" "$root/lib/"
    chmod +x "$root/ods-cli"
    : > "$root/docker-compose.base.yml"
    printf 'ODS_VERSION=%s\nCUSTOM_MARKER=%s\n' "$version" "$marker" > "$root/.env"
}

run_cli() {
    local cli="$1"
    shift
    env -u INSTALL_DIR -u ODS_HOME -u ODS_SCRIPT_HINT -u ODS_INSTALL_DIR \
        HOME="$FAKE_HOME" bash "$cli" "$@"
}

FAKE_HOME="$WORK/home"
DEFAULT_INSTALL="$FAKE_HOME/ods"
CUSTOM_INSTALL="$WORK/custom install"
EXPLICIT_INSTALL="$WORK/explicit"
LEGACY_INSTALL="$WORK/legacy"
HINT_INSTALL="$WORK/hint"

make_install "$DEFAULT_INSTALL" "1.0.0" "default"
make_install "$CUSTOM_INSTALL" "2.0.0" "custom"
make_install "$EXPLICIT_INSTALL" "3.0.0" "explicit"
make_install "$LEGACY_INSTALL" "4.0.0" "legacy"
make_install "$HINT_INSTALL" "5.0.0" "hint"

output="$(run_cli "$CUSTOM_INSTALL/ods-cli" config show)"
grep -Fq "Install dir: $CUSTOM_INSTALL" <<<"$output" \
    || fail "CLI beside a valid custom install targeted a different directory"
grep -Fq 'CUSTOM_MARKER=custom' <<<"$output" \
    || fail "custom install configuration was not read"
! grep -Fq 'CUSTOM_MARKER=default' <<<"$output" \
    || fail "CLI silently operated on HOME/ods instead of its own install"
pass "the invoked CLI targets its own custom install"

mkdir -p "$WORK/bin"
ln -s "$CUSTOM_INSTALL/ods-cli" "$WORK/bin/ods"
output="$(run_cli "$WORK/bin/ods" version)"
[[ "$output" == 'ods-cli v2.0.0' ]] \
    || fail "symlink invocation did not resolve the target install: $output"
pass "symlink invocation resolves the target install"

output="$(HOME="$FAKE_HOME" INSTALL_DIR="$EXPLICIT_INSTALL" ODS_HOME="$LEGACY_INSTALL" \
    ODS_SCRIPT_HINT="$HINT_INSTALL" ODS_INSTALL_DIR="$DEFAULT_INSTALL" \
    bash "$CUSTOM_INSTALL/ods-cli" version)"
[[ "$output" == 'ods-cli v3.0.0' ]] \
    || fail "INSTALL_DIR did not retain highest precedence: $output"
pass "INSTALL_DIR retains highest precedence"

output="$(HOME="$FAKE_HOME" ODS_HOME="$LEGACY_INSTALL" \
    ODS_SCRIPT_HINT="$HINT_INSTALL" ODS_INSTALL_DIR="$EXPLICIT_INSTALL" \
    bash "$CUSTOM_INSTALL/ods-cli" version)"
[[ "$output" == 'ods-cli v4.0.0' ]] \
    || fail "ODS_HOME did not retain precedence: $output"
pass "ODS_HOME retains legacy precedence"

output="$(HOME="$FAKE_HOME" ODS_SCRIPT_HINT="$HINT_INSTALL" \
    ODS_INSTALL_DIR="$EXPLICIT_INSTALL" bash "$CUSTOM_INSTALL/ods-cli" version)"
[[ "$output" == 'ods-cli v5.0.0' ]] \
    || fail "populated ODS_SCRIPT_HINT was ignored: $output"
pass "populated ODS_SCRIPT_HINT precedes ODS_INSTALL_DIR"

SCRATCH="$WORK/scratch"
mkdir -p "$SCRATCH/lib"
cp "$ROOT_DIR/ods-cli" "$SCRATCH/ods-cli"
cp "$ROOT_DIR/lib/service-registry.sh" "$SCRATCH/lib/"
output="$(run_cli "$SCRATCH/ods-cli" version)"
[[ "$output" == 'ods-cli v1.0.0' ]] \
    || fail "a scratch CLI copy without install sentinels did not fall back to HOME/ods: $output"
pass "a non-install script directory falls back to HOME/ods"

echo "[PASS] CLI install directory resolution"
