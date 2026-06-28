#!/usr/bin/env bash
# Regression: Windows AMD llama-server fallback must hot-swap the host-native
# llama-server.exe after the background full-model download completes.

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

fakebin="$tmp/bin"
install_dir="$tmp/install"
trace="$tmp/powershell.trace"
docker_trace="$tmp/docker.trace"
mkdir -p \
    "$fakebin" \
    "$install_dir/data/hermes" \
    "$install_dir/data/models" \
    "$install_dir/config/litellm" \
    "$install_dir/config/llama-server" \
    "$install_dir/extensions/services/hermes" \
    "$install_dir/llama-server"

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

cat > "$fakebin/powershell.exe" <<'EOF_PS'
#!/usr/bin/env bash
set -euo pipefail
: "${ODS_WIN_PID_FILE:?}"
: "${ODS_WIN_LLAMA_EXE:?}"
: "${ODS_WIN_MODEL_PATH:?}"
: "${ODS_WIN_ROLLBACK_MODEL_PATH:?}"
: "${ODS_WIN_LLAMA_PORT:?}"
: "${ODS_WIN_CTX_SIZE:?}"
: "${ODS_WIN_REASONING_FORMAT:?}"
{
  printf 'exe=%s\n' "$ODS_WIN_LLAMA_EXE"
  printf 'model=%s\n' "$ODS_WIN_MODEL_PATH"
  printf 'rollback=%s\n' "$ODS_WIN_ROLLBACK_MODEL_PATH"
  printf 'port=%s\n' "$ODS_WIN_LLAMA_PORT"
  printf 'ctx=%s\n' "$ODS_WIN_CTX_SIZE"
  printf 'reasoning=%s\n' "$ODS_WIN_REASONING_FORMAT"
} >> "${ODS_FAKE_PS_TRACE:?}"
mkdir -p "$(dirname "$ODS_WIN_PID_FILE")"
printf '4242\n' > "$ODS_WIN_PID_FILE"
exit 0
EOF_PS
chmod +x "$fakebin/powershell.exe"

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
AMD_INFERENCE_RUNTIME_MODE=windows-llama-server-fallback
AMD_INFERENCE_MANAGED=true
BIND_ADDRESS=127.0.0.1
GGUF_FILE=Bootstrap.gguf
LLM_MODEL=bootstrap-model
MAX_CONTEXT=8192
CTX_SIZE=8192
HERMES_LLM_BASE_URL=http://host.docker.internal:8080/v1
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
printf '#!/usr/bin/env bash\nexit 0\n' > "$install_dir/llama-server/llama-server.exe"
chmod +x "$install_dir/llama-server/llama-server.exe"
printf '1111\n' > "$install_dir/data/llama-server.pid"

PATH="$fakebin:$PATH" ODS_FAKE_PS_TRACE="$trace" ODS_FAKE_DOCKER_TRACE="$docker_trace" bash "$TARGET" \
    "$install_dir" \
    "Full.gguf" \
    "https://example.invalid/Full.gguf" \
    "" \
    "full-model" \
    "32768" \
    "Bootstrap.gguf" \
    > "$tmp/bootstrap.log" 2>&1

grep -q 'Restarting native Windows llama-server with full model' "$tmp/bootstrap.log" \
    || fail "bootstrap-upgrade should restart the native Windows llama-server fallback"
grep -q 'SUCCESS: native Windows llama-server running with Full.gguf' "$tmp/bootstrap.log" \
    || fail "bootstrap-upgrade should mark the native Windows llama-server swap verified"
grep -q 'model=.*Full.gguf$' "$trace" \
    || fail "PowerShell restart should receive the full GGUF path"
grep -q 'rollback=.*Bootstrap.gguf$' "$trace" \
    || fail "PowerShell restart should receive the bootstrap rollback path"
grep -q 'port=8080' "$trace" \
    || fail "PowerShell restart should target the AMD inference port"
grep -q 'ctx=32768' "$trace" \
    || fail "PowerShell restart should target the full-model context"
grep -q 'reasoning=none' "$trace" \
    || fail "PowerShell restart should disable reasoning by default"
grep -q '^GGUF_FILE=Full.gguf$' "$install_dir/.env" \
    || fail "bootstrap-upgrade should promote GGUF_FILE after verified restart"
grep -q '^LLM_MODEL=full-model$' "$install_dir/.env" \
    || fail "bootstrap-upgrade should promote LLM_MODEL after verified restart"
grep -q 'default: "Full.gguf"' "$install_dir/extensions/services/hermes/cli-config.yaml.template" \
    || fail "Hermes should use the bare GGUF id for Windows llama-server fallback"
! grep -q 'extra.Full.gguf' "$install_dir/extensions/services/hermes/cli-config.yaml.template" \
    || fail "Hermes should not use Lemonade extra.* model ids for Windows llama-server fallback"
grep -q 'default: "Full.gguf"' "$install_dir/data/hermes/config.yaml" \
    || fail "Hermes live config should keep the bare full-model id"
grep -q 'base_url: "http://host.docker.internal:8080/v1"' "$install_dir/data/hermes/config.yaml" \
    || fail "Hermes live config should preserve the generated llama-server route"
grep -q '^  context_length: 32768$' "$install_dir/data/hermes/config.yaml" \
    || fail "Hermes live config should use the full-model context"
grep -q '^    context_length: 32768$' "$install_dir/data/hermes/config.yaml" \
    || fail "Hermes auxiliary compression context should use the full-model context"
grep -q '^    request_timeout_seconds: 900$' "$install_dir/data/hermes/config.yaml" \
    || fail "Hermes live config should use the Windows local provider timeout"
grep -q 'model: openai/Full.gguf' "$install_dir/config/litellm/local.yaml" \
    || fail "LiteLLM local config should route default requests to the bare full-model id"
grep -q 'model: openai/\*' "$install_dir/config/litellm/local.yaml" \
    || fail "LiteLLM local config should preserve wildcard model routing"
grep -q 'api_base: http://host.docker.internal:8080/v1' "$install_dir/config/litellm/local.yaml" \
    || fail "LiteLLM local config should route to the native Windows llama-server host endpoint"
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
[[ ! -f "$install_dir/data/models/Bootstrap.gguf" ]] \
    || fail "bootstrap model should be removed after verified native Windows swap"
grep -q '"status": "complete"' "$install_dir/data/bootstrap-status.json" \
    || fail "bootstrap status should finish complete"
! grep -q 'backend_for_swap.*llama-server' "$TARGET" \
    || fail "Windows native swap must not be selected by LLM_BACKEND=llama-server alone"
! grep -q 'llm_backend.*llama-server' "$TARGET" \
    || fail "Windows native restart helper must not be selected by LLM_BACKEND=llama-server alone"

pass "Windows native llama-server bootstrap swap is verified"
