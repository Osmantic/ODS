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

function_block() {
    local function_name="$1"
    awk -v signature="^${function_name}[(][)]" '
        $0 ~ signature { in_block=1 }
        in_block { print }
        in_block && /^}/ { exit }
    ' "$TARGET"
}

assert_in_order() {
    local block="$1" label="$2"
    shift 2
    local previous=0 pattern line
    for pattern in "$@"; do
        line="$(grep -nF -- "$pattern" <<<"$block" | head -1 | cut -d: -f1 || true)"
        [[ -n "$line" ]] || fail "$label is missing ordered step: $pattern"
        (( line > previous )) || fail "$label has out-of-order step: $pattern"
        previous="$line"
    done
}

[[ -f "$TARGET" ]] || fail "missing $TARGET"

# Strip comments so explanatory text cannot satisfy or fail the checks.
active_code="$(grep -v '^[[:space:]]*#' "$TARGET")"

gate_acquire_block="$(function_block acquire_model_router_swap_gate | grep -v '^[[:space:]]*#')"
gate_release_block="$(function_block release_model_router_swap_gate | grep -v '^[[:space:]]*#')"
grep -qF 'model_router_swap_gate_call begin "$token" 30' <<<"$gate_acquire_block" \
    || fail "model swap admission must use a short renewable router lease"
grep -qF 'model_router_swap_gate_health' <<<"$gate_acquire_block" \
    || fail "model swap admission must inspect authoritative router request counts"
grep -qF 'consecutive_idle >= 2' <<<"$gate_acquire_block" \
    || fail "model swap admission must prove a stable drained boundary"
grep -qF 'model_router_swap_gate_call end "$token" 30' <<<"$gate_release_block" \
    || fail "model swap admission must explicitly reopen after the transaction"
grep -qF "trap 'release_model_router_swap_gate; release_model_lifecycle_lock; release_upgrade_lock' EXIT" <<<"$active_code" \
    || fail "model swap admission must reopen on every normal or failed exit"
top_level_swap="$(awk '
    /acquire_model_lifecycle_lock \|\| fail "Could not serialize background full-model activation/ { in_block=1 }
    in_block { print }
    /Snapshotting active model config before full-model swap/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
assert_in_order "$top_level_swap" "bootstrap swap drain boundary" \
    'acquire_model_lifecycle_lock ||' \
    'acquire_model_router_swap_gate' \
    'Snapshotting active model config before full-model swap'
pass "bootstrap promotion closes, drains, and releases router admission"

yaml_scalar_block="$(function_block yaml_double_quoted_scalar_content | grep -v '^[[:space:]]*#')"
sed_escape_block="$(function_block sed_replacement_escape | grep -v '^[[:space:]]*#')"
hermes_host_patch_block="$(function_block patch_hermes_yaml_with_sed | grep -v '^[[:space:]]*#')"
hermes_container_patch_block="$(function_block patch_hermes_yaml_in_container | grep -v '^[[:space:]]*#')"
grep -qF '$DOCKER_CMD exec ods-hermes sed -i' <<<"$hermes_container_patch_block" \
    || fail "Hermes live config patching must pass sed arguments directly to docker exec"
if grep -qF 'exec ods-hermes sh -c' <<<"$active_code"; then
    fail "Hermes live config patching must not reparse generated commands through sh -c"
fi

eval "$yaml_scalar_block"
eval "$sed_escape_block"
eval "$hermes_host_patch_block"
eval "$hermes_container_patch_block"
hermes_patch_tmp="$(mktemp -d "${TMPDIR:-/tmp}/ods-hermes-patch.XXXXXX")"
hermes_patch_marker="$hermes_patch_tmp/injected"
hermes_config="$hermes_patch_tmp/config.yaml"
cat >"$hermes_config" <<'YAML'
model:
  default: old-model
  context_length: 8192
provider:
  base_url: http://old.invalid/v1
  context_length: 8192
compression:
  enabled: false
  threshold: 0.50
  target_ratio: 0.25
  protect_last_n: 20
request:
    request_timeout_seconds: 180
YAML
hermes_docker_calls=0
hermes_docker_args=()
docker() {
    hermes_docker_calls=$((hermes_docker_calls + 1))
    hermes_docker_args=("$@")
    local arg_count=${#hermes_docker_args[@]}
    local sed_arg_count=$((arg_count - 4))
    command sed "${hermes_docker_args[@]:3:$sed_arg_count}" "$hermes_config"
}
DOCKER_CMD=docker
hermes_malicious_model="model&branch|tag\\path\"quoted' ; touch ${hermes_patch_marker} ; #"
hermes_malicious_url="http://example.invalid/v1\\path\"quoted' ; touch ${hermes_patch_marker}.url ; #"
patch_hermes_yaml_in_container \
    "$hermes_malicious_model" 65536 "$hermes_malicious_url" 900 true \
    || fail "Hermes live patch helper rejected metacharacters that should remain data"
[[ ! -e "$hermes_patch_marker" && ! -e "${hermes_patch_marker}.url" ]] \
    || fail "Hermes live patch helper executed config values as shell input"
[[ "${hermes_docker_args[0]:-}" == "exec" \
    && "${hermes_docker_args[1]:-}" == "ods-hermes" \
    && "${hermes_docker_args[2]:-}" == "sed" \
    && "${hermes_docker_args[3]:-}" == "-i" \
    && "${hermes_docker_args[${#hermes_docker_args[@]}-1]:-}" == "/opt/data/config.yaml" ]] \
    || fail "Hermes live patch helper did not preserve the expected docker/sed argv boundary"
for hermes_arg in "${hermes_docker_args[@]}"; do
    [[ "$hermes_arg" != "sh" && "$hermes_arg" != "-c" ]] \
        || fail "Hermes live patch helper reintroduced a container shell"
done
if patch_hermes_yaml_in_container "safe-model" "65536; touch ${hermes_patch_marker}" "" 180 false; then
    fail "Hermes live patch helper accepted a non-numeric context"
fi
[[ "$hermes_docker_calls" -eq 1 && ! -e "$hermes_patch_marker" ]] \
    || fail "Hermes live patch helper invoked docker for invalid numeric input"
hermes_expected_model="$(yaml_double_quoted_scalar_content "$hermes_malicious_model")"
hermes_expected_url="$(yaml_double_quoted_scalar_content "$hermes_malicious_url")"
grep -Fq "  default: \"${hermes_expected_model}\"" "$hermes_config" \
    || fail "Hermes live patch helper did not persist a valid double-quoted model scalar"
grep -Fq "  base_url: \"${hermes_expected_url}\"" "$hermes_config" \
    || fail "Hermes live patch helper did not persist a valid double-quoted base URL scalar"

hermes_host_config="$hermes_patch_tmp/host-config.yaml"
cp "$hermes_config" "$hermes_host_config"
patch_hermes_yaml_with_sed \
    "$hermes_host_config" "$hermes_malicious_model" 131072 "$hermes_malicious_url" 900 \
    || fail "Hermes host patch helper rejected safe scalar metacharacters"
grep -Fq "  default: \"${hermes_expected_model}\"" "$hermes_host_config" \
    || fail "Hermes host patch helper did not persist a valid double-quoted model scalar"
grep -Fq "  base_url: \"${hermes_expected_url}\"" "$hermes_host_config" \
    || fail "Hermes host patch helper did not persist a valid double-quoted base URL scalar"
rm -rf -- "$hermes_patch_tmp"
unset -f docker patch_hermes_yaml_in_container patch_hermes_yaml_with_sed \
    yaml_double_quoted_scalar_content sed_replacement_escape
pass "Hermes live patch values stay inside explicit docker exec arguments"

compose_hermes_block="$(function_block compose_recreate_hermes | grep -v '^[[:space:]]*#')"
eval "$compose_hermes_block"
compose_hermes_tmp="$(mktemp -d "${TMPDIR:-/tmp}/ods-hermes-compose.XXXXXX")"
INSTALL_DIR="$compose_hermes_tmp/install"
mkdir -p "$INSTALL_DIR"
compose_capture="$compose_hermes_tmp/calls"
compose_mock="$compose_hermes_tmp/mock-compose"
cat >"$compose_mock" <<'MOCK_COMPOSE'
#!/usr/bin/env bash
{
    printf 'cwd=%s\n' "$PWD"
    printf 'arg=%s\n' "$@"
    printf 'GGUF_FILE=%s\n' "${GGUF_FILE-unset}"
    printf 'LLM_MODEL=%s\n' "${LLM_MODEL-unset}"
    printf 'LEMONADE_MODEL=%s\n' "${LEMONADE_MODEL-unset}"
    printf 'MAX_CONTEXT=%s\n' "${MAX_CONTEXT-unset}"
    printf 'CTX_SIZE=%s\n' "${CTX_SIZE-unset}"
} >>"$ODS_COMPOSE_CAPTURE"
MOCK_COMPOSE
chmod +x "$compose_mock"
export ODS_COMPOSE_CAPTURE="$compose_capture"
DOCKER_COMPOSE_CMD="$compose_mock"
export GGUF_FILE=leaked-gguf LLM_MODEL=leaked-llm LEMONADE_MODEL=leaked-lemonade \
    MAX_CONTEXT=123 CTX_SIZE=456

COMPOSE_ARGS=(-f "$INSTALL_DIR/base.yml" -f "$INSTALL_DIR/overlay.yml")
WINDOWS_LEMONADE_COMPOSE_ARGS=(-f "$INSTALL_DIR/wrong-windows.yml")
compose_recreate_hermes || fail "Hermes recreate rejected the active COMPOSE_ARGS stack"
grep -Fxq "cwd=$INSTALL_DIR" "$compose_capture" \
    || fail "Hermes recreate did not execute from the install directory"
mapfile -t compose_call_args < <(sed -n 's/^arg=//p' "$compose_capture")
[[ "${compose_call_args[0]:-}" == -f \
    && "${compose_call_args[1]:-}" == "$INSTALL_DIR/base.yml" \
    && "${compose_call_args[2]:-}" == -f \
    && "${compose_call_args[3]:-}" == "$INSTALL_DIR/overlay.yml" \
    && "${compose_call_args[4]:-}" == up \
    && "${compose_call_args[5]:-}" == -d \
    && "${compose_call_args[6]:-}" == --force-recreate \
    && "${compose_call_args[7]:-}" == --no-deps \
    && "${compose_call_args[8]:-}" == hermes ]] \
    || fail "Hermes recreate did not preserve the exact active Compose argv"
for compose_env in GGUF_FILE LLM_MODEL LEMONADE_MODEL MAX_CONTEXT CTX_SIZE; do
    grep -Fxq "${compose_env}=unset" "$compose_capture" \
        || fail "Hermes recreate leaked ${compose_env} into Compose interpolation"
done

: >"$compose_capture"
unset COMPOSE_ARGS
WINDOWS_LEMONADE_COMPOSE_ARGS=(-f "$INSTALL_DIR/windows.yml")
compose_recreate_hermes || fail "Hermes recreate rejected the Windows Lemonade Compose stack"
grep -Fxq "arg=$INSTALL_DIR/windows.yml" "$compose_capture" \
    || fail "Hermes recreate did not fall back to WINDOWS_LEMONADE_COMPOSE_ARGS"

: >"$compose_capture"
unset WINDOWS_LEMONADE_COMPOSE_ARGS
printf '%s\n' "-f $INSTALL_DIR/persisted.yml -f $INSTALL_DIR/persisted-overlay.yml" \
    >"$INSTALL_DIR/.compose-flags"
compose_recreate_hermes || fail "Hermes recreate rejected the persisted Compose stack"
grep -Fxq "arg=$INSTALL_DIR/persisted.yml" "$compose_capture" \
    && grep -Fxq "arg=$INSTALL_DIR/persisted-overlay.yml" "$compose_capture" \
    || fail "Hermes recreate did not fall back to the persisted Compose stack"

rm -rf -- "$compose_hermes_tmp"
unset -f compose_recreate_hermes
unset ODS_COMPOSE_CAPTURE DOCKER_COMPOSE_CMD GGUF_FILE LLM_MODEL LEMONADE_MODEL MAX_CONTEXT CTX_SIZE
pass "Hermes recreation preserves the active stack and strips model overrides"

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

promote_env_block="$(function_block promote_full_model_env | grep -v '^[[:space:]]*#')"
for expected in \
    'write_env_value GGUF_FILE "$FULL_GGUF_FILE"' \
    'write_env_value LLM_MODEL "$FULL_LLM_MODEL"' \
    'write_env_value MAX_CONTEXT "$FULL_MAX_CONTEXT"' \
    'write_env_value CTX_SIZE "$FULL_MAX_CONTEXT"' \
    'full_model_env_matches' \
    'log_model_env_state'
do
    grep -qF "$expected" <<<"$promote_env_block" \
        || fail "full-model .env promotion must strictly persist ${expected}"
done
pass "full-model .env promotion is strict and self-diagnosing"

grep -qF 'promote_full_model_env "initial full-model promotion"' <<<"$active_code" \
    || fail "bootstrap upgrade must strictly promote .env before mutating runtime config"
grep -qF 'promote_full_model_env "pre-compose full-model promotion"' <<<"$active_code" \
    || fail "Docker hot-swap must reassert full-model .env immediately before compose recreate"
grep -qF 'promote_full_model_env "stale llama-server command repair"' <<<"$active_code" \
    || fail "stale llama-server command recovery must re-promote .env before its bounded retry"
pass "Docker hot-swap reasserts full-model .env before and during stale-command recovery"

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

cleanup_refresh_block="$(function_block refresh_lemonade_after_bootstrap_cleanup | grep -v '^[[:space:]]*#')"
grep -qF 'declare -p COMPOSE_ARGS' <<<"$cleanup_refresh_block" \
    || fail "Lemonade cleanup refresh must reuse the live compose args before requiring .compose-flags"
grep -qF 'compose_args=("${COMPOSE_ARGS[@]}")' <<<"$cleanup_refresh_block" \
    || fail "Lemonade cleanup refresh must copy the active compose stack"
assert_in_order "$cleanup_refresh_block" "Lemonade cleanup refresh compose args" \
    'declare -p COMPOSE_ARGS' \
    '[[ -f "$INSTALL_DIR/.compose-flags" ]]' \
    'up -d --force-recreate --no-deps llama-server'
pass "Lemonade cleanup refresh reuses active compose args before .compose-flags fallback"

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
grep -qF 'Resolve-ODSLemonadeModelId' <<<"$active_code" \
    || fail "Windows Lemonade hot-swap must resolve the runtime's exact full-model ID"
grep -qF 'write_env_value LEMONADE_MODEL "$model_id"' <<<"$active_code" \
    || fail "Windows Lemonade hot-swap must persist the verified full-model ID"
pass "Windows Lemonade hot-swap restarts native inference and persists the verified model ID"

restart_windows_lemonade_block="$(function_block restart_windows_lemonade_with_full_model | grep -v '^[[:space:]]*#')"
grep -qF 'ODS_LEMONADE_RESTART_PS_TIMEOUT' <<<"$restart_windows_lemonade_block" \
    || fail "Windows Lemonade restart helper must be bounded by a timeout"
grep -qF 'lemonade-bootstrap-restart.$(date +%Y%m%d-%H%M%S).$$.log' <<<"$restart_windows_lemonade_block" \
    || fail "Windows Lemonade restart helper output must go to a file, not a command-substitution pipe"
grep -qF '>"$ps_output_file" 2>&1' <<<"$restart_windows_lemonade_block" \
    || fail "Windows Lemonade restart helper must not capture PowerShell through an inherited stdout pipe"
grep -qF 'tail -c 12000 "$ps_output_file"' <<<"$restart_windows_lemonade_block" \
    || fail "Windows Lemonade restart diagnostics must be read back from the bounded log file"
grep -qF 'resolve_live_windows_lemonade_model_id "$lemonade_port" "$target_gguf"' <<<"$restart_windows_lemonade_block" \
    || fail "Windows Lemonade timeout/no-output fallback must resolve the live full-model ID"
grep -qF 'PowerShell restart helper did not finish cleanly, but Lemonade live state matches' <<<"$restart_windows_lemonade_block" \
    || fail "Windows Lemonade timeout fallback must only continue after live-state proof"
grep -qF 'Resolved native Windows Lemonade model ID from live state' <<<"$restart_windows_lemonade_block" \
    || fail "Windows Lemonade no-output fallback must require live-state proof"
grep -qF 'curl -sf --max-time 240 -X POST' <<<"$restart_windows_lemonade_block" \
    || fail "Windows Lemonade live-state fallback must still prove chat completion"
pass "Windows Lemonade restart cannot leave a half-promoted route after helper timeout"

assert_in_order "$restart_windows_lemonade_block" "Windows Lemonade context propagation" \
    'target_context="$(read_env_value CTX_SIZE)"' \
    'ODS_WIN_CONTEXT_SIZE=$target_context' \
    '$null = [int]::TryParse([string]$env:ODS_WIN_CONTEXT_SIZE, [ref]$contextSize)' \
    'ContextSize = $contextSize' \
    'verify_windows_lemonade_loaded_context "$lemonade_port" "$model_id" "$target_gguf" "$target_context"' \
    'write_env_value LEMONADE_MODEL "$model_id"'
pass "Windows Lemonade restart propagates and verifies the promoted context before commit"

host_agent_notify_block="$(function_block notify_host_agent_model_status | grep -v '^[[:space:]]*#')"
grep -qF '/v1/model/status' <<<"$host_agent_notify_block" \
    || fail "bootstrap upgrade must notify host-agent model status after full-model completion"
grep -qF 'Authorization: Bearer $key' <<<"$host_agent_notify_block" \
    || fail "host-agent model status notification must authenticate with ODS_AGENT_KEY"
grep -qF 'ss -ltnH' <<<"$host_agent_notify_block" \
    || fail "host-agent model status notification must discover the actual listening bind"
grep -qF 'ip -o -4 addr show' <<<"$host_agent_notify_block" \
    || fail "host-agent model status notification must include docker bridge interface fallbacks"
grep -qF 'for host in "${hosts[@]}"' <<<"$host_agent_notify_block" \
    || fail "host-agent model status notification must use the discovered host set"
grep -qF '172.17.0.1' <<<"$host_agent_notify_block" \
    || fail "host-agent model status notification must retain the legacy Linux docker-bridge fallback"
final_status_block="$(tail -n 90 "$TARGET" | grep -v '^[[:space:]]*#')"
assert_in_order "$final_status_block" "full-model route reconciliation" \
    'write_status "complete" 100 "$TOTAL_BYTES" "$TOTAL_BYTES" 0 ""' \
    'notify_host_agent_model_status || true'
pass "bootstrap upgrade reconciles host-agent route after full-model completion"

verify_context_block="$(function_block verify_windows_lemonade_loaded_context | grep -v '^[[:space:]]*#')"
grep -qF 'all_models_loaded' <<<"$verify_context_block" \
    || fail "Windows Lemonade loaded-context verifier must inspect health all_models_loaded"
grep -qF 'recipe_options' <<<"$verify_context_block" \
    || fail "Windows Lemonade loaded-context verifier must inspect recipe_options"
grep -qF 'ctx_size' <<<"$verify_context_block" \
    || fail "Windows Lemonade loaded-context verifier must inspect ctx_size"
grep -qF '$actualContext -lt $expectedContext' <<<"$verify_context_block" \
    || fail "Windows Lemonade loaded-context verifier must reject undersized contexts"
pass "Windows Lemonade loaded-context verifier rejects stale bootstrap contexts"

grep -qF 'patch_hermes_model_after_swap' <<<"$active_code" \
    || fail "Windows Lemonade hot-swap must patch Hermes off the bootstrap model"
grep -qF 'compose_recreate_hermes' "$TARGET" \
    || fail "Hermes model reconciliation must use the active Compose stack"
if grep -qF '$DOCKER_CMD restart ods-hermes' "$TARGET"; then
    fail "Hermes model reconciliation must not reuse stale Docker Desktop bind mounts"
fi
windows_activation_block="$(function_block activate_windows_lemonade_full_model | grep -v '^[[:space:]]*#')"
assert_in_order "$windows_activation_block" "Windows Lemonade activation" \
    'restart_windows_lemonade_with_full_model' \
    'model_id="$(read_env_value LEMONADE_MODEL)"' \
    'refresh_windows_lemonade_litellm_after_swap "$model_id"' \
    'patch_hermes_model_after_swap' \
    'recreate_windows_lemonade_openclaw' \
    'verify_windows_lemonade_openclaw_model_env "$model_id"' \
    'request_windows_switchboard_route_reconciliation' \
    'verify_windows_lemonade_downstream_route "$model_id" "full model route"'

switchboard_reconcile_block="$(function_block request_windows_switchboard_route_reconciliation | grep -v '^[[:space:]]*#')"
grep -qF 'ODS_MODEL_SWITCHBOARD' <<<"$switchboard_reconcile_block" \
    || fail "Windows Lemonade bootstrap reconciliation must be gated by enabled switchboard mode"
grep -qF '/v1/model/status' <<<"$switchboard_reconcile_block" \
    || fail "Windows Lemonade bootstrap reconciliation must schedule host-agent route proof"
grep -qF 'Authorization: Bearer $key' <<<"$switchboard_reconcile_block" \
    || fail "Windows Lemonade bootstrap reconciliation must authenticate with ODS_AGENT_KEY"

windows_lemonade_block="$(awk '
    /^if \[\[ "\$_windows_lemonade_swap_applies" == "true" \]\]; then/ { in_block=1 }
    in_block { print }
    in_block && /^elif \[\[ "\$_windows_native_llama_swap_applies"/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
assert_in_order "$windows_lemonade_block" "Windows Lemonade main path" \
    'activate_windows_lemonade_full_model' \
    'HOT_SWAP_VERIFIED=true' \
    'reconcile_ods_managed_pixel_model' \
    'discard_active_model_config_snapshot' \
    'discard_bootstrap_model_backup_after_windows_swap'
grep -qF 'windows_lemonade_swap_failed "the managed Pixel route could not be reconciled" true' <<<"$windows_lemonade_block" \
    || fail "Windows Lemonade must restore and verify both inference and Pixel after Pixel promotion failure"
pass "Windows Lemonade verifies the exact downstream route before commit"

snapshot_block="$(function_block snapshot_active_model_config | grep -v '^[[:space:]]*#')"
grep -qF '"$ACTIVE_CONFIG_SNAPSHOT_DIR/litellm-lemonade"' <<<"$snapshot_block" \
    || fail "every model transaction must snapshot the canonical Lemonade route"
grep -qF 'extensions/services/hermes/cli-config.yaml.template' <<<"$snapshot_block" \
    || fail "Windows Lemonade transaction must snapshot the Hermes template"
grep -qF 'data/hermes/config.yaml' <<<"$snapshot_block" \
    || fail "Windows Lemonade transaction must snapshot the Hermes live config"
grep -qF 'config/litellm/lemonade.yaml' <<<"$snapshot_block" \
    || fail "Windows Lemonade transaction must snapshot the active LiteLLM config"
grep -qF 'windows-lemonade.included' <<<"$snapshot_block" \
    || fail "dependent snapshots must be explicitly scoped to Windows Lemonade"

restore_block="$(function_block restore_active_model_config | grep -v '^[[:space:]]*#')"
grep -qF '"$ACTIVE_CONFIG_SNAPSHOT_DIR/litellm-lemonade"' <<<"$restore_block" \
    || fail "every model rollback must restore the canonical Lemonade route"
grep -qF 'windows-lemonade/hermes-template' <<<"$restore_block" \
    || fail "Windows Lemonade rollback must restore the Hermes template"
grep -qF 'windows-lemonade/hermes-live' <<<"$restore_block" \
    || fail "Windows Lemonade rollback must restore the Hermes live config"
grep -qF 'windows-lemonade/litellm-lemonade' <<<"$restore_block" \
    || fail "Windows Lemonade rollback must restore the LiteLLM config"
pass "Windows Lemonade transaction snapshots and restores dependent configs"

litellm_refresh_block="$(function_block refresh_windows_lemonade_litellm_after_swap | grep -v '^[[:space:]]*#')"
grep -qF -- '--lemonade-model-id "$model_id"' <<<"$litellm_refresh_block" \
    || fail "Windows Lemonade LiteLLM renderer must receive the exact resolved model ID"
grep -qF 'model: openai/${model_id}' <<<"$litellm_refresh_block" \
    || fail "Windows Lemonade rendered config must verify the exact resolved model ID"
grep -qF '$DOCKER_CMD restart ods-litellm' <<<"$litellm_refresh_block" \
    || fail "Windows Lemonade must reload LiteLLM after regenerating its config"

openclaw_refresh_block="$(function_block recreate_windows_lemonade_openclaw | grep -v '^[[:space:]]*#')"
dependent_state_block="$(function_block windows_lemonade_container_present | grep -v '^[[:space:]]*#')"
grep -qF '$DOCKER_CMD ps -a' <<<"$dependent_state_block" \
    || fail "Windows Lemonade must detect stopped or running dependent containers before the transaction"
grep -qF 'env -u GGUF_FILE -u LLM_MODEL -u LEMONADE_MODEL -u MAX_CONTEXT -u CTX_SIZE' <<<"$openclaw_refresh_block" \
    || fail "Windows Lemonade OpenClaw recreate must let the restored/current .env win interpolation"
grep -qF 'up -d --force-recreate --no-deps openclaw' <<<"$openclaw_refresh_block" \
    || fail "Windows Lemonade must force-recreate an existing OpenClaw without dependencies"
openclaw_verify_block="$(function_block verify_windows_lemonade_openclaw_model_env | grep -v '^[[:space:]]*#')"
grep -qF '$DOCKER_CMD inspect --type container' <<<"$openclaw_verify_block" \
    || fail "Windows Lemonade must inspect the recreated OpenClaw environment"
grep -qF 'LEMONADE_MODEL' <<<"$openclaw_verify_block" \
    || fail "Windows Lemonade OpenClaw proof must prefer the exact Lemonade model ID"
grep -qF 'actual_model="extra.${gguf_file}"' <<<"$openclaw_verify_block" \
    || fail "Windows Lemonade OpenClaw proof must mirror OpenClaw's GGUF fallback"
grep -qF 'actual_model" == "$expected_model' <<<"$openclaw_verify_block" \
    || fail "Windows Lemonade OpenClaw proof must fail on stale model identity"

downstream_block="$(function_block verify_windows_lemonade_downstream_route | grep -v '^[[:space:]]*#')"
grep -qF 'read_env_value HERMES_LLM_BASE_URL' <<<"$downstream_block" \
    || fail "Windows Lemonade route proof must use the configured Hermes downstream route"
grep -qF 'read_env_value LITELLM_KEY' <<<"$downstream_block" \
    || fail "Windows Lemonade route proof must fall back to LITELLM_KEY for older installs"
grep -qF '$DOCKER_CMD exec "$route_container" curl' <<<"$downstream_block" \
    || fail "Windows Lemonade route proof must execute from the downstream Hermes container when present"
grep -qF 'request_body="{\"model\":\"${escaped_model}\"' <<<"$downstream_block" \
    || fail "Windows Lemonade route proof must request the exact resolved model ID"
pass "Windows Lemonade refreshes LiteLLM/OpenClaw and proves the consumer route"

rollback_block="$(function_block rollback_windows_lemonade_swap | grep -v '^[[:space:]]*#')"
rollback_dependents_block="$(function_block restart_windows_lemonade_dependents_after_rollback | grep -v '^[[:space:]]*#')"
grep -qF '$DOCKER_CMD restart ods-litellm' <<<"$rollback_dependents_block" \
    || fail "Windows Lemonade rollback must restart LiteLLM with its restored config"
grep -qF 'compose_recreate_hermes' <<<"$rollback_dependents_block" \
    || fail "Windows Lemonade rollback must recreate Hermes with its restored config"
grep -qF 'recreate_windows_lemonade_openclaw' <<<"$rollback_dependents_block" \
    || fail "Windows Lemonade rollback must recreate a previously present OpenClaw"
assert_in_order "$rollback_block" "Windows Lemonade rollback" \
    'previous_gguf="$(snapshot_env_value GGUF_FILE)"' \
    'restore_bootstrap_model_after_windows_swap_failure' \
    'restore_active_model_config' \
    'restart_windows_lemonade_with_previous_model "$previous_gguf"' \
    'restart_windows_lemonade_dependents_after_rollback' \
    'verify_windows_lemonade_openclaw_model_env "$previous_model_id"' \
    'verify_windows_lemonade_downstream_route "$previous_model_id" "previous model route"' \
    'reconcile_ods_managed_pixel_model "$previous_llm_model"' \
    'Rollback verified: the previous model completed through the restored downstream route.'
pass "Windows Lemonade rollback restarts and proves the previous routed model and managed Pixel route"

for injected_failure in native model-id litellm hermes openclaw openclaw-env reconcile route; do
    if ! (
        eval "$windows_activation_block"
        failure_stage="$injected_failure"
        calls=()
        rollback_reason=""

        restart_windows_lemonade_with_full_model() {
            calls+=(native)
            [[ "$failure_stage" != "native" ]]
        }
        read_env_value() {
            [[ "$failure_stage" == "model-id" ]] && return 0
            printf '%s\n' 'user.Qwen3.5-9B-Q4_K_M.gguf'
        }
        refresh_windows_lemonade_litellm_after_swap() {
            calls+=(litellm)
            [[ "$failure_stage" != "litellm" ]]
        }
        patch_hermes_model_after_swap() {
            calls+=(hermes)
            [[ "$failure_stage" != "hermes" ]]
        }
        recreate_windows_lemonade_openclaw() {
            calls+=(openclaw)
            [[ "$failure_stage" != "openclaw" ]]
        }
        verify_windows_lemonade_openclaw_model_env() {
            calls+=(openclaw-env)
            [[ "$failure_stage" != "openclaw-env" ]]
        }
        request_windows_switchboard_route_reconciliation() {
            calls+=(reconcile)
            [[ "$failure_stage" != "reconcile" ]]
        }
        verify_windows_lemonade_downstream_route() {
            calls+=(route)
            [[ "$failure_stage" != "route" ]]
        }
        windows_lemonade_swap_failed() {
            rollback_reason="$1"
            calls+=(rollback)
            return 1
        }

        if activate_windows_lemonade_full_model; then
            exit 1
        fi
        [[ -n "$rollback_reason" ]] || exit 1
        last_call="${calls[$(( ${#calls[@]} - 1 ))]}"
        [[ "$last_call" == "rollback" ]] || exit 1

        expected=(native)
        case "$failure_stage" in
            native|model-id) ;;
            litellm) expected+=(litellm) ;;
            hermes) expected+=(litellm hermes) ;;
            openclaw) expected+=(litellm hermes openclaw) ;;
            openclaw-env) expected+=(litellm hermes openclaw openclaw-env) ;;
            reconcile) expected+=(litellm hermes openclaw openclaw-env reconcile) ;;
            route) expected+=(litellm hermes openclaw openclaw-env reconcile route) ;;
        esac
        expected+=(rollback)
        [[ "${calls[*]}" == "${expected[*]}" ]]
    ); then
        fail "injected Windows Lemonade ${injected_failure} failure did not stop and enter rollback"
    fi
done
pass "Windows Lemonade activation rolls back every injected post-swap failure"

docker_swap_block="$(awk '
    /^elif \[\[ -n "\$DOCKER_CMD" \]\] && \$DOCKER_CMD ps/ { in_block=1 }
    in_block { print }
    in_block && /^elif \[\[ -f "\$INSTALL_DIR\/data\/\.llama-server\.pid" \]\]/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
grep -qF 'if [[ "$_gpu_backend" == "amd" ]]' <<<"$docker_swap_block" \
    || fail "Docker Lemonade route rendering must be gated to the AMD backend"
assert_in_order "$docker_swap_block" "Docker model transaction commit" \
    '--surface litellm-lemonade' \
    '$DOCKER_CMD restart ods-litellm' \
    'verify_model_completion_route' \
    'HOT_SWAP_VERIFIED=true' \
    'reconcile_ods_managed_pixel_model' \
    'discard_active_model_config_snapshot'
grep -qF 'Full model served, but ODS could not reconcile the managed Pixel route.' <<<"$docker_swap_block" \
    || fail "Docker model promotion must fail honestly when Pixel reconciliation fails"
grep -qF 'restore_docker_llama_server_after_swap_failure "$_health_url" true' <<<"$docker_swap_block" \
    || fail "Docker Pixel promotion failure must request managed Pixel rollback verification"
pass "Docker model transaction commits only after renderer, reload, completion proof, and Pixel reconciliation"

docker_rollback_block="$(function_block restore_docker_llama_server_after_swap_failure | grep -v '^[[:space:]]*#')"
assert_in_order "$docker_rollback_block" "Docker model transaction rollback" \
    'previous_llm_model="$(snapshot_env_value LLM_MODEL)"' \
    'previous_model_id="$(snapshot_env_value LEMONADE_MODEL)"' \
    'restore_active_model_config' \
    'compose_recreate_llama_server_with_retry' \
    '$DOCKER_CMD restart ods-litellm' \
    'verify_model_completion_route' \
    'reconcile_ods_managed_pixel_model "$previous_llm_model"'
pass "Docker rollback restores and proves the previous routed model and managed Pixel route"

completion_route_block="$(function_block verify_model_completion_route | grep -v '^[[:space:]]*#')"
grep -qF '"choices"[[:space:]]*:' <<<"$completion_route_block" \
    || fail "model route proof must require a completion choices payload"
grep -qF '"error"[[:space:]]*:' <<<"$completion_route_block" \
    || fail "model route proof must reject an error payload"
grep -qF 'ODS_MODEL_ROUTE_ATTEMPTS' <<<"$completion_route_block" \
    || fail "model route proof must expose a bounded attempt count"
grep -qF 'ODS_MODEL_ROUTE_TIMEOUT' <<<"$completion_route_block" \
    || fail "model route proof must expose a bounded request timeout"
pass "Docker model route proof is bounded and rejects error responses"

grep -qF 'switchboard_mode="$(read_env_value ODS_MODEL_SWITCHBOARD' <<<"$active_code" \
    || fail "Hermes post-swap patch helper must read switchboard mode"
grep -qF '_hermes_switchboard_mode="$(read_env_value ODS_MODEL_SWITCHBOARD' <<<"$active_code" \
    || fail "Docker full-model swap must read switchboard mode before patching Hermes"
grep -qF 'new_model="ods/current"' <<<"$active_code" \
    || fail "Hermes post-swap patch helper must use the stable switchboard alias"
grep -qF '_hermes_new_model="ods/current"' <<<"$active_code" \
    || fail "Docker full-model swap must patch Hermes to the stable switchboard alias"
grep -qF 'hermes_base_url="http://litellm:4000/v1"' <<<"$active_code" \
    || fail "Switchboard Hermes patch helper must route through LiteLLM"
grep -qF '_hermes_base_url="http://litellm:4000/v1"' <<<"$active_code" \
    || fail "Switchboard Docker swap must route Hermes through LiteLLM"
pass "Hermes post-swap patch uses switchboard stable alias when enabled"

perplexica_update_block="$(awk '
    /Updating Perplexica config to point at/ { in_block=1 }
    in_block { print }
    in_block && /Perplexica config update failed/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
grep -qF 'ods_detect_python_cmd' <<<"$perplexica_update_block" \
    || fail "Perplexica post-swap update must reject Windows Store Python aliases"
grep -qF 'read_env_value HERMES_LLM_BASE_URL' <<<"$perplexica_update_block" \
    || fail "Lemonade Perplexica updates must use the working LiteLLM route"
grep -qF 'read_env_value LEMONADE_MODEL' <<<"$perplexica_update_block" \
    || fail "Perplexica post-swap update must use the exact Lemonade model ID"
grep -qF 'read_env_value ODS_MODEL_SWITCHBOARD' <<<"$perplexica_update_block" \
    || fail "Perplexica post-swap update must branch on switchboard mode"
grep -qF '_px_model="ods/current"' <<<"$perplexica_update_block" \
    || fail "Switchboard Perplexica updates must keep the stable model alias"
grep -qF '_px_base_url="http://litellm:4000/v1"' <<<"$perplexica_update_block" \
    || fail "Switchboard Perplexica updates must route through LiteLLM"
pass "Perplexica post-swap update uses runnable Python and the exact LiteLLM/switchboard model route"

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

grep -qF 'write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES"' <<<"$windows_lemonade_block" \
    || fail "Windows Lemonade hot-swap failure must mark bootstrap status failed"
grep -qF 'WINDOWS_LEMONADE_ROLLBACK_VERIFIED' <<<"$windows_lemonade_block" \
    || fail "Windows Lemonade status must distinguish proven rollback from an attempted rollback"
grep -qF 'exit 1' <<<"$windows_lemonade_block" \
    || fail "Windows Lemonade hot-swap failure must exit non-zero"
pass "Windows Lemonade hot-swap failure is honest"

docker_timeout_block="$(awk '
    /llama-server health check timed out/ { in_block=1 }
    in_block { print }
    in_block && /exit 1/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
grep -qF 'ODS_BOOTSTRAP_HEALTH_ATTEMPTS' <<<"$active_code" \
    || fail "Docker hot-swap health wait must expose a bounded attempt override"
grep -qF 'ODS_BOOTSTRAP_CONTAINER_FAILURE_GRACE_ATTEMPTS' <<<"$active_code" \
    || fail "Docker hot-swap must expose a bounded failed-container grace override"
grep -qF 'is_windows_bash' <<<"$active_code" \
    || fail "Docker hot-swap restart grace must account for slower Windows Docker Desktop transitions"
grep -qF 'continuing within restart grace' <<<"$active_code" \
    || fail "Docker hot-swap must tolerate transient failed/restarting container states before rollback"
pass "Docker hot-swap restart grace is bounded and visible"
grep -qF 'write_status "failed" 100 "$TOTAL_BYTES" "$TOTAL_BYTES"' <<<"$docker_timeout_block" \
    || fail "Docker hot-swap timeout must mark bootstrap status failed with real byte counts"
grep -qF 'exit 1' <<<"$docker_timeout_block" \
    || fail "Docker hot-swap timeout must exit non-zero"
pass "Docker hot-swap timeout is honest"
