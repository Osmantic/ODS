#!/usr/bin/env bash
# Contract: every live assignment in .env.example must read back as the same
# value for Docker Compose and for ODS's own .env readers.
#
# Compose (and a plain `source .env`) treat " # ..." after an unquoted value as
# a comment. lib/safe-env.sh, the dashboard settings reader and the macOS CLI
# keep the rest of the line as the value instead. A trailing inline comment on
# a live line therefore hands containers one value and ODS tooling another,
# e.g. OLLAMA_PORT="11434          # llama-server API ..." inside `ods chat`.
#
# Run from repo root:  bash ods/tests/test-env-example-inline-comments.sh
# Or from ods:         bash tests/test-env-example-inline-comments.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_EXAMPLE="$ROOT_DIR/.env.example"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$ENV_EXAMPLE" ]] || fail ".env.example not found at $ENV_EXAMPLE"
[[ -f "$ROOT_DIR/lib/safe-env.sh" ]] || fail "lib/safe-env.sh not found"

echo "Test 1: no live .env.example assignment carries an inline comment"
# Live (uncommented) KEY=value lines with whitespace + '#' after the value.
# Commented-out reference lines are free to keep their trailing notes.
offenders="$(grep -nE '^[A-Za-z_][A-Za-z0-9_]*=.*[[:space:]]#' "$ENV_EXAMPLE" || true)"
if [[ -n "$offenders" ]]; then
    echo "$offenders"
    fail "inline comment on a live .env.example assignment; put the note on its own line"
fi
pass "no live assignment has an inline comment"

echo "Test 2: safe-env.sh reads the shipped port defaults as plain numbers"
ports="$(
    # shellcheck source=../lib/safe-env.sh
    . "$ROOT_DIR/lib/safe-env.sh"
    load_env_file "$ENV_EXAMPLE"
    printf '%s\n%s\n' "${OLLAMA_PORT:-}" "${WEBUI_PORT:-}"
)"
ollama_port="${ports%%$'\n'*}"
webui_port="${ports#*$'\n'}"
[[ "$ollama_port" =~ ^[0-9]+$ ]] || fail "OLLAMA_PORT read as <$ollama_port>"
[[ "$webui_port" =~ ^[0-9]+$ ]] || fail "WEBUI_PORT read as <$webui_port>"
pass "OLLAMA_PORT=$ollama_port WEBUI_PORT=$webui_port"

echo "All .env.example inline-comment checks passed."
