#!/usr/bin/env bash
# End-to-end smoke for Dream Server wrapping an existing Lemonade SDK runtime.
#
# Default mode starts a tiny OpenAI-compatible mock Lemonade server, renders the
# external-Lemonade LiteLLM config, starts LiteLLM through the real Dream Server
# compose overlay, and verifies a chat completion traverses the route.
#
# Real mode points at an already-running Lemonade SDK service:
#   LEMONADE_E2E_URL=http://localhost:13305 \
#   LEMONADE_E2E_MODEL=<model-from-/api/v1/models> \
#   tests/fleet-external-lemonade-e2e.sh --real

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="mock"
KEEP_WORK=false

usage() {
    cat <<'EOF'
Usage: tests/fleet-external-lemonade-e2e.sh [--mock|--real] [--keep-work]

Options:
  --mock       Start a local mock Lemonade server container (default).
  --real       Use LEMONADE_E2E_URL as an already-running Lemonade SDK service.
  --keep-work  Keep the temporary copied Dream Server tree for debugging.
  -h, --help   Show this help.

Environment:
  LEMONADE_E2E_URL       Host-side Lemonade URL for --real. Default: http://localhost:13305
  LEMONADE_E2E_MODEL     Model id to request. Default: Qwen3-0.6B-GGUF
  LEMONADE_E2E_MOCK_IMAGE  Python image for --mock. Default: python:3.12-bookworm
  LEMONADE_E2E_PORT      Host port for --mock. Default: auto-selected free port
  LEMONADE_E2E_LITELLM_PORT  Host LiteLLM port. Default: auto-selected free port
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mock) MODE="mock"; shift ;;
        --real) MODE="real"; shift ;;
        --keep-work) KEEP_WORK=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "[FAIL] unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1" >&2; exit 1; }
note() { printf '[INFO] %s\n' "$1"; }

need() {
    command -v "$1" >/dev/null 2>&1 || fail "$1 is required"
}

need docker
need curl

PY="${DREAM_PYTHON_CMD:-}"
if [[ -z "$PY" ]]; then
    if command -v python3 >/dev/null 2>&1; then
        PY=python3
    elif command -v python >/dev/null 2>&1; then
        PY=python
    else
        fail "python is required"
    fi
fi

free_port() {
    "$PY" - <<'PY'
import socket
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1])
PY
}

wait_url() {
    local url="$1" label="$2" timeout="${3:-120}"
    local start now
    start="$(date +%s)"
    while true; do
        if curl -fsS "$url" >/dev/null 2>&1; then
            pass "$label"
            return 0
        fi
        now="$(date +%s)"
        if (( now - start >= timeout )); then
            echo "[DEBUG] timed out waiting for $url" >&2
            return 1
        fi
        sleep 2
    done
}

json_get_first_model() {
    "$PY" - "$1" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
data = payload.get("data") or []
print((data[0] or {}).get("id", "") if data else "")
PY
}

host_to_container_base() {
    local base="$1"
    case "$base" in
        http://localhost:*) printf '%s\n' "${base/http:\/\/localhost:/http:\/\/host.docker.internal:}" ;;
        http://127.0.0.1:*) printf '%s\n' "${base/http:\/\/127.0.0.1:/http:\/\/host.docker.internal:}" ;;
        *) printf '%s\n' "$base" ;;
    esac
}

strip_api_suffix() {
    local value="${1%/}"
    case "$value" in
        */api/v1) value="${value%/api/v1}" ;;
        */v1) value="${value%/v1}" ;;
        */api) value="${value%/api}" ;;
    esac
    printf '%s\n' "$value"
}

suffix="$(date +%s)-$$"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/dream-external-lemonade-e2e.XXXXXX")"
runtime_dir="$work_dir/dream-server"
mock_container="dream-lemonade-mock-$suffix"
litellm_container="dream-litellm-e2e-$suffix"
compose_project="dream-external-lemonade-e2e-$suffix"
litellm_override_file="e2e-litellm-container.override.yml"

cleanup() {
    set +e
    if [[ -d "$runtime_dir" ]]; then
        (cd "$runtime_dir" && docker compose \
            -p "$compose_project" \
            -f docker-compose.base.yml \
            -f docker-compose.cloud.yml \
            -f docker-compose.lemonade-external.yml \
            -f extensions/services/litellm/compose.yaml \
            -f "$litellm_override_file" \
            down --remove-orphans >/dev/null 2>&1)
    fi
    docker rm -f "$mock_container" >/dev/null 2>&1
    if [[ "$KEEP_WORK" != "true" ]]; then
        rm -rf "$work_dir"
    else
        note "Kept work dir: $work_dir"
    fi
}
trap cleanup EXIT

note "Preparing temporary Dream Server tree at $runtime_dir"
mkdir -p "$runtime_dir"
(cd "$ROOT_DIR" && tar --exclude='.git' --exclude='.pytest_cache' -cf - .) | (cd "$runtime_dir" && tar -xf -)

model="${LEMONADE_E2E_MODEL:-Qwen3-0.6B-GGUF}"
lemonade_key="${LEMONADE_E2E_API_KEY:-sk-e2e-lemonade}"
litellm_key="${LEMONADE_E2E_LITELLM_KEY:-sk-e2e-litellm}"
api_path="/api/v1"

if [[ "$MODE" == "mock" ]]; then
    lemonade_port="${LEMONADE_E2E_PORT:-$(free_port)}"
    host_base="http://127.0.0.1:${lemonade_port}"
    container_base="http://host.docker.internal:${lemonade_port}"
    note "Starting mock Lemonade server on $host_base"
    mock_publish="127.0.0.1:${lemonade_port}:13305"
    if [[ "$(uname -s)" == "Linux" ]]; then
        # On native Linux, containers reach host services through the bridge
        # gateway, so a host service bound only to loopback is not reachable.
        mock_publish="${lemonade_port}:13305"
    fi
    read -r -d '' mock_code <<'PY' || true
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

MODEL = "Qwen3-0.6B-GGUF"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in {"/api/v1/health", "/health"}:
            self._send(200, {"status": "ok"})
        elif self.path == "/api/v1/models":
            self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "dream-e2e"}]})
        else:
            self._send(404, {"error": self.path})

    def do_POST(self):
        length = int(self.headers.get("content-length", "0") or 0)
        if length:
            self.rfile.read(length)
        if self.path == "/api/v1/chat/completions":
            self._send(200, {
                "id": "chatcmpl-dream-e2e",
                "object": "chat.completion",
                "model": MODEL,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "external lemonade ok"},
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 3, "total_tokens": 4}
            })
        else:
            self._send(404, {"error": self.path})

ThreadingHTTPServer(("0.0.0.0", 13305), Handler).serve_forever()
PY
    docker run -d --name "$mock_container" \
        -p "$mock_publish" \
        "${LEMONADE_E2E_MOCK_IMAGE:-python:3.12-bookworm}" python -u -c "$mock_code" >/dev/null
    wait_url "${host_base}${api_path}/health" "mock Lemonade health is reachable" 120 \
        || fail "mock Lemonade did not become healthy"
else
    host_base="$(strip_api_suffix "${LEMONADE_E2E_URL:-http://localhost:13305}")"
    container_base="$(host_to_container_base "$host_base")"
    note "Using real Lemonade service at $host_base"
    wait_url "${host_base}${api_path}/health" "real Lemonade health is reachable" 30 \
        || fail "real Lemonade is not reachable at ${host_base}${api_path}/health"
    if [[ -z "${LEMONADE_E2E_MODEL:-}" ]]; then
        models_json="$(curl -fsS "${host_base}${api_path}/models")"
        detected_model="$(json_get_first_model "$models_json")"
        [[ -n "$detected_model" ]] || fail "could not detect model from ${host_base}${api_path}/models"
        model="$detected_model"
        pass "detected real Lemonade model: $model"
    fi
fi

container_api_base="${container_base}${api_path}"
litellm_port="${LEMONADE_E2E_LITELLM_PORT:-$(free_port)}"

note "Checking container-side Lemonade reachability at $container_api_base"
docker run --rm --add-host=host.docker.internal:host-gateway curlimages/curl:8.11.1 \
    -fsS "${container_api_base}/models" >/dev/null \
    || fail "Docker containers cannot reach Lemonade at ${container_api_base}/models"
pass "container-side Lemonade models endpoint is reachable"

cd "$runtime_dir"

note "Rendering external Lemonade LiteLLM config"
"$PY" scripts/render-runtime-configs.py \
    --surface litellm-lemonade \
    --dream-mode lemonade \
    --gpu-backend amd \
    --lemonade-model-id "$model" \
    --lemonade-api-base "$container_api_base" \
    --litellm-key "$lemonade_key" \
    --output-root "$runtime_dir" \
    --write >/dev/null

cat > .env <<ENV
BIND_ADDRESS=127.0.0.1
DREAM_MODE=lemonade
GPU_BACKEND=amd
LLM_API_URL=http://litellm:4000
LLM_MODEL=${model}
LLM_BACKEND=lemonade
LLM_API_BASE_PATH=/api/v1
LEMONADE_EXTERNAL=true
LEMONADE_BASE_URL=${host_base}
LEMONADE_CONTAINER_BASE_URL=${container_base}
LEMONADE_API_BASE_PATH=/api/v1
LEMONADE_MODEL=${model}
AMD_INFERENCE_RUNTIME=lemonade
AMD_INFERENCE_BACKEND=auto
AMD_INFERENCE_LOCATION=host
AMD_INFERENCE_PORT=${host_base##*:}
AMD_INFERENCE_SUPPORTED_BACKENDS=auto
AMD_INFERENCE_RUNTIME_MODE=external-lemonade
AMD_INFERENCE_MANAGED=false
LITELLM_PORT=${litellm_port}
LITELLM_KEY=${litellm_key}
LITELLM_LEMONADE_API_KEY=${lemonade_key}
WEBUI_SECRET=e2e-webui-secret
N8N_PASS=e2e-n8n-pass
DASHBOARD_API_KEY=e2e-dashboard-key
DREAM_AGENT_KEY=e2e-agent-key
DREAM_SESSION_SECRET=e2e-session-secret
ENV
chmod 600 .env

cat > "$litellm_override_file" <<YAML
services:
  litellm:
    container_name: ${litellm_container}
YAML

"$PY" -c 'import yaml' >/dev/null 2>&1 || fail "PyYAML is required for compose resolver"
flags="$(LEMONADE_EXTERNAL=true DREAM_MODE=lemonade AMD_INFERENCE_RUNTIME=lemonade AMD_INFERENCE_MANAGED=false \
    DREAM_PYTHON_CMD="$PY" ./scripts/resolve-compose-stack.sh \
    --script-dir "$runtime_dir" \
    --dream-mode lemonade \
    --gpu-backend amd \
    --tier SH_LARGE \
    --env)"
[[ "$flags" == *"docker-compose.cloud.yml"* ]] || fail "external Lemonade stack must include cloud overlay"
[[ "$flags" == *"docker-compose.lemonade-external.yml"* ]] || fail "external Lemonade stack must include external overlay"
[[ "$flags" != *"docker-compose.amd.yml"* ]] || fail "external Lemonade stack must not include managed AMD overlay"
pass "compose resolver selects external Lemonade stack"

note "Starting LiteLLM through Dream Server compose on http://127.0.0.1:${litellm_port}"
docker compose \
    -p "$compose_project" \
    -f docker-compose.base.yml \
    -f docker-compose.cloud.yml \
    -f docker-compose.lemonade-external.yml \
    -f extensions/services/litellm/compose.yaml \
    -f "$litellm_override_file" \
    up -d litellm >/dev/null

wait_url "http://127.0.0.1:${litellm_port}/health/readiness" "LiteLLM readiness is reachable" 180 \
    || {
        docker logs "$litellm_container" --tail 200 >&2 || true
        fail "LiteLLM did not become ready"
    }

models_response="$(curl -fsS \
    -H "Authorization: Bearer ${litellm_key}" \
    "http://127.0.0.1:${litellm_port}/v1/models")"
[[ "$models_response" == *"default"* ]] || fail "LiteLLM model list did not expose default route"
pass "LiteLLM exposes external Lemonade route"

chat_response="$(curl -fsS \
    -H "Authorization: Bearer ${litellm_key}" \
    -H "Content-Type: application/json" \
    -d '{"model":"default","messages":[{"role":"user","content":"ping"}],"max_tokens":16}' \
    "http://127.0.0.1:${litellm_port}/v1/chat/completions")"
[[ "$chat_response" == *"external lemonade ok"* ]] || {
    echo "$chat_response" >&2
    fail "LiteLLM chat completion did not traverse external Lemonade"
}
pass "LiteLLM chat completion traverses external Lemonade"

echo "[PASS] external Lemonade E2E (${MODE})"
