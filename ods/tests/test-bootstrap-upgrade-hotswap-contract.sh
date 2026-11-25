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
grep -qF "trap 'stop_download_monitor; cleanup_bootstrap_pixel_model_transaction; release_model_router_swap_gate; release_model_lifecycle_lock; release_upgrade_lock' EXIT" <<<"$active_code" \
    || fail "model swap admission must reopen on every normal or failed exit"
top_level_swap="$(awk '
    /acquire_model_lifecycle_lock \|\| fail "Could not serialize background full-model activation/ { in_block=1 }
    in_block { print }
    /Snapshotting active model config before full-model swap/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
assert_in_order "$top_level_swap" "bootstrap swap drain boundary" \
    'acquire_model_lifecycle_lock ||' \
    'acquire_bootstrap_pixel_model_transaction' \
    'Could not safely drain Portal work before full-model activation.' \
    'acquire_model_router_swap_gate' \
    'Could not safely drain model traffic before full-model activation.' \
    'Snapshotting active model config before full-model swap'
pass "bootstrap promotion closes, drains, reports gate failure, and releases router admission"

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
hermes_docker_pathconv=""
docker() {
    hermes_docker_calls=$((hermes_docker_calls + 1))
    hermes_docker_pathconv="${MSYS_NO_PATHCONV:-}"
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
[[ "$hermes_docker_pathconv" == "1" ]] \
    || fail "Hermes live patch helper did not disable Git Bash path conversion for container paths"
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

hermes_prewarm_block="$(awk '
    /Pre-warming Hermes system prompt/ { in_block=1 }
    in_block { print }
    in_block && /\/opt\/hermes\/\.venv\/bin\/hermes/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
grep -qF 'MSYS_NO_PATHCONV=1 $DOCKER_CMD exec ods-hermes timeout 90' <<<"$hermes_prewarm_block" \
    || fail "Hermes pre-warm must disable Git Bash path conversion for the container executable"
grep -qF '/opt/hermes/.venv/bin/hermes -z "ping" --yolo' <<<"$hermes_prewarm_block" \
    || fail "Hermes pre-warm must preserve the exact container executable and argv"
pass "Hermes pre-warm preserves its container-absolute executable on Windows"

compose_hermes_block="$(function_block compose_recreate_hermes | grep -v '^[[:space:]]*#')"
windows_compose_loader_block="$(function_block load_windows_lemonade_compose_args | grep -v '^[[:space:]]*#')"
eval "$compose_hermes_block"
eval "$windows_compose_loader_block"
eval "$(function_block validate_bootstrap_compose_args)"
log() { :; }
compose_hermes_tmp="$(mktemp -d "${TMPDIR:-/tmp}/ods-hermes-compose.XXXXXX")"
INSTALL_DIR="$compose_hermes_tmp/install"
mkdir -p "$INSTALL_DIR/scripts"
cp "$ROOT_DIR/scripts/compose-cache-policy.py" "$INSTALL_DIR/scripts/"
compose_capture="$compose_hermes_tmp/calls"
compose_mock="$compose_hermes_tmp/mock-compose"
cat >"$compose_mock" <<'MOCK_COMPOSE'
#!/usr/bin/env bash
{
    printf 'cwd=%s\n' "$PWD"
    printf 'arg=%s\n' "$@"
    printf 'GGUF_FILE=%s\n' "${GGUF_FILE-unset}"
    printf 'LLM_MODEL=%s\n' "${LLM_MODEL-unset}"
    printf 'MAX_CONTEXT=%s\n' "${MAX_CONTEXT-unset}"
    printf 'CTX_SIZE=%s\n' "${CTX_SIZE-unset}"
} >>"$ODS_COMPOSE_CAPTURE"
MOCK_COMPOSE
chmod +x "$compose_mock"
export ODS_COMPOSE_CAPTURE="$compose_capture"
DOCKER_COMPOSE_CMD="$compose_mock"
export GGUF_FILE=leaked-gguf LLM_MODEL=leaked-llm MAX_CONTEXT=123 CTX_SIZE=456

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
for compose_env in GGUF_FILE LLM_MODEL MAX_CONTEXT CTX_SIZE; do
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

: >"$compose_capture"
unset WINDOWS_LEMONADE_COMPOSE_ARGS
declare -a WINDOWS_LEMONADE_COMPOSE_ARGS=()
rm -f "$INSTALL_DIR/.compose-flags"
mkdir -p "$INSTALL_DIR/logs"
touch "$INSTALL_DIR/recovered.yml" "$INSTALL_DIR/recovered-overlay.yml"
printf '%s\n' \
    $'compose_flags=-f recovered.yml -f recovered-overlay.yml\r' \
    >"$INSTALL_DIR/logs/compose-launch.txt"
is_windows_bash() { return 0; }
compose_recreate_hermes || fail "Hermes recreate did not recover the Windows launch stack"
grep -Fxq "arg=recovered.yml" "$compose_capture" \
    && grep -Fxq "arg=recovered-overlay.yml" "$compose_capture" \
    || fail "Hermes recreate changed the recovered Windows launch stack"
grep -Fxq -- "-f recovered.yml -f recovered-overlay.yml" "$INSTALL_DIR/.compose-flags" \
    || fail "Hermes recreate did not persist the recovered Windows launch stack"

: >"$compose_capture"
WINDOWS_LEMONADE_COMPOSE_ARGS=()
rm -f "$INSTALL_DIR/.compose-flags"
printf '%s\n' "compose_flags=-f missing.yml" >"$INSTALL_DIR/logs/compose-launch.txt"
if compose_recreate_hermes; then
    fail "Hermes recreate accepted a recovered stack with a missing Compose file"
fi
[[ ! -e "$INSTALL_DIR/.compose-flags" && ! -s "$compose_capture" ]] \
    || fail "Hermes recreate persisted or executed an invalid recovered stack"

WINDOWS_LEMONADE_COMPOSE_ARGS=()
printf '%s\n' "compose_flags=--env-file .env" >"$INSTALL_DIR/logs/compose-launch.txt"
if compose_recreate_hermes; then
    fail "Hermes recreate accepted a recovered stack without any Compose file"
fi
[[ ! -e "$INSTALL_DIR/.compose-flags" ]] \
    || fail "Hermes recreate persisted a recovered stack without a Compose file"

rm -rf -- "$compose_hermes_tmp"
unset -f compose_recreate_hermes load_windows_lemonade_compose_args is_windows_bash log
unset ODS_COMPOSE_CAPTURE DOCKER_COMPOSE_CMD GGUF_FILE LLM_MODEL MAX_CONTEXT CTX_SIZE
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

# AMD runs the llama.cpp server image like every GPU: the Linux Docker swap
# has no Lemonade health route, model-id bookkeeping, LiteLLM re-render or
# post-cleanup refresh, and AMD is not exempt from crash-loop detection or
# the --model inspection.
docker_swap_block="$(awk '
    /Restarting llama-server container \(backend:/ { in_block=1 }
    in_block { print }
    /Phase 5b: Remove bootstrap model only after verified full-model serving/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
[[ -n "$docker_swap_block" ]] || fail "Docker hot-swap block not found"
for retired in '/api/v1' 'model_loaded' 'LEMONADE_MODEL' 'litellm-lemonade' 'extra.' '"$_gpu_backend" != "amd"' '"$_gpu_backend" == "amd"'; do
    if grep -qF -- "$retired" <<<"$docker_swap_block"; then
        fail "Linux Docker hot-swap still carries the retired Lemonade branch: $retired"
    fi
done
grep -qF '_health_url="http://127.0.0.1:${OLLAMA_PORT:-8080}/health"' <<<"$docker_swap_block" \
    || fail "Linux Docker hot-swap must wait on llama.cpp /health for every GPU"
if grep -qF 'refresh_lemonade_after_bootstrap_cleanup' <<<"$active_code"; then
    fail "bootstrap cleanup must not refresh a Lemonade runtime on Linux"
fi
pass "Linux Docker hot-swap treats AMD as llama.cpp (no Lemonade health, ids or re-render)"

# The legacy OpenClaw extension was removed; nothing may recreate or inspect
# its container. Pixel's host OpenClaw runtime is reconciled separately.
if grep -Eq 'ods-openclaw|_lemonade_openclaw|LEMONADE_OPENCLAW|force-recreate( --no-deps)? openclaw' <<<"$active_code"; then
    fail "bootstrap upgrade must not recreate or inspect the removed legacy OpenClaw container"
fi
pass "bootstrap upgrade no longer touches the removed legacy OpenClaw container"

grep -qF 'inspect ods-llama-server --format' <<<"$active_code" \
    || fail "hot-swap must inspect the recreated container command"
grep -qF '"/models/${FULL_GGUF_FILE}"' <<<"$active_code" \
    || fail "hot-swap must assert the running command points at the full GGUF"
pass "hot-swap asserts the running command uses the full GGUF"

# The Windows package retired ODS's Lemonade on Windows: no Lemonade model
# transaction, rollback or dependent-config snapshot is left here.
for retired in activate_windows_lemonade_full_model windows_lemonade_swap_failed \
        rollback_windows_lemonade_swap refresh_windows_lemonade_litellm_after_swap \
        verify_windows_lemonade_downstream_route capture_windows_lemonade_dependent_state \
        restart_windows_lemonade_dependents_after_rollback move_bootstrap_model_aside_for_windows_swap \
        '"$_windows_lemonade_swap_applies" == "true"' 'windows-lemonade.included'; do
    if grep -qF -- "$retired" <<<"$active_code"; then
        fail "the retired Windows Lemonade transaction is still present: $retired"
    fi
done
pass "the retired Windows Lemonade transaction is gone"

host_agent_notify_block="$(function_block notify_host_agent_model_status | grep -v '^[[:space:]]*#')"
grep -qF '/v1/model/status' <<<"$host_agent_notify_block" \
    || fail "bootstrap upgrade must notify host-agent model status after full-model completion"
grep -qF -- "-H @<(printf 'Authorization: Bearer %s\\n' \"\$key\")" <<<"$host_agent_notify_block" \
    || fail "host-agent model status notification must authenticate with ODS_AGENT_KEY through a header file, never argv"
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

grep -qF 'patch_hermes_model_after_swap' <<<"$active_code" \
    || fail "the Windows native hot-swap must patch Hermes off the bootstrap model"
grep -qF 'compose_recreate_hermes' "$TARGET" \
    || fail "Hermes model reconciliation must use the active Compose stack"
if grep -qF '$DOCKER_CMD restart ods-hermes' "$TARGET"; then
    fail "Hermes model reconciliation must not reuse stale Docker Desktop bind mounts"
fi
hermes_patch_block="$(function_block patch_hermes_model_after_swap | grep -v '^[[:space:]]*#')"
if grep -qE 'extra\.|LEMONADE_MODEL' <<<"$hermes_patch_block"; then
    fail "the Hermes patch must use the GGUF llama-server serves, not a Lemonade id"
fi
pass "the Windows native hot-swap patches Hermes to the GGUF llama-server serves"

snapshot_block="$(function_block snapshot_active_model_config | grep -v '^[[:space:]]*#')"
restore_block="$(function_block restore_active_model_config | grep -v '^[[:space:]]*#')"
if grep -qi 'lemonade' <<<"$snapshot_block$restore_block"; then
    fail "model transactions must not snapshot or restore Lemonade configs"
fi
pass "model transactions snapshot and restore no Lemonade configs"

docker_swap_block="$(awk '
    /^elif \[\[ -n "\$DOCKER_CMD" \]\] && \$DOCKER_CMD ps/ { in_block=1 }
    in_block { print }
    in_block && /^elif \[\[ -f "\$INSTALL_DIR\/data\/\.llama-server\.pid" \]\]/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
# Every GPU's container serves the GGUF it was started with; LiteLLM's local
# route needs no re-render after the swap.
if grep -qF -- '--surface litellm-lemonade' <<<"$docker_swap_block"; then
    fail "the Docker model transaction must not render the retired Lemonade route"
fi
assert_in_order "$docker_swap_block" "Docker model transaction commit" \
    'HOT_SWAP_VERIFIED=true' \
    'reconcile_ods_managed_pixel_model' \
    'discard_active_model_config_snapshot'
grep -qF 'Full model served, but ODS could not reconcile the managed Pixel route.' <<<"$docker_swap_block" \
    || fail "Docker model promotion must fail honestly when Pixel reconciliation fails"
grep -qF 'restore_docker_llama_server_after_swap_failure "$_health_url" true' <<<"$docker_swap_block" \
    || fail "Docker Pixel promotion failure must request managed Pixel rollback verification"
pass "Docker model transaction commits only after health and Pixel reconciliation"

docker_rollback_block="$(function_block restore_docker_llama_server_after_swap_failure | grep -v '^[[:space:]]*#')"
assert_in_order "$docker_rollback_block" "Docker model transaction rollback" \
    'previous_llm_model="$(snapshot_env_value LLM_MODEL)"' \
    'restore_active_model_config' \
    'compose_recreate_llama_server_with_retry' \
    'reconcile_ods_managed_pixel_model "$previous_llm_model"'
for retired in LEMONADE_MODEL 'restart ods-litellm'; do
    if grep -qF -- "$retired" <<<"$docker_rollback_block"; then
        fail "Docker rollback still restores the retired Lemonade route: $retired"
    fi
done
pass "Docker rollback restores the previous model and managed Pixel route"

grep -qF 'switchboard_mode="$(read_env_value ODS_MODEL_SWITCHBOARD' <<<"$active_code" \
    || fail "Hermes post-swap patch helper must read switchboard mode"
grep -qF '_hermes_switchboard_mode="$(read_env_value ODS_MODEL_SWITCHBOARD' <<<"$active_code" \
    || fail "Docker full-model swap must read switchboard mode before patching Hermes"
grep -qF 'new_model="ods/current"' <<<"$active_code" \
    || fail "Hermes post-swap patch helper must use the stable switchboard alias"
grep -qF '_hermes_new_model="ods/current"' <<<"$active_code" \
    || fail "Docker full-model swap must patch Hermes to the stable switchboard alias"
grep -qF 'hermes_base_url="http://model-router:9099/v1"' <<<"$active_code" \
    || fail "Switchboard Hermes patch helper must route through model-router"
grep -qF '_hermes_base_url="http://model-router:9099/v1"' <<<"$active_code" \
    || fail "Switchboard Docker swap must route Hermes through model-router"
pass "Hermes post-swap patch uses switchboard stable alias when enabled"

perplexica_update_block="$(awk '
    /Updating Perplexica config to point at/ { in_block=1 }
    in_block { print }
    in_block && /Perplexica config update failed/ { exit }
' "$TARGET" | grep -v '^[[:space:]]*#')"
grep -qF 'ods_detect_python_cmd' <<<"$perplexica_update_block" \
    || fail "Perplexica post-swap update must reject Windows Store Python aliases"
# llama-server serves the GGUF file name on every runtime; there is no
# separate Lemonade model id to look up.
grep -qF '_px_model="$FULL_GGUF_FILE"' <<<"$perplexica_update_block" \
    || fail "Perplexica post-swap update must name the GGUF llama-server serves"
if grep -qF 'LEMONADE_MODEL' <<<"$perplexica_update_block"; then
    fail "Perplexica post-swap update must not read the retired Lemonade model id"
fi
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

# llama.cpp serves only the --model it started with, so removing the
# bootstrap GGUF needs no runtime refresh on any GPU (Lemonade advertised
# every file in its models directory).
if grep -qF 'refresh_lemonade_after_bootstrap_cleanup' <<<"$active_code"; then
    fail "bootstrap cleanup must not refresh the retired Lemonade runtime"
fi
pass "bootstrap cleanup needs no runtime refresh"

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
