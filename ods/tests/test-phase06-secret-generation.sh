#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/06-directories.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

extract_function() {
    sed -n "/^${1}() {/,/^}/p" "$PHASE"
}

error() { printf '%s\n' "$*" >&2; }
_env_get() { printf '%s' "${2:-}"; }
eval "$(extract_function _phase06_generate_hex_secret)"
eval "$(extract_function _phase06_env_hex_secret)"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

od_bin="$(command -v od)"
tr_bin="$(command -v tr)"
mkdir -p "$TMP_DIR/od-only" "$TMP_DIR/empty"
printf '#!/bin/sh\nexec "%s" "$@"\n' "$od_bin" > "$TMP_DIR/od-only/od"
printf '#!/bin/sh\nexec "%s" "$@"\n' "$tr_bin" > "$TMP_DIR/od-only/tr"
chmod +x "$TMP_DIR/od-only/od" "$TMP_DIR/od-only/tr"

secret="$(PATH="$TMP_DIR/od-only" _phase06_generate_hex_secret 32)" \
    || fail "od-only fallback did not generate a secret"
[[ "$secret" =~ ^[0-9a-fA-F]{64}$ ]] \
    || fail "od-only fallback did not return 32 hex-encoded bytes"
echo "[PASS] od-only fallback returns validated entropy"

if PATH="$TMP_DIR/empty" _phase06_generate_hex_secret 32 >/dev/null 2>&1; then
    fail "secret generation succeeded without an entropy encoder"
fi
echo "[PASS] missing entropy encoders fail before env generation"

EXISTING_KEY="keep-this-existing-secret"
preserved="$(PATH="$TMP_DIR/empty" _phase06_env_hex_secret EXISTING_KEY 32)" \
    || fail "existing secret should not require an entropy encoder"
[[ "$preserved" == "$EXISTING_KEY" ]] \
    || fail "existing secret was replaced"
echo "[PASS] reruns preserve existing secrets without regeneration"

[[ "$(grep -c 'openssl rand -hex' "$PHASE")" -eq 1 ]] \
    || fail "phase 06 still bypasses the validated hex-secret helper"
echo "[PASS] every phase 06 hex secret uses the validated helper"
