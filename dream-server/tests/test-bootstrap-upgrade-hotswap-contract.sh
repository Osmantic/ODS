#!/usr/bin/env bash
# Regression checks for bootstrap-upgrade's llama-server hot-swap contract.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/scripts/bootstrap-upgrade.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$TARGET" ]] || fail "missing $TARGET"

# Strip comments so explanatory text cannot satisfy or fail the checks.
active_code="$(grep -v '^[[:space:]]*#' "$TARGET")"

grep -qF 'up -d --force-recreate --no-deps llama-server' <<<"$active_code" \
    || fail "llama-server hot-swap must force-recreate llama-server without deps"
pass "llama-server hot-swap uses force-recreate/no-deps"

llama_recreate_block="$(awk '
    /Restarting llama-server container/ { in_block=1 }
    in_block { print }
    in_block && /up -d --force-recreate --no-deps llama-server/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"

grep -qF 'env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE' <<<"$llama_recreate_block" \
    || fail "llama-server recreate must strip model vars so .env wins compose interpolation"
pass "llama-server recreate strips model env before compose"

if grep -qE '\brestart[[:space:]]+(llama-server|dream-llama-server)\b' <<<"$active_code"; then
    fail "llama-server hot-swap must not use restart; recreate is required so updated env lands"
fi
pass "llama-server hot-swap does not use restart shortcut"

if grep -qE '\bstop[[:space:]]+llama-server\b' <<<"$active_code"; then
    fail "llama.cpp hot-swap must not stop llama-server before compose up"
fi
pass "llama.cpp hot-swap does not use stop + up"

openclaw_recreate_block="$(awk '
    /Recreating OpenClaw to pick up model change/ { in_block=1 }
    in_block { print }
    in_block && /up -d --force-recreate openclaw/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"

grep -qF 'env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE' <<<"$openclaw_recreate_block" \
    || fail "OpenClaw recreate must strip model vars so .env wins compose interpolation"
pass "OpenClaw recreate strips model env before compose"

grep -qF 'inspect dream-llama-server --format' <<<"$active_code" \
    || fail "hot-swap must inspect the recreated container command"
grep -qF '"/models/${FULL_GGUF_FILE}"' <<<"$active_code" \
    || fail "hot-swap must assert the running command points at the full GGUF"
pass "hot-swap asserts the running command uses the full GGUF"

stale_block="$(awk '
    /llama-server container started with stale --model arg/ { in_block=1 }
    in_block { print }
    in_block && /fail "llama-server container started with stale --model arg after force-recreate."/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"

grep -qF 'write_status "failed"' <<<"$stale_block" \
    || fail "stale --model assertion must mark bootstrap status failed"
grep -qF 'fail "llama-server container started with stale --model arg after force-recreate."' <<<"$stale_block" \
    || fail "stale --model assertion must exit non-zero"
pass "stale --model assertion fails loudly"
