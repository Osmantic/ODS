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
    local -a flags=()
    if [[ -s .compose-flags ]]; then
        read -r -a flags < .compose-flags
    else
        # Dashboard enable deliberately invalidates the installer cache. On
        # this isolated CPU gateway runner, resolve the newly enabled graph
        # with the same persisted route selectors the host agent now uses.
        local resolved
        resolved="$(ODS_EXTERNAL_LLM_SELECTED=true ODS_GATEWAY_ONLY=true \
            ENABLE_OPEN_WEBUI=false ODS_MODE=local \
            bash scripts/resolve-compose-stack.sh --script-dir "$INSTALL_DIR" \
                --tier 1 --gpu-backend cpu --ods-mode local)" || return 1
        read -r -a flags <<<"$resolved"
    fi
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

# Exercise the actual managed-local overlay with a tiny unhealthy stand-in.
# This checks the start guard without allocating a GGUF or GPU on CI.
cat >"$audit_root/unhealthy-llama.yml" <<'YAML'
services:
  llama-server:
    image: busybox:1.36
    command: ["sleep", "120"]
    healthcheck:
      test: ["CMD", "false"]
      interval: 2s
      timeout: 1s
      retries: 2
  perplexica:
    image: busybox:1.36
    entrypoint: ["sleep"]
    command: ["120"]
YAML
local_definition="$INSTALL_DIR/extensions/services/perplexica/compose.yaml.disabled"
local_overlay="$INSTALL_DIR/extensions/services/perplexica/compose.local.yaml"
[[ -f "$local_definition" && -f "$local_overlay" ]] \
    || fail 'installed managed-local Perplexica definitions are missing'
: >"$audit_root/local-empty.env"
local_stack=(docker compose -p ods-perplexica-unhealthy \
    --project-directory "$INSTALL_DIR" \
    --env-file "$audit_root/local-empty.env" \
    -f "$local_definition" \
    -f "$local_overlay" \
    -f "$audit_root/unhealthy-llama.yml")
"${local_stack[@]}" config --format json >"$audit_root/local-compose.json" \
    || fail 'managed-local Perplexica Compose did not render'
python3 - "$audit_root/local-compose.json" <<'PY' || fail 'managed-local health guard missing'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
assert config["services"]["perplexica"]["depends_on"]["llama-server"]["condition"] == "service_healthy"
assert config["services"]["perplexica"]["image"] == "busybox:1.36"
PY
if timeout 120s "${local_stack[@]}" up -d --wait perplexica \
    >"$audit_root/unhealthy-start.log" 2>&1; then
    fail 'managed-local Perplexica started with an unhealthy llama-server'
fi
if ! grep -Eiq 'unhealthy|dependency failed' "$audit_root/unhealthy-start.log"; then
    tail -n 12 "$audit_root/unhealthy-start.log" >&2
    fail 'managed-local start failed for a reason other than llama health'
fi
stub_id="$("${local_stack[@]}" ps -q llama-server)"
[[ -n "$stub_id" && "$(docker inspect --format '{{.State.Health.Status}}' "$stub_id")" == unhealthy ]] \
    || fail 'managed-local llama stand-in was not unhealthy'
if docker ps --format '{{.Names}}' | grep -qx ods-perplexica; then
    fail 'managed-local Perplexica started before llama-server was healthy'
fi
"${local_stack[@]}" down --volumes --remove-orphans >/dev/null \
    || fail 'could not clean disposable unhealthy-llama project'
printf 'PASS: managed-local Perplexica waits for healthy llama-server\n'

[[ -n "$product" ]] || fail 'product checkout is required'
[[ "$(git -C "$product" rev-parse HEAD)" == 623510b5bb5f4f05ee52c161c6230e12debca5d5 ]] || fail 'wrong product checkout'

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
for _ in {1..36}; do
    if curl -fsS --max-time 5 http://127.0.0.1:8888/healthz >/dev/null 2>&1; then
        break
    fi
    sleep 5
done
curl -fsS --max-time 20 http://127.0.0.1:8888/healthz >/dev/null \
    || fail 'SearXNG health endpoint unavailable after startup wait'
printf 'PASS: SearXNG enabled and healthy\n'

perplexica_code="$(curl -sS --max-time 900 -o "$audit_root/perplexica-enable.json" \
    -w '%{http_code}' -X POST http://127.0.0.1:3001/api/extensions/perplexica/enable || true)"
if [[ "$perplexica_code" != 200 ]]; then
    python3 - "$audit_root/perplexica-enable.json" "$key_file" <<'PY' >&2
import json
import re
import sys
from pathlib import Path

response = Path(sys.argv[1])
secret = Path(sys.argv[2]).read_text(encoding="ascii").strip()
try:
    detail = json.loads(response.read_text(encoding="utf-8")).get("detail", "")
except (OSError, ValueError):
    detail = "unreadable API response"
message = str(detail).replace(secret, "<redacted>")
message = re.sub(r"(?i)Bearer\s+\S+", "Bearer <redacted>", message)
print("Perplexica enable detail (sanitized):", message[:500])
PY
    fail "Perplexica enable returned HTTP $perplexica_code"
fi
python3 - "$audit_root/perplexica-enable.json" <<'PY' || fail 'Perplexica failed to start'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert "perplexica" in value.get("enabled_services", []), value
assert value.get("failed_services") == [], value
PY
for _ in {1..60}; do
    if curl -fLsS --max-time 5 http://127.0.0.1:3004/ >/dev/null 2>&1; then
        break
    fi
    sleep 5
done
curl -fLsS --max-time 20 http://127.0.0.1:3004/ >/dev/null \
    || fail 'Perplexica UI endpoint unavailable after startup wait'
printf 'PASS: Perplexica enabled and healthy on external-model gateway\n'

services="$(compose_services)" || fail 'enabled Compose selection cannot be resolved'
grep -qx perplexica <<<"$services" || fail 'Perplexica missing from selected stack'
grep -qx searxng <<<"$services" || fail 'SearXNG missing from selected stack'
if grep -qx llama-server <<<"$services"; then fail 'managed llama-server entered external stack'; fi
if docker ps --format '{{.Names}}' | grep -Eq 'ods-llama|ods-llama-server'; then
    fail 'managed llama-server container started'
fi
printf 'PASS: external Perplexica add-back kept managed llama-server absent\n'

mock_hits_before="$(grep -c 'accept=chat' "$mock_log" || true)"
if ! python3 - "$audit_root/perplexica-query.json" <<'PY'
import json
import sys
import urllib.error
import urllib.request

base = "http://127.0.0.1:3004"
with urllib.request.urlopen(base + "/api/config", timeout=10) as response:
    values = json.load(response)["values"]
prefs = values["preferences"]
providers = values["modelProviders"]
chat = {"providerId": prefs["defaultChatProvider"], "key": prefs["defaultChatModel"]}
embedding = {"providerId": prefs["defaultEmbeddingProvider"],
             "key": prefs["defaultEmbeddingModel"]}
assert any(p["id"] == chat["providerId"] and
           any(m["key"] == chat["key"] for m in p["chatModels"])
           for p in providers), "default chat model is not registered"
assert any(p["id"] == embedding["providerId"] and
           any(m["key"] == embedding["key"] for m in p["embeddingModels"])
           for p in providers), "default embedding model is not registered"
payload = {"sources": [], "query": "ODS acceptance model route check",
           "chatModel": chat, "embeddingModel": embedding,
           "stream": False, "optimizationMode": "speed"}
request = urllib.request.Request(
    base + "/api/search", data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.load(response)
except urllib.error.HTTPError as error:
    print(f"Perplexica search returned HTTP {error.code}", file=sys.stderr)
    raise SystemExit(1)
except (TimeoutError, urllib.error.URLError) as error:
    print(f"Perplexica search transport: {type(error).__name__}", file=sys.stderr)
    raise SystemExit(1)
assert isinstance(result.get("message"), str) and "OK" in result["message"], \
    "Perplexica returned no mock-backed answer"
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"message_present": True, "sources_count": len(result.get("sources") or [])}, stream)
print("PASS: Perplexica returned a mock-backed search answer")
PY
then
    python3 - "$mock_log" "$mock_hits_before" <<'PY' >&2
from pathlib import Path
import subprocess
import sys

mock = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
before = int(sys.argv[2])
after = mock.count("accept=chat")
print(f"Perplexica query diagnostic: accepted upstream chats before={before} after={after}")
for marker in ("reject=model-or-messages", "reject=request-json", "unauthorized"):
    print(f"Perplexica query diagnostic: mock {marker}={mock.count(marker)}")
result = subprocess.run(["docker", "logs", "--tail", "250", "ods-perplexica"],
                        capture_output=True, text=True, timeout=20)
logs = (result.stdout + result.stderr).lower()
for marker in ("embedding", "searxng", "timeout", "fetch failed", "download", "error"):
    print(f"Perplexica query diagnostic: container {marker}={logs.count(marker)}")
PY
    fail 'Perplexica did not complete a model-backed no-source request'
fi
mock_hits_after="$(grep -c 'accept=chat' "$mock_log" || true)"
(( mock_hits_after > mock_hits_before )) || fail 'Perplexica search did not reach the selected external model'
printf 'PASS: selected external model received Perplexica completion\n'

python3 - <<'PY' || fail 'SearXNG web source was unavailable on this runner'
import json
import urllib.parse
import urllib.request

query = urllib.parse.urlencode({"q": "Python programming language", "format": "json"})
with urllib.request.urlopen("http://127.0.0.1:8888/search?" + query, timeout=90) as response:
    result = json.load(response)
assert len(result.get("results") or []) > 0, "SearXNG returned no web results"
print("PASS: selected SearXNG returned web results")
PY

mock_web_calls_before="$(grep -c 'accept=tool-web' "$mock_log" || true)"
python3 - "$audit_root/perplexica-web-query.json" <<'PY' || fail 'Perplexica did not complete a SearXNG-backed web query'
import json
import sys
import urllib.request

base = "http://127.0.0.1:3004"
with urllib.request.urlopen(base + "/api/config", timeout=10) as response:
    values = json.load(response)["values"]
prefs = values["preferences"]
payload = {
    "sources": ["web"],
    "query": "ODS acceptance web query: what is Python programming language?",
    "chatModel": {"providerId": prefs["defaultChatProvider"],
                  "key": prefs["defaultChatModel"]},
    "embeddingModel": {"providerId": prefs["defaultEmbeddingProvider"],
                       "key": prefs["defaultEmbeddingModel"]},
    "stream": False,
    "optimizationMode": "speed",
}
request = urllib.request.Request(
    base + "/api/search", data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(request, timeout=240) as response:
    result = json.load(response)
assert isinstance(result.get("message"), str) and "OK" in result["message"]
sources = result.get("sources") or []
assert sources and all(source.get("metadata", {}).get("url", "").startswith("http")
                       for source in sources), "web answer has no cited sources"
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"message_present": True, "sources_count": len(sources)}, stream)
print("PASS: Perplexica returned a model-backed answer with web source URLs")
PY
mock_web_calls_after="$(grep -c 'accept=tool-web' "$mock_log" || true)"
(( mock_web_calls_after > mock_web_calls_before )) || fail 'model never requested Vane web_search tool'
printf 'PASS: selected external model invoked Vane web_search\n'

docker exec -u 0 ods-perplexica sh -c 'printf retained-perplexica-data >/home/vane/data/ods-acceptance-sentinel'     || fail 'could not write Perplexica data sentinel'
curl -fsS --max-time 180 -X POST http://127.0.0.1:3001/api/extensions/perplexica/disable     >"$audit_root/perplexica-disable.json" || fail 'Perplexica Disable failed'
[[ -f "$INSTALL_DIR/extensions/services/perplexica/compose.yaml.disabled" ]]     || fail 'Perplexica definition remained enabled after Disable'
curl -fsS --max-time 900 -X POST http://127.0.0.1:3001/api/extensions/perplexica/enable     >"$audit_root/perplexica-reenable.json" || fail 'Perplexica re-enable failed'
docker exec ods-perplexica cat /home/vane/data/ods-acceptance-sentinel     | grep -qx retained-perplexica-data || fail 'Perplexica data changed on Disable and re-enable'
printf 'PASS: Perplexica data retained across Disable and re-enable\n'
