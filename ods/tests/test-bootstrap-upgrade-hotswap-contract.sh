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
    in_block && /compose_recreate_llama_server_with_retry/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"

grep -qF 'compose_recreate_llama_server_with_retry "${COMPOSE_ARGS[@]}"' <<<"$llama_recreate_block" \
    || fail "llama-server hot-swap must use the retrying compose recreate helper"
pass "llama-server hot-swap uses retrying compose recreate helper"

compose_retry_block="$(awk '
    /^compose_recreate_llama_server_with_retry\(\)/ { in_block=1 }
    in_block { print }
    in_block && /^}/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"

grep -qF 'env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE' <<<"$compose_retry_block" \
    || fail "llama-server recreate must strip model vars so .env wins compose interpolation"
pass "llama-server recreate strips model env before compose"
grep -qF 'ODS_BOOTSTRAP_COMPOSE_RETRY_ATTEMPTS' <<<"$compose_retry_block" \
    || fail "llama-server recreate must expose a retry attempt override"
grep -qF 'No such container' <<<"$compose_retry_block" \
    || fail "llama-server recreate must retry Docker's missing-container race"
pass "llama-server recreate retries transient compose races"

if grep -qE '\brestart[[:space:]]+(llama-server|ods-llama-server)\b' <<<"$active_code"; then
    fail "llama-server hot-swap must not use restart; recreate is required so updated env lands"
fi
pass "llama-server hot-swap does not use restart shortcut"

if grep -qE '\bstop[[:space:]]+llama-server\b' <<<"$active_code"; then
    fail "llama.cpp hot-swap must not stop llama-server before compose up"
fi
pass "llama.cpp hot-swap does not use stop + up"

grep -qF 'resolve-compose-stack.sh' <<<"$active_code" \
    || fail "missing .compose-flags fallback must try resolve-compose-stack.sh before giving up"
pass "missing .compose-flags fallback tries compose resolver"

missing_flags_block="$(awk '
    /unable to recover compose flags/ { in_block=1 }
    in_block { print }
    in_block && /exit 1/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"

grep -qF 'write_status "failed"' <<<"$missing_flags_block" \
    || fail "missing compose flags fallback must mark bootstrap status failed"
grep -qF 'exit 1' <<<"$missing_flags_block" \
    || fail "missing compose flags fallback must stop before health checks"
if grep -qE '\b(stop|rm)[[:space:]]+ods-llama-server\b' <<<"$missing_flags_block"; then
    fail "missing compose flags fallback must not stop/remove the serving llama-server container"
fi
pass "missing .compose-flags fallback is non-destructive"

openclaw_recreate_block="$(awk '
    /Recreating OpenClaw to pick up model change/ { in_block=1 }
    in_block { print }
    in_block && /up -d --force-recreate openclaw/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"

grep -qF 'env -u GGUF_FILE -u LLM_MODEL -u MAX_CONTEXT -u CTX_SIZE' <<<"$openclaw_recreate_block" \
    || fail "OpenClaw recreate must strip model vars so .env wins compose interpolation"
pass "OpenClaw recreate strips model env before compose"

grep -qF 'inspect ods-llama-server --format' <<<"$active_code" \
    || fail "hot-swap must inspect the recreated container command"
grep -qF '"/models/${FULL_GGUF_FILE}"' <<<"$active_code" \
    || fail "hot-swap must assert the running command points at the full GGUF"
pass "hot-swap asserts the running command uses the full GGUF"

grep -qF 'restart_windows_lemonade_with_full_model' <<<"$active_code" \
    || fail "Windows Lemonade hot-swap must restart the native Lemonade process"
grep -qF 'extra.${FULL_GGUF_FILE' <<<"$active_code" \
    || fail "Windows Lemonade hot-swap must verify the full GGUF model id"
pass "Windows Lemonade hot-swap restarts native inference and verifies the full model"

grep -qF 'patch_hermes_model_after_swap' <<<"$active_code" \
    || fail "Windows Lemonade hot-swap must patch Hermes off the bootstrap model"
windows_lemonade_block="$(awk '
    /_windows_lemonade_swap_applies/ { in_block=1 }
    in_block { print }
    in_block && /HOT_SWAP_VERIFIED=true/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
grep -qF 'patch_hermes_model_after_swap' <<<"$windows_lemonade_block" \
    || fail "Windows Lemonade must patch Hermes before marking the swap verified"
pass "Windows Lemonade hot-swap patches Hermes before cleanup"

grep -qF 'HOT_SWAP_VERIFIED=true' <<<"$active_code" \
    || fail "hot-swap must record when the full model is verified serving"
grep -qF 'Removing bootstrap model after verified full-model serving' <<<"$active_code" \
    || fail "bootstrap GGUF cleanup must happen only after verified full-model serving"
bootstrap_cleanup_block="$(awk '
    /HOT_SWAP_VERIFIED.*true.*BOOTSTRAP_PATH/ { in_block=1 }
    in_block { print }
    in_block && /Bootstrap model removed/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
if grep -qF 'HOT_SWAP_VERIFIED' <<<"$bootstrap_cleanup_block" \
    && grep -qF 'Removing bootstrap model after verified full-model serving' <<<"$bootstrap_cleanup_block"; then
    pass "bootstrap cleanup is gated by verified full-model serving"
else
    fail "bootstrap cleanup must be gated by HOT_SWAP_VERIFIED"
fi

grep -qF 'refresh_lemonade_after_bootstrap_cleanup' <<<"$active_code" \
    || fail "AMD/Lemonade cleanup must refresh llama-server after removing bootstrap GGUF"
lemonade_cleanup_block="$(awk '
    /refresh_lemonade_after_bootstrap_cleanup/ { in_block=1 }
    in_block { print }
    in_block && /Lemonade refresh after bootstrap cleanup failed/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
grep -qF 'up -d --force-recreate --no-deps llama-server' <<<"$lemonade_cleanup_block" \
    || fail "AMD/Lemonade cleanup refresh must force-recreate llama-server after bootstrap removal"
grep -qF 'old_model_id="extra.${BOOTSTRAP_GGUF' <<<"$lemonade_cleanup_block" \
    || fail "AMD/Lemonade cleanup refresh must verify the removed bootstrap model is no longer advertised"
grep -qF 'write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES"' <<<"$lemonade_cleanup_block" \
    || fail "AMD/Lemonade cleanup refresh failure must report real downloaded bytes"
pass "AMD/Lemonade cleanup refresh drops stale bootstrap metadata"

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

windows_failure_block="$(awk '
    /_windows_lemonade_swap_applies/ { in_block=1 }
    in_block { print }
    in_block && /exit 1/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
grep -qF 'write_status "failed"' <<<"$windows_failure_block" \
    || fail "Windows Lemonade hot-swap failure must mark bootstrap status failed"
grep -qF 'exit 1' <<<"$windows_failure_block" \
    || fail "Windows Lemonade hot-swap failure must exit non-zero"
pass "Windows Lemonade hot-swap failure is honest"

docker_timeout_block="$(awk '
    /llama-server health check timed out/ { in_block=1 }
    in_block { print }
    in_block && /exit 1/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
grep -qF 'write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES"' <<<"$docker_timeout_block" \
    || fail "Docker hot-swap timeout must mark bootstrap status failed with real byte counts"
grep -qF 'exit 1' <<<"$docker_timeout_block" \
    || fail "Docker hot-swap timeout must exit non-zero"
pass "Docker hot-swap timeout is honest"
