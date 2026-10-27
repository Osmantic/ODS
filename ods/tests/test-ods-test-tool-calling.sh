#!/usr/bin/env bash
# scripts/ods-test.sh (named in QUICKSTART and the dashboard's Troubleshooting
# Assistant) must pass its Tool Calling check when the model makes the call,
# and fail when it does not.
#
# It reported Tool Calling as failed on every install: the request carried an
# extra "}" (invalid JSON, which llama-server refuses), named a retired model,
# and the script read ~/.ods/.env instead of the install's .env. Success was a
# grep for "tool_calls", which a reply with "tool_calls": null and no call also
# contains. LLM Inference used the same kind of grep.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[FAIL] Bash 4+ is required (service registry)" >&2
    exit 1
fi
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_TEST_UNDER_TEST:-$ROOT_DIR/scripts/ods-test.sh}"
TMP_DIR="$(mktemp -d)"
SERVER_PID=""
cleanup() {
    # The server may already have exited; nothing else is left to stop.
    [[ -z "$SERVER_PID" ]] || kill "$SERVER_PID" 2>/dev/null || true
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$TARGET" ]] || fail "missing $TARGET"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"

# Run from a copy laid out like an install: the script finds lib/, the
# service manifests and the install's .env from its own parent directory.
INSTALL="$TMP_DIR/install"
mkdir -p "$INSTALL/scripts" "$INSTALL/bin" "$INSTALL/extensions/services" "$TMP_DIR/home"
cp -R "$ROOT_DIR/lib" "$INSTALL/"
cp "$TARGET" "$INSTALL/scripts/ods-test.sh"
for manifest in "$ROOT_DIR"/extensions/services/*/manifest.yaml; do
    svc="$(basename "$(dirname "$manifest")")"
    mkdir -p "$INSTALL/extensions/services/$svc"
    cp "$manifest" "$INSTALL/extensions/services/$svc/"
done
printf '#!/bin/sh\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n' > "$INSTALL/bin/docker"
chmod +x "$INSTALL/bin/docker"

# A stand-in llama-server on a free loopback port. It serves one model id,
# records each chat request it can parse, and answers as $TMP_DIR/mode says.
cat > "$TMP_DIR/server.py" <<'PY'
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

WORK = sys.argv[1]
MODEL = "fixture-model.gguf"


def completion(message):
    return {"object": "chat.completion", "model": MODEL, "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}]}


REPLIES = {
    "call": (200, completion({"content": None, "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "get_weather", "arguments": json.dumps({"city": "Tokyo"})}}]})),
    "text": (200, completion({"content": "Hello there!"})),
    # A thinking model can spend a short max_tokens budget on reasoning alone.
    "reasoning": (200, completion({"content": "", "reasoning_content": "The user wants a greeting."})),
    "error": (500, {"error": {"code": 500, "type": "server_error",
                              "message": "tool_calls are not supported by this chat template"}}),
    # No call, but the key is there (some OpenAI-compatible servers send
    # "tool_calls": null): the old grep took it as a call.
    "no-call": (200, completion({"content": "I cannot look up the weather.", "tool_calls": None})),
}
# Like llama-server, a working server refuses a body it cannot parse. The
# other modes answer whatever arrives: a server or gateway that fails or
# declines the call.
WORKING = {"call", "text", "reasoning"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        # Compact like llama-server's own JSON.
        data = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self.reply(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        else:
            self.reply(404, {"error": {"code": 404, "message": "File Not Found"}})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with open(os.path.join(WORK, "mode")) as handle:
            mode = handle.read().strip()
        try:
            request = json.loads(raw)
        except ValueError:
            request = None
        if request is not None:
            with open(os.path.join(WORK, "requests.jsonl"), "a") as log:
                log.write(json.dumps(request) + "\n")
        elif mode in WORKING:
            self.reply(400, {"error": {"code": 400, "type": "invalid_request_error",
                                       "message": "Failed to parse the request body as JSON"}})
            return
        self.reply(*REPLIES[mode])


server = HTTPServer(("127.0.0.1", 0), Handler)
with open(os.path.join(WORK, "port.tmp"), "w") as handle:
    handle.write(str(server.server_address[1]))
os.replace(os.path.join(WORK, "port.tmp"), os.path.join(WORK, "port"))
server.serve_forever()
PY
python3 "$TMP_DIR/server.py" "$TMP_DIR" 2>"$TMP_DIR/server.err" &
SERVER_PID=$!
for _ in $(seq 1 100); do
    [[ -s "$TMP_DIR/port" ]] && break
    sleep 0.1
done
[[ -s "$TMP_DIR/port" ]] || fail "the stand-in llama-server did not start: $(cat "$TMP_DIR/server.err")"
# Only the install's .env knows the port, as on a real install.
# This fixture is the direct llama-server lane, not a configured gateway.
printf 'ODS_MODEL_SWITCHBOARD=disabled\nOLLAMA_PORT=%s\n' "$(cat "$TMP_DIR/port")" > "$INSTALL/.env"

# run_check <mode> <service> [explicit|default]: run one section with --json.
# "explicit" names the install and its .env the way the other ods-test tests
# do; "default" relies on the script's own default with an empty HOME.
run_check() {
    local mode="$1" service="$2" config="${3:-explicit}"
    printf '%s\n' "$mode" > "$TMP_DIR/mode"
    : > "$TMP_DIR/requests.jsonl"
    # ods-test.sh exits 1 when a check fails; the verdict is read from its JSON.
    (
        cd "$INSTALL" || exit 1
        unset ODS_DIR ENV_FILE LLM_HOST LLM_PORT LLM_URL
        if [[ "$config" == explicit ]]; then
            export ODS_DIR="$INSTALL" ENV_FILE="$INSTALL/.env"
        fi
        HOME="$TMP_DIR/home" PATH="$INSTALL/bin:$PATH" \
            "$BASH" scripts/ods-test.sh --service "$service" --json
    ) >"$TMP_DIR/out.json" 2>"$TMP_DIR/err.txt" || true
}

result_status() {
    python3 - "$TMP_DIR/out.json" "$1" <<'PY'
import json, sys
with open(sys.argv[1]) as stream:
    doc = json.load(stream)
print({r["name"]: r["status"] for r in doc["results"]}.get(sys.argv[2], "missing"))
PY
}

# expect <label> <mode> <service> <check name> <status> [explicit|default]
expect() {
    local label="$1" mode="$2" service="$3" check="$4" want="$5" config="${6:-explicit}" got
    run_check "$mode" "$service" "$config"
    got="$(result_status "$check")" \
        || fail "$label: --json output is not one JSON document: $(head -c 300 "$TMP_DIR/out.json")"
    [[ "$got" == "$want" ]] \
        || fail "$label: '$check' is '$got', expected '$want' (details: $(grep -o "\"name\": \"$check\"[^}]*" "$TMP_DIR/out.json" | head -n 1))"
    pass "$label"
}

expect "Tool Calling passes when the model calls get_weather" call tool-calling "Tool Calling" pass
python3 - "$TMP_DIR/requests.jsonl" <<'PY' || fail "the Tool Calling request did not name the served model and the get_weather tool"
import json, sys
with open(sys.argv[1]) as stream:
    requests = [json.loads(line) for line in stream if line.strip()]
assert requests, "no parseable chat request reached the server"
request = requests[-1]
assert request["model"] == "fixture-model.gguf", request["model"]
assert request["tools"][0]["function"]["name"] == "get_weather", request["tools"]
assert request["tool_choice"] == "auto", request
PY
pass "the Tool Calling request is valid JSON naming the served model and get_weather"

expect "Tool Calling fails on an error body" error tool-calling "Tool Calling" fail
expect "Tool Calling fails on a reply without a call (\"tool_calls\": null)" no-call tool-calling "Tool Calling" fail
expect "Tool Calling reads the install's .env by default (empty HOME)" call tool-calling "Tool Calling" pass default

expect "LLM Inference passes on an assistant answer" text llm "LLM Inference" pass
expect "LLM Inference passes when a thinking model only reasoned" reasoning llm "LLM Inference" pass
expect "LLM Inference fails on an error body" error llm "LLM Inference" fail
