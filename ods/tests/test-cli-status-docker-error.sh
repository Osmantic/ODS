#!/usr/bin/env bash
# Contract: `ods status` must show docker compose's own error when listing
# containers fails. It used to silence compose (2>/dev/null) and fall back to a
# flag-less `docker-compose ps`, so a stopped daemon surfaced as
# "no configuration file provided: not found" (or "command not found").
#
# Run from repo root:  bash ods/tests/test-cli-status-docker-error.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/ods-cli"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$CLI" ]] || fail "ods-cli not found"

status_src="$(sed -n '/^cmd_status()/,/^}/p' "$CLI")"
[[ -n "$status_src" ]] || fail "could not extract cmd_status from ods-cli"

echo "Test 1: cmd_status does not fall back to a flag-less docker-compose"
if grep -qE '\|\| *docker-compose ps' <<<"$status_src"; then
    fail "cmd_status still falls back to 'docker-compose ps' without compose flags"
fi
pass "no flag-less docker-compose fallback"

echo "Test 2: cmd_status lets the compose ps error through"
ps_line="$(grep -E 'docker compose "\$\{flags\[@\]\}" ps --format "table' <<<"$status_src" || true)"
[[ -n "$ps_line" ]] || fail "cmd_status no longer lists containers with 'docker compose \"\${flags[@]}\" ps --format \"table ...'"
if grep -qE '2> */dev/null' <<<"$ps_line"; then
    fail "cmd_status still discards the stderr of 'docker compose ps'"
fi
pass "docker compose ps stderr is not discarded"

echo "All ods status docker-error checks passed."
