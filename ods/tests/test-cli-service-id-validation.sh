#!/usr/bin/env bash
# Regression (issue #4604): cmd_enable/cmd_disable spliced the resolved
# service_id into `$INSTALL_DIR/extensions/services/$service_id` with no
# charset validation. `sr_resolve` passes unknown input through unchanged,
# so `ods enable ../../escape` resolved to a path outside the install tree;
# when that directory existed, `mv`/`rm`/`_regenerate_compose_flags`
# operated on it.
#
# `_require_safe_service_id` must reject traversal/absolute-path input and
# both cmd_enable and cmd_disable must invoke it.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/ods-cli"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

FUNC_SRC="$(awk '/^_require_safe_service_id\(\) \{/,/^\}/' "$CLI")"
[[ -n "$FUNC_SRC" ]] || fail "could not extract _require_safe_service_id from ods-cli"

check_id() {
    bash -c '
        set -euo pipefail
        error() { echo -e "ERROR: $1" >&2; exit 1; }
        eval "$1"
        _require_safe_service_id "$2"
    ' _ "$FUNC_SRC" "$1"
}

# ── Legitimate IDs accepted ────────────────────────────────────────────────
for id in n8n open-webui ods-proxy llama-server pixel-agent tts x whisper2; do
    check_id "$id" || fail "valid service id rejected: $id"
done
pass "kebab-case service ids accepted"

# ── Traversal / absolute / metachar input rejected ─────────────────────────
# shellcheck disable=SC2016  # metachar strings are intentional literals
for bad in '../escape' '../../etc/passwd' 'a/b' '/etc' 'x;rm' 'x y' 'x$(id)' 'x`id`' '.hidden' '-flag' 'UPPER'; do
    if check_id "$bad" >/dev/null 2>&1; then
        fail "unsafe service id accepted: $bad"
    fi
done
pass "path traversal and metachar service ids rejected"

# ── Both mutating entry points invoke the validator ────────────────────────
for fn in cmd_enable cmd_disable; do
    awk "/^${fn}\(\) \{/,/^\}/" "$CLI" | grep -q '_require_safe_service_id' \
        || fail "$fn does not invoke _require_safe_service_id"
done
pass "cmd_enable and cmd_disable invoke _require_safe_service_id"

echo "All service-id validation checks passed."
