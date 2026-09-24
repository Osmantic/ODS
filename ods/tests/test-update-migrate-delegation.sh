#!/usr/bin/env bash
# ods-update.sh must delegate migrations to the migration manager.
#
# cmd_update used to run every migrations/migrate-v*.sh unconditionally on
# every update. That bypassed the documented contract in
# migrations/README.md ("ods-update.sh ... runs migrations during updates:
# ./scripts/migrate-config.sh migrate"):
#
#   * .migration-state was never read or stamped, so already-applied
#     migrations re-ran on every update and `migrate-config.sh check`
#     reported "Migration needed" forever afterwards.
#   * There was no upper version bound, so a script bundle containing a
#     migration for a release newer than the incoming one would write
#     future configuration onto an older installation.
#   * The recorded .version still names the OLD release at migration time,
#     so the update flow must pass the post-pull manifest version as the
#     selection bound (MIGRATE_TARGET_VERSION).
#
# Run from repo root:  bash ods/tests/test-update-migrate-delegation.sh
# Or from ods:         bash tests/test-update-migrate-delegation.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="$ROOT_DIR/ods-update.sh"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$UPDATE_SCRIPT" ]] || fail "ods-update.sh not found at $UPDATE_SCRIPT"

cmd_update_body=$(awk '/^cmd_update\(\) \{/,/^}/' "$UPDATE_SCRIPT")
[[ -n "$cmd_update_body" ]] || fail "could not extract cmd_update from ods-update.sh"

# 1. Delegates to the migration manager rather than open-coding the loop.
echo "$cmd_update_body" | grep -q 'migrate-config\.sh' \
    || fail "cmd_update does not call the migration manager (scripts/migrate-config.sh)"
pass "cmd_update delegates migrations to scripts/migrate-config.sh"

echo "$cmd_update_body" | grep -q 'migrate_manager" migrate\|migrate-config\.sh" migrate\|migrate-config\.sh migrate' \
    || fail "cmd_update does not invoke the manager's migrate subcommand"
pass "cmd_update invokes \`migrate-config.sh migrate\`"

# 2. Bounds migration selection by the incoming release: the recorded
# .version still names the old release at this point, so the post-pull
# manifest.json supplies the target version.
echo "$cmd_update_body" | grep -q 'MIGRATE_TARGET_VERSION' \
    || fail "cmd_update does not pass the incoming release as the selection bound"
pass "cmd_update passes MIGRATE_TARGET_VERSION to the manager"

echo "$cmd_update_body" | grep -q 'manifest\.json' \
    || fail "cmd_update does not derive the target version from the post-pull manifest.json"
pass "target version comes from the post-pull manifest.json"

# 3. A migration failure still routes through _update_rollback.
migrate_tail=$(echo "$cmd_update_body" | sed -n '/migrate-config\.sh/,$p' | head -10)
echo "$migrate_tail" | grep -q '_update_rollback' \
    || fail "migration failure is not routed through _update_rollback"
pass "migration failure rolls back"

# 4. The unconditional per-script loop is gone.
if echo "$cmd_update_body" | grep -q 'for migration in'; then
    fail "cmd_update still iterates migrate-v*.sh unconditionally"
else
    pass "unconditional migrate-v*.sh loop removed"
fi
