#!/usr/bin/env bash
# Disposable gateway install and Dashboard Library WebUI add-back journey.
set -euo pipefail

root="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
expected=54186765bfb2ab562003e6e135ea11b92aa9fd9c
audit_root="${RUNNER_TEMP:?}/ods-webui-library-acceptance"
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

check_api() {
    python3 "$harness/portal-requests.py" "$1" "$INSTALL_DIR/.env"
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
    printf 'Running %s at exact product %s\n' "$stage" "$expected"
    if ! (cd "$root" && timeout 1500s bash install-core.sh \
        --non-interactive --skip-docker --no-pixel \
        --external-llm-url "http://127.0.0.1:$mock_port" \
        --external-llm-provider openai-compatible \
        --external-llm-model "$model" "$@") >>"$LOG_FILE" 2>&1; then
        python3 - "$LOG_FILE" "$key_file" "$INSTALL_DIR/.env" <<'PY' >&2
from pathlib import Path
import re
import sys
log_path, key_path, env_path = map(Path, sys.argv[1:])
secrets = [key_path.read_text(encoding="utf-8").strip()]
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^(?:export )?([A-Z0-9_]+)=(.*)$", line)
        if match and any(word in match[1] for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            secrets.append(match[2].strip("\"'"))
print("Sanitized gateway installer log tail:")
for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]:
    for secret in secrets:
        if secret:
            line = line.replace(secret, "<redacted>")
    line = re.sub(r"(?i)Bearer\s+\S+", "Bearer <redacted>", line)
    line = re.sub(r"\b(?:sk-|mock-)[A-Za-z0-9_-]{12,}\b", "<redacted>", line)
    print(line[:500])
PY
        fail "$stage installer did not complete"
    fi
}

[[ "${GITHUB_ACTIONS:-}" == true ]] || fail 'refusing non-disposable host'
[[ "$(git -C "$root" rev-parse HEAD)" == "$expected" ]] || fail 'product checkout is not pinned'
[[ "$INSTALL_DIR" == "$RUNNER_TEMP"/* ]] || fail 'install path is outside runner temp'
command -v docker >/dev/null || fail 'Docker CLI missing'
docker info >/dev/null || fail 'isolated Docker Engine unavailable'
[[ ! -e "$INSTALL_DIR" ]] || fail 'fresh install directory already exists'
umask 077
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

check_api selection-off || fail 'Library did not report WebUI as addable'
mkdir -p "$INSTALL_DIR/data/open-webui"
printf 'retained-webui-data\n' >"$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt"
sentinel_hash="$(sha256sum "$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt" | cut -d' ' -f1)"
cp "$INSTALL_DIR/.env" "$audit_root/env-before"
cp "$INSTALL_DIR/docker-compose.base.yml" "$audit_root/base-before.yml"

# Force a WebUI-only startup failure in this disposable installed tree.
python3 - "$INSTALL_DIR/docker-compose.base.yml" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
original = next(line for line in text.splitlines() if "ghcr.io/open-webui/open-webui:" in line)
modified = text.replace(original, "    image: ghcr.io/open-webui/ods-acceptance-missing-image:never", 1)
assert modified != text
path.write_text(modified, encoding="utf-8")
PY
check_api expect-add-failure || fail 'Library did not report failed WebUI startup'
cmp -s "$audit_root/env-before" "$INSTALL_DIR/.env" || fail 'failed add-back changed selection'
check_api selection-off || fail 'failed add-back left WebUI selected'
cp "$audit_root/base-before.yml" "$INSTALL_DIR/docker-compose.base.yml"
cmp -s "$audit_root/base-before.yml" "$INSTALL_DIR/docker-compose.base.yml" \
    || fail 'WebUI failure injection was not restored'
[[ "$(sha256sum "$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt" | cut -d' ' -f1)" == "$sentinel_hash" ]] \
    || fail 'failed add-back changed retained WebUI data'
printf 'PASS: failed Library add-back restored selection and data\n'

check_api add-webui || fail 'Library did not add WebUI'
check_api selection-on || fail 'Library did not retain WebUI choice'
[[ "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' ods-webui)" == unless-stopped ]] \
    || fail 'Library-added WebUI lacks restart persistence'
curl -fLsS --max-time 30 http://127.0.0.1:3000/ >/dev/null \
    || fail 'Library-added WebUI is unreachable'
docker restart ods-webui >/dev/null || fail 'Library-added WebUI could not restart'
webui_ready=false
for attempt in {1..40}; do
    if curl -fLsS --max-time 8 http://127.0.0.1:3000/ >/dev/null 2>&1; then
        webui_ready=true
        break
    fi
    sleep 3
done
[[ "$webui_ready" == true ]] || fail 'Library-added WebUI did not recover after restart'
check_api selection-on || fail 'WebUI choice changed after container restart'
assert_litellm_completion
[[ "$(sha256sum "$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt" | cut -d' ' -f1)" == "$sentinel_hash" ]] \
    || fail 'Library add-back changed retained WebUI data'
docker ps -a --format '{{.Names}}' | grep -qx ods-llama-server \
    && fail 'Library add-back started a managed model on the external route'
printf 'PASS: Library added WebUI, retained data, and kept managed inference absent\n'
