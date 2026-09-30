#!/usr/bin/env bash
# Disposable Ubuntu Docker Engine journey for the final Linux gateway stack.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
audit_root="${RUNNER_TEMP:?}/ods-gateway-acceptance"
export INSTALL_DIR="$audit_root/install"
export LOG_FILE="$audit_root/install.log"
key_file="$audit_root/mock.key"
mock_log="$audit_root/mock.log"
model=ods-acceptance-mock
mock_port=18080
mock_pid=""

cleanup() {
    if [[ -n "$mock_pid" ]]; then
        kill "$mock_pid" 2>/dev/null || true
        wait "$mock_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    docker ps --format '{{.Names}} {{.Status}}' >&2 || true
    exit 1
}

compose_services() (
    cd "$INSTALL_DIR"
    [[ -s .compose-flags ]] || return 1
    local -a flags=()
    read -r -a flags < .compose-flags
    docker compose "${flags[@]}" config --services
)

assert_gateway() {
    local services
    services="$(compose_services)" || fail 'installed Compose selection cannot be resolved'
    for required in dashboard dashboard-api litellm; do
        grep -qx "$required" <<<"$services" || fail "gateway omitted $required"
    done
    for forbidden in open-webui llama-server model-router perplexica whisper tts n8n qdrant searxng comfyui hermes token-spy; do
        if grep -qx "$forbidden" <<<"$services"; then
            fail "gateway unexpectedly selected $forbidden"
        fi
    done
    if docker ps --format '{{.Names}}' | grep -qx ods-webui; then
        fail 'WebUI container remains running in API-only selection'
    fi
    python3 - <<'PY' || fail 'WebUI host port remains open'
import socket
try:
    socket.create_connection(("127.0.0.1", 3000), timeout=2).close()
except OSError:
    pass
else:
    raise SystemExit(1)
PY
}

assert_litellm_completion() {
    python3 - "$INSTALL_DIR/.env" <<'PY' || fail 'LiteLLM ods/current completion failed'
import json
import sys
import urllib.request

env_path = sys.argv[1]
with open(env_path, encoding="utf-8") as stream:
    values = dict(line.rstrip("\n").split("=", 1) for line in stream if "=" in line and not line.startswith("#"))
key = values.get("LITELLM_KEY", "").strip()
if len(key) >= 2 and key[0] in "\"'" and key[-1] == key[0]:
    key = key[1:-1]
if not key:
    raise SystemExit("installed LiteLLM key is missing")
body = json.dumps({
    "model": "ods/current",
    "messages": [{"role": "user", "content": "Reply OK."}],
    "max_tokens": 8,
    "temperature": 0,
    "stream": False,
}).encode()
request = urllib.request.Request(
    "http://127.0.0.1:4000/v1/chat/completions",
    data=body,
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=90) as response:
    result = json.load(response)
message = result["choices"][0]["message"]
assert message["role"] == "assistant" and message["content"].strip() == "OK"
PY
}

probe_extension_reads() {
    python3 - "$INSTALL_DIR/.env" <<'PY'
import json
import urllib.error
import urllib.request
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    values = dict(line.rstrip("\n").split("=", 1) for line in stream
                  if "=" in line and not line.startswith("#"))
key = values.get("DASHBOARD_API_KEY", "").strip()
if len(key) >= 2 and key[0] in "\"'" and key[-1] == key[0]:
    key = key[1:-1]
if not key:
    raise SystemExit("installed Dashboard API credential is missing")
port = values.get("DASHBOARD_API_PORT", "3002").strip().strip("\"'")
for path in ("/api/extensions/actual-budget", "/api/extensions/actual-budget/install-plan"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=45)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        status = response.status
        content_type = response.headers.get_content_type()
        keys = []
        if 200 <= status < 300 and content_type == "application/json":
            value = json.load(response)
            if isinstance(value, dict):
                keys = sorted(value)
        print(f"MAIN_EXTENSION_READ path={path} status={status} content_type={content_type} keys={keys}")
PY
}

run_installer() {
    local stage="$1"
    shift
    printf 'Running %s at %s\n' "$stage" "$(git -C "$root" rev-parse HEAD)"
    if ! (cd "$root" && timeout 1500s bash install-core.sh \
        --non-interactive --skip-docker --no-pixel \
        --external-llm-url "http://127.0.0.1:$mock_port" \
        --external-llm-provider openai-compatible \
        --external-llm-model "$model" "$@") >>"$LOG_FILE" 2>&1; then
        fail "$stage installer did not complete; inspect private runner log"
    fi
}

command -v docker >/dev/null || fail 'Docker CLI missing'
docker info >/dev/null || fail 'isolated Docker Engine unavailable'
[[ ! -e "$INSTALL_DIR" ]] || fail 'fresh install directory already exists'
previous_umask="$(umask)"
umask 077
mkdir -p "$audit_root"
python3 - "$key_file" <<'PY'
import secrets
import sys
with open(sys.argv[1], "w", encoding="ascii") as stream:
    stream.write("mock-" + secrets.token_hex(24))
PY
chmod 600 "$key_file"
umask "$previous_umask"

python3 "$root/tests/acceptance/mock-openai-upstream.py" \
    --key-file "$key_file" --port "$mock_port" >"$mock_log" 2>&1 &
mock_pid=$!
for attempt in {1..30}; do
    if curl -fsS --max-time 2 "http://127.0.0.1:$mock_port/healthz" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl -fsS --max-time 2 "http://127.0.0.1:$mock_port/healthz" >/dev/null \
    || fail 'mock upstream did not start'

docker image ls --format '{{.Repository}}' | sort -u >"$audit_root/images-before.txt"
run_installer fresh --gateway-only --external-llm-key-file "$key_file"
assert_gateway
assert_litellm_completion
probe_extension_reads

installed_key="$INSTALL_DIR/config/litellm/external-upstream.key"
[[ "$(stat -c %a "$installed_key")" == 600 ]] || fail 'installed upstream key mode is not 600'
cmp -s "$key_file" "$installed_key" || fail 'installed upstream key changed'
if grep -Fq -f "$key_file" "$INSTALL_DIR/.env" "$INSTALL_DIR/config/litellm/"*.yaml "$LOG_FILE"; then
    fail 'upstream key leaked into generated config or installer log'
fi

docker image ls --format '{{.Repository}}' | sort -u >"$audit_root/images-after.txt"
if comm -13 "$audit_root/images-before.txt" "$audit_root/images-after.txt" \
    | grep -Ei 'open-webui|llama.cpp|perplexica|whisper|kokoro|searxng|comfyui'; then
    fail 'fresh gateway pulled an unselected optional image'
fi

printf 'retained-data-sentinel\n' >"$INSTALL_DIR/data/acceptance-sentinel.txt"
sentinel_hash="$(sha256sum "$INSTALL_DIR/data/acceptance-sentinel.txt" | cut -d' ' -f1)"
run_installer retained --gateway-only
assert_gateway
assert_litellm_completion
[[ "$(sha256sum "$INSTALL_DIR/data/acceptance-sentinel.txt" | cut -d' ' -f1)" == "$sentinel_hash" ]] \
    || fail 'retained rerun changed data sentinel'

run_installer webui --gateway-only --with-webui
compose_services | grep -qx open-webui || fail 'WebUI opt-in did not select Open WebUI'
curl -fLsS --max-time 20 http://127.0.0.1:3000/ >/dev/null \
    || fail 'opted-in WebUI is not reachable'
assert_litellm_completion

run_installer rollback --gateway-only
assert_gateway
assert_litellm_completion
[[ "$(sha256sum "$INSTALL_DIR/data/acceptance-sentinel.txt" | cut -d' ' -f1)" == "$sentinel_hash" ]] \
    || fail 'WebUI rollback changed data sentinel'

printf 'PASS: exact-source gateway install, retained rerun, WebUI opt-in and rollback\n'
