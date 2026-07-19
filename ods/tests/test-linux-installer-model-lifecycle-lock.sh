#!/usr/bin/env bash
# Regression: Linux reinstall and background bootstrap promotion must not race.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_LIB="$ROOT_DIR/installers/lib/model-lifecycle-lock.sh"
INSTALLER="$ROOT_DIR/install-core.sh"
UPGRADER="$ROOT_DIR/scripts/bootstrap-upgrade.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$LOCK_LIB" ]] || fail "missing model lifecycle lock helper"
# shellcheck source=installers/lib/model-lifecycle-lock.sh
. "$LOCK_LIB"

installer_acquire_line="$(grep -n 'ods_model_lifecycle_lock_acquire.*Linux installer model configuration' "$INSTALLER" | cut -d: -f1)"
phase06_line="$(grep -n 'INSTALL_PHASE="06-directories"' "$INSTALLER" | cut -d: -f1)"
phase11_line="$(grep -n 'INSTALL_PHASE="11-services"' "$INSTALLER" | cut -d: -f1)"
installer_release_line="$(grep -n '^ods_model_lifecycle_lock_release$' "$INSTALLER" | cut -d: -f1)"

[[ -n "$installer_acquire_line" && -n "$phase06_line" && -n "$phase11_line" && -n "$installer_release_line" ]] \
    || fail "could not locate installer lifecycle lock boundaries"
(( installer_acquire_line < phase06_line )) \
    || fail "installer must acquire the model lifecycle lock before phase 06 rewrites .env"
(( phase11_line < installer_release_line )) \
    || fail "installer must retain the model lifecycle lock through phase 11 compose startup"
pass "installer serializes model configuration from phase 06 through phase 11"

sdxl_spawn_block="$(awk '
    /This daemon must not inherit the installer model lifecycle lock/ { in_block=1 }
    in_block { print }
    in_block && /^[[:space:]]*\)[[:space:]]*&/ { exit }
' "$ROOT_DIR/installers/phases/11-services.sh")"
grep -q '_phase11_close_inherited_fds_for_daemon' <<<"$sdxl_spawn_block" \
    || fail "detached SDXL download must close the installer lifecycle lock fd"
grep -q 'exec nohup env' <<<"$sdxl_spawn_block" \
    || fail "detached SDXL download must exec only after inherited fds are closed"
pass "detached Phase 11 downloads cannot retain the installer lifecycle lock"

verify_line="$(grep -n '# .*Phase 2: Verify integrity' "$UPGRADER" | cut -d: -f1)"
upgrader_acquire_line="$(grep -n 'acquire_model_lifecycle_lock || fail "Could not serialize background full-model activation' "$UPGRADER" | cut -d: -f1)"
env_update_line="$(grep -n '# .*Phase 3: Update .env' "$UPGRADER" | cut -d: -f1)"
bootstrap_cleanup_line="$(grep -n '# .*Phase 5b: Remove bootstrap model' "$UPGRADER" | cut -d: -f1)"
complete_line="$(grep -n 'write_status "complete"' "$UPGRADER" | tail -1 | cut -d: -f1)"

[[ -n "$verify_line" && -n "$upgrader_acquire_line" && -n "$env_update_line" && -n "$bootstrap_cleanup_line" && -n "$complete_line" ]] \
    || fail "could not locate upgrader lifecycle lock boundaries"
(( verify_line < upgrader_acquire_line && upgrader_acquire_line < env_update_line )) \
    || fail "upgrader must confirm lifecycle ownership before config promotion"
(( bootstrap_cleanup_line < complete_line )) \
    || fail "upgrader completion must follow bootstrap cleanup"
grep -q "trap 'release_model_lifecycle_lock; release_upgrade_lock' EXIT" "$UPGRADER" \
    || fail "upgrader must retain and automatically release both lifecycle locks"
finalization_locks="$(grep -c 'acquire_model_lifecycle_lock || fail "Could not serialize full-model finalization' "$UPGRADER")"
[[ "$finalization_locks" -ge 3 ]] \
    || fail "every Linux path that publishes a final GGUF must first acquire the lifecycle lock"
grep -q 'Download interrupted.*release_model_lifecycle_lock; release_upgrade_lock' "$UPGRADER" \
    || fail "interrupted finalization must release both lifecycle locks"
pass "upgrader locks finalization/activation and retains ownership through cleanup"

if ! command -v flock >/dev/null 2>&1; then
    echo "[SKIP] flock is unavailable; static lifecycle lock contracts passed, runtime contention test skipped"
    exit 0
fi

tmp="$(mktemp -d)"
installer_pid=""
upgrader_pid=""
cleanup() {
    [[ -n "$installer_pid" ]] && kill "$installer_pid" 2>/dev/null || true
    [[ -n "$upgrader_pid" ]] && kill "$upgrader_pid" 2>/dev/null || true
    rm -rf "$tmp"
}
trap cleanup EXIT

install_dir="$tmp/install"
mkdir -p "$install_dir/data/models"
printf 'bootstrap\n' > "$install_dir/data/models/Bootstrap.gguf"

lock_from_shell="$(XDG_RUNTIME_DIR="$tmp/runtime-shell" ods_model_lifecycle_lock_file "$install_dir")"
lock_from_service="$(XDG_RUNTIME_DIR="$tmp/runtime-service" ods_model_lifecycle_lock_file "$install_dir")"
[[ "$lock_from_shell" == "$lock_from_service" ]] \
    || fail "lifecycle lock identity must not depend on XDG_RUNTIME_DIR"
pass "shell and service contexts resolve the same lifecycle lock"

export ODS_MODEL_LIFECYCLE_LOCK_ROOT="$tmp/locks"
export ODS_TEST_LOCK_LIB="$LOCK_LIB"
export ODS_TEST_INSTALL_DIR="$install_dir"
export ODS_TEST_EVENTS="$tmp/events"

bash -c '
    set -euo pipefail
    . "$ODS_TEST_LOCK_LIB"
    ods_model_lifecycle_lock_acquire "$ODS_TEST_INSTALL_DIR" "test installer"
    [[ ! -f "$ODS_TEST_INSTALL_DIR/data/models/Full.gguf" ]]
    printf "installer-decided-bootstrap\n" >> "$ODS_TEST_EVENTS"
    for _wait in $(seq 1 100); do
        grep -q "upgrader-waiting" "$ODS_TEST_EVENTS" 2>/dev/null && break
        sleep 0.05
    done
    grep -q "upgrader-waiting" "$ODS_TEST_EVENTS"
    sleep 0.2
    [[ -s "$ODS_TEST_INSTALL_DIR/data/models/Bootstrap.gguf" ]]
    [[ ! -e "$ODS_TEST_INSTALL_DIR/data/models/Full.gguf" ]]
    printf "installer-compose-used-bootstrap\n" >> "$ODS_TEST_EVENTS"
    ods_model_lifecycle_lock_release
' &
installer_pid=$!

for _wait in $(seq 1 100); do
    grep -q "installer-decided-bootstrap" "$tmp/events" 2>/dev/null && break
    sleep 0.05
done
grep -q "installer-decided-bootstrap" "$tmp/events" \
    || fail "test installer did not acquire the lifecycle lock"

bash -c '
    set -euo pipefail
    . "$ODS_TEST_LOCK_LIB"
    printf "full\n" > "$ODS_TEST_INSTALL_DIR/data/models/Full.gguf.part"
    printf "upgrader-waiting\n" >> "$ODS_TEST_EVENTS"
    ods_model_lifecycle_lock_acquire "$ODS_TEST_INSTALL_DIR" "test upgrader"
    mv "$ODS_TEST_INSTALL_DIR/data/models/Full.gguf.part" "$ODS_TEST_INSTALL_DIR/data/models/Full.gguf"
    rm -f "$ODS_TEST_INSTALL_DIR/data/models/Bootstrap.gguf"
    printf "upgrader-promoted-full\n" >> "$ODS_TEST_EVENTS"
    ods_model_lifecycle_lock_release
' &
upgrader_pid=$!

wait "$installer_pid"
installer_pid=""
wait "$upgrader_pid"
upgrader_pid=""

events="$(cat "$tmp/events")"
[[ "$events" == *$'installer-compose-used-bootstrap\nupgrader-promoted-full'* ]] \
    || fail "background promotion crossed the installer's bootstrap decision/compose boundary"
[[ ! -e "$install_dir/data/models/Bootstrap.gguf" && -s "$install_dir/data/models/Full.gguf" ]] \
    || fail "serialized handoff did not leave the full model as the final state"
pass "concurrent download stays parallel while activation waits for installer compose"
