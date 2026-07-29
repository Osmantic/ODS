#!/usr/bin/env bash
# Windows Lemonade full-model swap-wait contract (#1517).
#
# Guards two regressions in the BACKGROUND full-model swap on native Windows
# Lemonade (scripts/bootstrap-upgrade.sh):
#
#   1. The register/load wait must not be the too-short hardcoded 12-attempt
#      (~2 min) budget. A 22 GB MoE (Qwen3.6-35B-A3B) was not even *listed* by
#      Lemonade within 2 min of the swap restart, so the swap reverted to the
#      bootstrap model. The wait must be configurable and default to a longer
#      budget (>= 60 attempts), matching the llama.cpp warm-up path.
#
#   2. On swap failure the status JSON must report the REAL downloaded byte
#      counts (the model did download + verify) plus the actual cause — not a
#      bare `write_status "failed"` that zeroes bytesDownloaded/bytesTotal and
#      reads as a 0-byte download failure (which is what originally misled
#      triage into thinking the download never started).
#
#   3. The full-model swap must not leave active config split-brained. The
#      script updates .env/models.ini before restarting native Lemonade; if the
#      full model cannot be proven, it must restore the previous active config
#      so the bootstrap model remains the last-known-good runtime.
#
#   4. The swap must stop the existing native Lemonade router/listener before
#      launching the replacement. Otherwise Lemonade exits with "Another
#      instance of lemonade-router is already running", while probes keep
#      hitting the stale bootstrap process forever.
#
#   5. The restart helper must refuse to stop externally-managed Lemonade
#      runtimes, and it must accept daemon-style launches where the parent
#      process exits after leaving a healthy listener behind.
#
#   6. The post-swap Hermes patch must be dependency-free and fail-loud.
#      Windows Git Bash can resolve `python` to the Microsoft Store alias,
#      causing the Python patcher path to fail even though `command -v python`
#      returns something. The swap must patch with shell tools, verify the
#      YAML, and return non-zero if the patch does not land.
#
#   7. The Windows swap must move the bootstrap GGUF aside before launching
#      the full-model Lemonade process. Lemonade registers files present at
#      launch, so deleting the bootstrap after launch leaves stale bootstrap
#      model metadata that naïve clients/probes can select.
#
#   8. Native Lemonade cleanup must also stop the per-user cached llama.cpp
#      child process; it does not live under the Program Files Lemonade bin dir.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

SCRIPT="scripts/bootstrap-upgrade.sh"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

echo "[contract] Windows Lemonade swap wait + failure reporting (#1517)"

# ---------------------------------------------------------------------------
# 1. The swap wait must not hardcode the too-short 12-attempt budget.
# ---------------------------------------------------------------------------
if grep -Eq 'for _i in \$\(seq 1 12\)' "$SCRIPT"; then
    fail "swap wait still hardcodes 'seq 1 12' (~2 min) — too short for a large MoE to register"
else
    pass "swap wait no longer hardcodes the 12-attempt (~2 min) budget"
fi

# ---------------------------------------------------------------------------
# 2. The swap wait must be configurable with a sane longer default (>= 60).
# ---------------------------------------------------------------------------
if grep -Eq 'ODS_LEMONADE_SWAP_ATTEMPTS:-(6[0-9]|[7-9][0-9]|[1-9][0-9]{2,})' "$SCRIPT"; then
    pass "swap wait is configurable (ODS_LEMONADE_SWAP_ATTEMPTS) with a >= 60 default"
else
    fail "swap wait must read ODS_LEMONADE_SWAP_ATTEMPTS with a default of at least 60"
fi

# ---------------------------------------------------------------------------
# 3. The swap-registration-timeout failure must report real bytes + cause,
#    not a bare zero-byte write_status "failed".
# ---------------------------------------------------------------------------
if grep -q 'did not load it after swap (registration timeout)' "$SCRIPT" \
   && grep -Eq 'write_status "failed" 100 "\$TOTAL_BYTES" "\$TOTAL_BYTES"' "$SCRIPT"; then
    pass "swap-timeout failure reports real downloaded bytes + cause (not a 0-byte failure)"
else
    fail "swap-timeout failure must write_status with real TOTAL_BYTES and the registration-timeout cause"
fi

# ---------------------------------------------------------------------------
# 4. The post-swap Hermes-patch failure must likewise carry real bytes.
# ---------------------------------------------------------------------------
if grep -q 'Hermes config patch failed after swap' "$SCRIPT"; then
    pass "post-swap Hermes-patch failure reports real bytes + cause"
else
    fail "post-swap Hermes-patch failure must report real bytes + cause, not a bare failed status"
fi

# ---------------------------------------------------------------------------
# 5. Windows Lemonade swap must snapshot and restore active config on failure.
# ---------------------------------------------------------------------------
if grep -q 'snapshot_active_model_config' "$SCRIPT" \
   && grep -q 'restore_active_model_config' "$SCRIPT" \
   && grep -q 'move_bootstrap_model_aside_for_windows_swap' "$SCRIPT" \
   && grep -q 'restore_bootstrap_model_after_windows_swap_failure' "$SCRIPT" \
   && grep -q 'discard_bootstrap_model_backup_after_windows_swap' "$SCRIPT" \
   && grep -q 'Restoring previous active model config after Windows Lemonade swap timeout' "$SCRIPT"; then
    pass "Windows Lemonade swap snapshots config and protects bootstrap model recovery"
else
    fail "Windows Lemonade swap failure must restore previous config and bootstrap model backup"
fi

# ---------------------------------------------------------------------------
# 6. Failure status should tell operators the active config was restored.
# ---------------------------------------------------------------------------
if grep -q 'Previous active model config restored and bootstrap model kept' "$SCRIPT"; then
    pass "swap-timeout status tells the operator the previous active config was restored"
else
    fail "swap-timeout status should explicitly say the previous active config was restored"
fi

# ---------------------------------------------------------------------------
# 7. Native Windows Lemonade restart must clear the old router/listener before
#    launching the replacement process.
# ---------------------------------------------------------------------------
if grep -q 'Get-NetTCPConnection -LocalPort' "$SCRIPT" \
   && grep -q 'lemonade-router is already running' "$SCRIPT" \
   && grep -q 'Refusing to stop unowned process' "$SCRIPT" \
   && grep -q 'lemonadeCacheBin' "$SCRIPT" \
   && grep -q 'ODS_WIN_MODELS_DIR' "$SCRIPT"; then
    pass "Windows Lemonade restart clears only owned listeners, cached children, and singleton-router failures"
else
    fail "Windows Lemonade restart must safely stop owned listeners/cache children and detect singleton-router launch failures"
fi

# ---------------------------------------------------------------------------
# 8. The restart helper must stay scoped to ODS-managed native Windows
#    Lemonade and avoid false-failing daemon-style launches.
# ---------------------------------------------------------------------------
if grep -q 'AMD_INFERENCE_MANAGED' "$SCRIPT" \
   && grep -q 'AMD_INFERENCE_RUNTIME_MODE' "$SCRIPT" \
   && grep -q 'LEMONADE_EXTERNAL' "$SCRIPT" \
   && grep -q 'externally managed' "$SCRIPT" \
   && grep -q '/api/v1/models' "$SCRIPT"; then
    pass "Windows Lemonade restart is scoped to managed runtimes and allows healthy daemonized launches"
else
    fail "Windows Lemonade restart must avoid external runtimes and accept healthy daemonized launches"
fi

# ---------------------------------------------------------------------------
# 8b. Lemonade 10.7 removed the legacy launch flags and changed local GGUF
#     request IDs. Bootstrap promotion must use the shared version-aware
#     contract, resolve the live ID, and persist it for every downstream client.
# ---------------------------------------------------------------------------
restart_block="$(awk '
    /^restart_windows_lemonade_with_full_model\(\)/ { in_block=1 }
    in_block { print }
    in_block && /^}/ { exit }
' "$SCRIPT" | grep -v '^[[:space:]]*#')"
if grep -q 'Get-ODSLemonadeLaunchContract' <<<"$restart_block" \
   && grep -q 'New-ODSLemonadeScheduledTaskAction' <<<"$restart_block" \
   && grep -q 'Set-ODSLemonadeModernRuntimeConfig' <<<"$restart_block" \
   && grep -q 'Resolve-ODSLemonadeModelId' <<<"$restart_block" \
   && grep -q 'write_env_value LEMONADE_MODEL' <<<"$restart_block" \
   && ! grep -q -- '--no-tray' <<<"$restart_block" \
   && ! grep -q -- '--extra-models-dir' <<<"$restart_block"; then
    pass "Windows Lemonade swap uses the version-aware launch and exact model-ID contracts"
else
    fail "Windows Lemonade swap must use shared 10.7 launch/model-ID contracts without legacy flags"
fi

# ---------------------------------------------------------------------------
# 8c. The bootstrap-to-full Lemonade promotion must carry the promoted context
#     into Lemonade's runtime config and prove the loaded model picked it up.
#     Otherwise Windows can announce the full model while still serving the old
#     bootstrap/default context until a later restart.
# ---------------------------------------------------------------------------
if grep -q 'target_context="$(read_env_value CTX_SIZE)"' <<<"$restart_block" \
   && grep -q 'ODS_WIN_CONTEXT_SIZE=$target_context' <<<"$restart_block" \
   && grep -qF '$null = [int]::TryParse([string]$env:ODS_WIN_CONTEXT_SIZE, [ref]$contextSize)' <<<"$restart_block" \
   && grep -q 'ContextSize = $contextSize' <<<"$restart_block" \
   && grep -q 'verify_windows_lemonade_loaded_context "$lemonade_port" "$model_id" "$target_gguf" "$target_context"' <<<"$restart_block"; then
    pass "Windows Lemonade swap propagates and proves the promoted context"
else
    fail "Windows Lemonade swap must propagate and prove the promoted context before commit"
fi

# ---------------------------------------------------------------------------
# 9. Post-swap Hermes patching must not depend on Python being callable from
#    Git Bash, and it must verify the YAML before returning success.
# ---------------------------------------------------------------------------
if grep -q 'patch_hermes_yaml_with_sed' "$SCRIPT" \
   && grep -Fq 'grep -Fq "  default: \"${model}\""' "$SCRIPT" \
   && grep -Fq 'grep -Fq "  base_url: \"${base_url}\""' "$SCRIPT" \
   && grep -q 'ERROR: Could not patch ${tpl} after full-model swap.' "$SCRIPT" \
   && grep -q 'return 1' "$SCRIPT"; then
    pass "post-swap Hermes patch is dependency-free, verifies model/base_url, and is fail-loud"
else
    fail "post-swap Hermes patch must avoid Python alias failures and verify model/base_url before success"
fi

# ---------------------------------------------------------------------------
# 10. Full-model swap must preserve the install-time Hermes provider route.
#     On Windows AMD/Lemonade, HERMES_LLM_BASE_URL is the LiteLLM route; if
#     swap patching only updates model/context, the persisted Hermes config can
#     keep direct native Lemonade and override the fixed env value.
# ---------------------------------------------------------------------------
if grep -q 'hermes_base_url="$(read_env_value HERMES_LLM_BASE_URL)"' "$SCRIPT" \
   && grep -q 'patch_hermes_yaml_with_sed "$tpl" "$new_model" "$FULL_MAX_CONTEXT" "$hermes_base_url"' "$SCRIPT" \
   && grep -q 'patch_hermes_yaml_with_sed "$live" "$new_model" "$FULL_MAX_CONTEXT" "$hermes_base_url"' "$SCRIPT" \
   && grep -q 'patch_hermes_yaml_with_sed "$_hermes_live" "$_hermes_new_model" "$FULL_MAX_CONTEXT" "$_hermes_base_url"' "$SCRIPT" \
   && grep -q 'hermes_base_url_sed="$(printf' "$SCRIPT" \
   && grep -q 'base_url: \\"${hermes_base_url_sed}\\"' "$SCRIPT"; then
    pass "post-swap Hermes patch preserves HERMES_LLM_BASE_URL in template and live config"
else
    fail "post-swap Hermes patch must carry HERMES_LLM_BASE_URL into persisted Hermes config"
fi

echo "------------------------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
