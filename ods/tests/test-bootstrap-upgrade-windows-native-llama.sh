#!/usr/bin/env bash
# Regression: Windows AMD hot-swaps the host-native llama-server.exe after the
# background full-model download completes. Round F: the swap goes through
# "ods.ps1 native-llm-restart <install dir>", which relaunches from the
# promoted .env with the shared launch contract (alias, one slot, Vulkan
# device, API key file) and exits 0 only after the model and context are
# proven. A failed proof restores the previous model config and restarts it.

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

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# One fresh fixture per scenario.
setup_fixture() {
    local case_dir="$1"
    fakebin="$case_dir/bin"
    install_dir="$case_dir/install"
    trace="$case_dir/powershell.trace"
    docker_trace="$case_dir/docker.trace"
    mkdir -p \
        "$fakebin" \
        "$install_dir/installers/windows" \
        "$install_dir/data/hermes" \
        "$install_dir/data/models" \
        "$install_dir/config/litellm" \
        "$install_dir/config/llama-server" \
        "$install_dir/extensions/services/hermes" \
        "$install_dir/llama-server"
    : > "$trace"
    : > "$docker_trace"
    # The dependent Compose refresh also uses the real installed policy gate.
    mkdir -p "$install_dir/scripts"
    cp "$ROOT_DIR/scripts/compose-cache-policy.py" "$install_dir/scripts/"

    cat > "$fakebin/uname" <<'EOF_UNAME'
#!/usr/bin/env bash
printf 'MINGW64_NT-10.0\n'
EOF_UNAME
    chmod +x "$fakebin/uname"

    cat > "$fakebin/curl" <<'EOF_CURL'
#!/usr/bin/env bash
case " $* " in
  *" -sI "*)
    printf 'HTTP/2 200\r\ncontent-length: 11\r\n\r\n'
    exit 0
    ;;
esac
exit 22
EOF_CURL
    chmod +x "$fakebin/curl"

    cat > "$fakebin/cygpath" <<'EOF_CYGPATH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "${!#}"
EOF_CYGPATH
    chmod +x "$fakebin/cygpath"

    # Fake PowerShell: records each native-llm-restart and the GGUF_FILE the
    # promoted .env holds at that moment. ODS_FAKE_NATIVE_FAILS=N fails the
    # first N restarts (a failed /v1/models or /props proof).
    cat > "$fakebin/powershell.exe" <<'EOF_PS'
#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" agent restart "* ]]; then
  printf 'agent-restart=%s\n' "$*" >> "${ODS_FAKE_PS_TRACE:?}"
  exit 0
fi
if [[ -n "${ODS_ENV_ACL_SOURCE:-}" && -n "${ODS_ENV_ACL_TARGET:-}" ]]; then
  exit 0
fi
if [[ -n "${ODS_ENV_REPLACE_SOURCE:-}" && -n "${ODS_ENV_REPLACE_TARGET:-}" && -n "${ODS_ENV_REPLACE_BACKUP:-}" ]]; then
  cp -p "$ODS_ENV_REPLACE_TARGET" "$ODS_ENV_REPLACE_BACKUP"
  mv -f "$ODS_ENV_REPLACE_SOURCE" "$ODS_ENV_REPLACE_TARGET"
  exit 0
fi
if [[ " $* " == *" native-llm-restart "* ]]; then
  install="${!#}"
  printf 'native-llm-restart=%s\n' "$*" >> "${ODS_FAKE_PS_TRACE:?}"
  printf 'env-gguf=%s\n' "$(sed -n 's/^GGUF_FILE=//p' "$install/.env")" >> "$ODS_FAKE_PS_TRACE"
  env | grep -q '^ODS_WIN_' && printf 'leaked-ods-win-env\n' >> "$ODS_FAKE_PS_TRACE"
  calls_file="${ODS_FAKE_PS_TRACE}.calls"
  calls=$(( $(cat "$calls_file" 2>/dev/null || echo 0) + 1 ))
  printf '%s\n' "$calls" > "$calls_file"
  if (( calls <= ${ODS_FAKE_NATIVE_FAILS:-0} )); then
    echo "Native llama-server did not start: llama-server served another model" >&2
    exit 1
  fi
  exit 0
fi
printf 'unexpected=%s\n' "$*" >> "${ODS_FAKE_PS_TRACE:?}"
exit 1
EOF_PS
    chmod +x "$fakebin/powershell.exe"

    printf '# Windows CLI fixture\n' > "$install_dir/installers/windows/ods.ps1"
    printf '# Windows CLI fixture (install root)\n' > "$install_dir/ods.ps1"

    cat > "$fakebin/docker" <<'EOF_DOCKER'
#!/usr/bin/env bash
set -euo pipefail
case " $* " in
  " info ")
    exit 0
    ;;
  " compose version ")
    exit 0
    ;;
esac
if [[ "${1:-}" == "ps" ]]; then
  case " $* " in
    *"name=ods-litellm"*)
      printf 'ods-litellm\n'
      ;;
  esac
  exit 0
fi
if [[ "${1:-}" == "restart" && "${2:-}" == "ods-litellm" ]]; then
  printf 'restart ods-litellm\n' >> "${ODS_FAKE_DOCKER_TRACE:?}"
  exit 0
fi
exit 0
EOF_DOCKER
    chmod +x "$fakebin/docker"

    cat > "$install_dir/.env" <<'EOF_ENV'
ODS_MODE=local
GPU_BACKEND=amd
LLM_BACKEND=llama-server
LLM_API_URL=http://host.docker.internal:8080
LLM_API_BASE_PATH=/v1
AMD_INFERENCE_RUNTIME=llama-server
AMD_INFERENCE_BACKEND=vulkan
AMD_INFERENCE_LOCATION=host
AMD_INFERENCE_PORT=8080
AMD_INFERENCE_RUNTIME_MODE=windows-native-llama-server
AMD_INFERENCE_MANAGED=true
BIND_ADDRESS=0.0.0.0
GGUF_FILE=Bootstrap.gguf
LLM_MODEL=bootstrap-model
MAX_CONTEXT=8192
CTX_SIZE=8192
HERMES_LLM_BASE_URL=http://host.docker.internal:8080/v1
LLAMA_SERVER_API_KEY=abababababababababababababababababababababababababababababababab
EOF_ENV

    cat > "$install_dir/extensions/services/hermes/cli-config.yaml.template" <<'EOF_HERMES'
model:
  default: "Bootstrap.gguf"
  provider: "custom"
  base_url: "http://host.docker.internal:8080/v1"
  context_length: 8192
providers:
  custom:
    request_timeout_seconds: 180
EOF_HERMES

    cat > "$install_dir/data/hermes/config.yaml" <<'EOF_HERMES_LIVE'
model:
  default: "Full.gguf"
  provider: "custom"
  base_url: "http://stale.invalid/v1"
  context_length: 8192
providers:
  custom:
    request_timeout_seconds: 180
auxiliary:
  compression:
    context_length: 8192
EOF_HERMES_LIVE

    printf 'bootstrap\n' > "$install_dir/data/models/Bootstrap.gguf"
    printf 'full-model\n' > "$install_dir/data/models/Full.gguf"
    printf 'fixture\n' > "$install_dir/llama-server/llama-server.exe"
    printf '1111\n' > "$install_dir/data/llama-server.pid"
}

run_upgrade() {
    PATH="$fakebin:$PATH" ODS_FAKE_PS_TRACE="$trace" ODS_FAKE_DOCKER_TRACE="$docker_trace" bash "$TARGET" \
        "$install_dir" \
        "Full.gguf" \
        "https://example.invalid/Full.gguf" \
        "" \
        "full-model" \
        "32768" \
        "Bootstrap.gguf"
}

# --- 1. A proven swap ---------------------------------------------------------
setup_fixture "$tmp/success"
run_upgrade > "$tmp/success.log" 2>&1 || { cat "$tmp/success.log" >&2; fail "the proven native swap should succeed"; }

grep -q 'Restarting native Windows llama-server with full model' "$tmp/success.log" \
    || fail "bootstrap-upgrade should restart the native Windows llama-server"
grep -q 'SUCCESS: native Windows llama-server running with Full.gguf' "$tmp/success.log" \
    || fail "bootstrap-upgrade should mark the native Windows llama-server swap verified"
grep -Eq "^native-llm-restart=-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ${install_dir}/ods\.ps1 native-llm-restart ${install_dir}$" "$trace" \
    || fail "the swap must go through ods.ps1 native-llm-restart for this installation"
[[ "$(grep -c '^native-llm-restart=' "$trace")" -eq 1 ]] \
    || fail "a proven swap restarts the native runtime exactly once"
grep -qx 'env-gguf=Full.gguf' "$trace" \
    || fail "ods.ps1 must relaunch from the .env already promoted to the full model"
! grep -q 'leaked-ods-win-env\|unexpected=' "$trace" \
    || fail "the swap must not pass launch settings through ODS_WIN_* variables or other PowerShell calls"
grep -q '^GGUF_FILE=Full.gguf$' "$install_dir/.env" \
    || fail "bootstrap-upgrade should promote GGUF_FILE after verified restart"
grep -q '^LLM_MODEL=full-model$' "$install_dir/.env" \
    || fail "bootstrap-upgrade should promote LLM_MODEL after verified restart"
grep -q 'default: "Full.gguf"' "$install_dir/extensions/services/hermes/cli-config.yaml.template" \
    || fail "Hermes should use the GGUF alias for the native Windows llama-server"
! grep -q 'extra.Full.gguf' "$install_dir/extensions/services/hermes/cli-config.yaml.template" \
    || fail "Hermes must not use Lemonade extra.* model ids"
grep -q 'default: "Full.gguf"' "$install_dir/data/hermes/config.yaml" \
    || fail "Hermes live config should keep the full-model alias"
grep -q 'base_url: "http://host.docker.internal:8080/v1"' "$install_dir/data/hermes/config.yaml" \
    || fail "Hermes live config should preserve the generated llama-server route"
grep -q '^  context_length: 32768$' "$install_dir/data/hermes/config.yaml" \
    || fail "Hermes live config should use the full-model context"
grep -q '^    context_length: 32768$' "$install_dir/data/hermes/config.yaml" \
    || fail "Hermes auxiliary compression context should use the full-model context"
grep -q '^    request_timeout_seconds: 900$' "$install_dir/data/hermes/config.yaml" \
    || fail "Hermes live config should use the Windows local provider timeout"
grep -q 'model: openai/Full.gguf' "$install_dir/config/litellm/local.yaml" \
    || fail "LiteLLM local config should route default requests to the full-model alias"
grep -q 'model: openai/\*' "$install_dir/config/litellm/local.yaml" \
    || fail "LiteLLM local config should preserve wildcard model routing"
grep -q 'api_base: http://host.docker.internal:8080/v1' "$install_dir/config/litellm/local.yaml" \
    || fail "LiteLLM local config should route to the native Windows llama-server host endpoint"
[[ "$(grep -c 'api_key: os.environ/LLAMA_SERVER_API_KEY' "$install_dir/config/litellm/local.yaml")" -eq 2 ]] \
    || fail "LiteLLM must send LLAMA_SERVER_API_KEY from its environment on both routes"
! grep -q 'not-needed\|abababab' "$install_dir/config/litellm/local.yaml" \
    || fail "LiteLLM local config must neither drop the key nor embed it"
grep -q 'enable_thinking: false' "$install_dir/config/litellm/local.yaml" \
    || fail "LiteLLM local config should disable Qwen thinking for native Windows llama-server"
grep -q '^  request_timeout: 900$' "$install_dir/config/litellm/local.yaml" \
    || fail "LiteLLM local config should keep long-model request timeout for native Windows llama-server"
grep -q '^  stream_timeout: 900$' "$install_dir/config/litellm/local.yaml" \
    || fail "LiteLLM local config should keep long-model stream timeout for native Windows llama-server"
! grep -q 'api_base: http://llama-server:8080/v1' "$install_dir/config/litellm/local.yaml" \
    || fail "LiteLLM local config must not point at the absent llama-server container"
grep -q 'restart ods-litellm' "$docker_trace" \
    || fail "bootstrap-upgrade should restart LiteLLM after refreshing the native Windows config"
grep -Eq '^agent-restart=-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .*/installers/windows/ods\.ps1 agent restart$' "$trace" \
    || fail "bootstrap-upgrade should refresh the native Windows host agent after .env changes"
[[ ! -f "$install_dir/data/models/Bootstrap.gguf" ]] \
    || fail "bootstrap model should be removed after verified native Windows swap"
grep -q '"status": "complete"' "$install_dir/data/bootstrap-status.json" \
    || fail "bootstrap status should finish complete"
pass "a proven native swap promotes the full model through ods.ps1"

# --- 2. A swap whose proof fails restores and restarts the previous model -----
setup_fixture "$tmp/failure"
if ODS_FAKE_NATIVE_FAILS=1 run_upgrade > "$tmp/failure.log" 2>&1; then
    fail "a failed native proof must fail the upgrade"
fi
[[ "$(grep -c '^native-llm-restart=' "$trace")" -eq 2 ]] \
    || fail "a failed proof restarts the previous model once"
[[ "$(sed -n 's/^env-gguf=//p' "$trace" | tr '\n' ' ')" == "Full.gguf Bootstrap.gguf " ]] \
    || fail "the second restart must run from the restored bootstrap .env (got: $(sed -n 's/^env-gguf=//p' "$trace" | tr '\n' ' '))"
grep -q '^GGUF_FILE=Bootstrap.gguf$' "$install_dir/.env" \
    || fail "a failed swap leaves the previous model configured"
[[ -f "$install_dir/data/models/Bootstrap.gguf" ]] \
    || fail "a failed swap keeps the bootstrap model"
grep -q 'Previous native Windows llama-server model restarted.' "$tmp/failure.log" \
    || fail "a failed swap reports that the previous model is running again"
grep -q '"status": "failed"' "$install_dir/data/bootstrap-status.json" \
    || fail "bootstrap status should report the failed swap"
pass "a failed native proof restores the previous model and restarts it"

! grep -q 'backend_for_swap.*llama-server' "$TARGET" \
    || fail "Windows native swap must not be selected by LLM_BACKEND=llama-server alone"
! grep -q 'llm_backend.*llama-server' "$TARGET" \
    || fail "Windows native restart helper must not be selected by LLM_BACKEND=llama-server alone"

pass "Windows native llama-server bootstrap swap is verified"
