#!/usr/bin/env bash
# Windows full-model swap after Round F: the native Windows Lemonade swap is
# retired (#1517's wait budget, router restarts and Lemonade id rules went
# with it). This removal guard keeps the name the existing runners call.
#
# Guards, in scripts/bootstrap-upgrade.sh (Windows Git Bash code only):
#   1. The Windows Lemonade launcher and its context check are gone, and no
#      Windows swap is selected for a Lemonade runtime.
#   2. No Lemonade process, task or ODS_WIN_LEMONADE_* launch plumbing remains,
#      so a user's own Lemonade can never be stopped or restarted by a swap.
#   3. The native llama-server swap goes through "ods.ps1 native-llm-restart"
#      (shared launch contract, pin.json check, model and context proof) and
#      restores and restarts the previous model when the proof fails.
# Behaviour is covered by tests/test-bootstrap-upgrade-windows-native-llama.sh.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="$ROOT_DIR/scripts/bootstrap-upgrade.sh"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

echo "[contract] Windows Lemonade full-model swap is retired"

for retired in restart_windows_lemonade_with_full_model verify_windows_lemonade_loaded_context; do
    if grep -q "^${retired}() {" "$TARGET"; then
        fail "bootstrap-upgrade.sh still defines ${retired}"
    else
        pass "bootstrap-upgrade.sh no longer defines ${retired}"
    fi
done

windows_dispatch="$(awk '
    /^_docker_llama_swap_applies=false$/ { seen=1; next }
    seen && /^if is_windows_bash; then$/ { on=1 }
    on { print }
    on && /^elif / { exit }
' "$TARGET")"
if [[ -n "$windows_dispatch" ]] \
    && grep -q '_windows_native_llama_swap_applies=true' <<<"$windows_dispatch" \
    && ! grep -q '_windows_lemonade_swap_applies=true' <<<"$windows_dispatch" \
    && ! grep -qi 'lemonade' <<<"$windows_dispatch"; then
    pass "the Windows swap dispatch selects only the native llama-server swap"
else
    fail "the Windows swap dispatch must select only the native llama-server swap"
fi

if grep -Eq 'ODS_WIN_LEMONADE_|ODSLemonadeRuntime|LemonadeServer|lemonade-server|lemonade-router' "$TARGET"; then
    fail "bootstrap-upgrade.sh still launches, stops or matches Lemonade processes or tasks"
else
    pass "no Lemonade process, task or launch plumbing remains in bootstrap-upgrade.sh"
fi

native_swap="$(awk '
    /^restart_windows_native_llama_server_with_full_model\(\) \{$/ { on=1 }
    on { print }
    on && /^}$/ { exit }
' "$TARGET")"
if grep -q 'native-llm-restart "\$install_dir_win"' <<<"$native_swap" \
    && grep -q 'restore_active_model_config' <<<"$native_swap" \
    && ! grep -q 'Stop-Process\|taskkill\|Get-NetTCPConnection' <<<"$native_swap"; then
    pass "the native swap goes through ods.ps1 native-llm-restart and restores the previous model on failure"
else
    fail "the native swap must use ods.ps1 native-llm-restart, restore the previous model, and never kill by port or name"
fi

echo "------------------------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
