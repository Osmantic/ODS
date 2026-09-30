#!/usr/bin/env bash
# Disposable Ubuntu Docker Engine journey for the final Linux gateway stack.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
audit_root="$RUNNER_TEMP/ods-perplexica-acceptance"
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

run_installer() {
    local stage="$1"
    shift
    printf 'Running %s at %s\n' "$stage" "$(git -C "$product" rev-parse HEAD)"
    if ! (cd "$product" && timeout 1500s bash install-core.sh \
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
umask 022
mkdir -p "$audit_root"
python3 - "$key_file" <<'PY'
import secrets
import sys
with open(sys.argv[1], "w", encoding="ascii") as stream:
    stream.write("mock-" + secrets.token_hex(24))
PY
chmod 600 "$key_file"

python3 "$harness/mock-openai-upstream.py" \
    --key-file "$key_file" --port "$mock_port" >"$mock_log" 2>&1 &
mock_pid=$!
for _ in {1..30}; do
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

[[ -n "$product" ]] || fail 'product checkout is required'
[[ "$(git -C "$product" rev-parse HEAD)" == dae6dc25cc8dd13f9829e19667345475761467f6 ]] || fail 'wrong product checkout'

curl -fsS --max-time 30 http://127.0.0.1:3001/api/extensions/perplexica/install-plan     >"$audit_root/perplexica-plan.json" || fail 'Perplexica install plan unavailable'
python3 - "$audit_root/perplexica-plan.json" <<'PY' || fail 'external-model plan is blocked'
import json, sys
plan = json.load(open(sys.argv[1], encoding="utf-8"))
assert plan["blocked"] is False, plan
steps = [(step["extensionId"], step["action"]) for step in plan["steps"]]
assert steps == [("searxng", "enable"), ("perplexica", "enable")], steps
print("PASS: external-model plan enables search and Perplexica without llama-server")
PY

curl -fsS --max-time 900 -X POST http://127.0.0.1:3001/api/extensions/searxng/enable     >"$audit_root/search-enable.json" || fail 'SearXNG enable request failed'
python3 - "$audit_root/search-enable.json" <<'PY' || fail 'SearXNG failed to start'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert "searxng" in value.get("enabled_services", []), value
assert value.get("failed_services") == [], value
PY
curl -fsS --max-time 20 http://127.0.0.1:8888/healthz >/dev/null     || fail 'SearXNG health endpoint unavailable'
printf 'PASS: SearXNG enabled and healthy\n'

curl -fsS --max-time 900 -X POST http://127.0.0.1:3001/api/extensions/perplexica/enable     >"$audit_root/perplexica-enable.json" || fail 'Perplexica enable request failed'
python3 - "$audit_root/perplexica-enable.json" <<'PY' || fail 'Perplexica failed to start'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert "perplexica" in value.get("enabled_services", []), value
assert value.get("failed_services") == [], value
PY
curl -fLsS --max-time 20 http://127.0.0.1:3004/ >/dev/null     || fail 'Perplexica UI endpoint unavailable'
printf 'PASS: Perplexica enabled and healthy on external-model gateway\n'

services="$(compose_services)" || fail 'enabled Compose selection cannot be resolved'
grep -qx perplexica <<<"$services" || fail 'Perplexica missing from selected stack'
grep -qx searxng <<<"$services" || fail 'SearXNG missing from selected stack'
if grep -qx llama-server <<<"$services"; then fail 'managed llama-server entered external stack'; fi
if docker ps --format '{{.Names}}' | grep -Eq 'ods-llama|ods-llama-server'; then
    fail 'managed llama-server container started'
fi
printf 'PASS: external Perplexica add-back kept managed llama-server absent\n'

docker exec -u 0 ods-perplexica sh -c 'printf retained-perplexica-data >/home/vane/data/ods-acceptance-sentinel'     || fail 'could not write Perplexica data sentinel'
curl -fsS --max-time 180 -X POST http://127.0.0.1:3001/api/extensions/perplexica/disable     >"$audit_root/perplexica-disable.json" || fail 'Perplexica Disable failed'
[[ -f "$INSTALL_DIR/extensions/services/perplexica/compose.yaml.disabled" ]]     || fail 'Perplexica definition remained enabled after Disable'
curl -fsS --max-time 900 -X POST http://127.0.0.1:3001/api/extensions/perplexica/enable     >"$audit_root/perplexica-reenable.json" || fail 'Perplexica re-enable failed'
docker exec ods-perplexica cat /home/vane/data/ods-acceptance-sentinel     | grep -qx retained-perplexica-data || fail 'Perplexica data changed on Disable and re-enable'
printf 'PASS: Perplexica data retained across Disable and re-enable\n'
