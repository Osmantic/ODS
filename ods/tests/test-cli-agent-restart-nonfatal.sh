#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/ods-cli"
update_block="$(sed -n '/^cmd_update() {/,/^cmd_shell() {/p' "$CLI")"

grep -q 'log_error "python3 not found in PATH"' "$CLI"
grep -q 'log_error "ODS host agent script not found:' "$CLI"
grep -q 'log_error "Agent did not become healthy' "$CLI"
! grep -q 'error "python3 not found in PATH"' "$CLI"
! grep -q 'error "ODS host agent script not found:' "$CLI"
! grep -q 'error "Agent did not become healthy' "$CLI"
grep -q 'cmd_agent restart || warn "Host agent restart failed (non-fatal)"' <<<"$update_block"
grep -q 'success "Update complete"' <<<"$update_block"

echo "PASS: failed session-agent restart remains non-fatal to update"
