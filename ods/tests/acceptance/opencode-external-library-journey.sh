#!/usr/bin/env bash
# Disposable installed proof: lean external-model ODS -> Library OpenCode -> completion.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
audit_root="${RUNNER_TEMP:?runner temp is required}/ods-opencode-library"
export INSTALL_DIR="$audit_root/install"
export LOG_FILE="$audit_root/install.log"
key_file="$audit_root/mock.key"
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
    if [[ -f "$audit_root/mock.log" ]]; then
        tail -n 20 "$audit_root/mock.log" >&2 || true
    fi
    exit 1
}

[[ "${GITHUB_ACTIONS:-}" == true ]] || fail 'refusing non-disposable host'
[[ "$RUNNER_TEMP" == /* && "$INSTALL_DIR" == "$RUNNER_TEMP"/* ]] || fail 'install path is outside runner temp'
[[ "$(git -C "$(dirname "$product")" rev-parse HEAD)" == 23429fa4bc3b555c4ff61a81a6986acc8021e177 ]] \
    || fail 'product source changed'
[[ "$(cat /proc/1/comm)" == systemd ]] || fail 'runner is not a systemd host'
docker info >/dev/null || fail 'isolated Docker Engine unavailable'
[[ ! -e "$INSTALL_DIR" ]] || fail 'fresh install path is not empty'
if docker ps -a --format '{{.Names}}' | grep -Eq '^ods-'; then
    fail 'pre-existing ODS containers on runner'
fi

umask 077
mkdir -p "$audit_root"
python3 - "$key_file" <<'PY'
import secrets, sys
with open(sys.argv[1], "w", encoding="ascii") as stream:
    stream.write("mock-" + secrets.token_hex(24))
PY
chmod 600 "$key_file"
python3 "$harness/mock-openai-upstream.py" --key-file "$key_file" \
    --port "$mock_port" >"$audit_root/mock.log" 2>&1 &
mock_pid=$!
for attempt in {1..30}; do
    curl -fsS --max-time 2 "http://127.0.0.1:$mock_port/healthz" >/dev/null 2>&1 && break
    sleep 1
done
curl -fsS --max-time 2 "http://127.0.0.1:$mock_port/healthz" >/dev/null \
    || fail 'mock upstream did not start'

if ! (umask 022; cd "$product" && timeout 2400s bash install-core.sh \
    --non-interactive --skip-docker --no-bootstrap \
    --external-llm-url "http://127.0.0.1:$mock_port" \
    --external-llm-provider openai-compatible \
    --external-llm-model ods-acceptance-mock \
    --external-llm-key-file "$key_file") >>"$LOG_FILE" 2>&1; then
    fail 'exact product installer did not finish; log retained on disposable runner'
fi

[[ "$(grep -m1 '^ODS_MODEL_SWITCHBOARD=' "$INSTALL_DIR/.env" | cut -d= -f2-)" == observe ]] \
    || fail 'external install did not select the observed direct gateway route'
if docker ps -a --format '{{.Names}}' | grep -Fxq ods-llama-server; then
    fail 'external model install launched a managed llama-server'
fi
printf 'PASS: fresh external-model ODS omitted managed llama-server\n'

uid="$(id -u)"
if [[ ! -S "/run/user/$uid/bus" ]]; then
    sudo loginctl enable-linger "$USER" || fail 'could not enable the disposable runner user bus'
    sudo systemctl start "user@$uid.service" || fail 'could not start the disposable runner user manager'
fi
[[ -S "/run/user/$uid/bus" ]] || fail 'systemd user bus unavailable on runner'
export XDG_RUNTIME_DIR="/run/user/$uid"
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus"

status_file="$audit_root/opencode-status.json"
curl -fsS --max-time 30 http://127.0.0.1:3001/api/apps/opencode >"$status_file" \
    || fail 'Dashboard OpenCode status unavailable before Add'
python3 - "$status_file" <<'PY' || fail 'Library did not offer OpenCode setup'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["state"] == "not_installed" and state["setupSupported"] is True, state
PY

setup_code="$(curl -sS --max-time 40 -o "$audit_root/setup.json" -w '%{http_code}' \
    -X POST http://127.0.0.1:3001/api/apps/opencode/setup)" \
    || fail 'OpenCode Library setup request was unreachable'
[[ "$setup_code" == 202 ]] || fail "OpenCode Library setup returned HTTP $setup_code"

state=""
for attempt in {1..180}; do
    curl -fsS --max-time 30 http://127.0.0.1:3001/api/apps/opencode >"$status_file" \
        || fail 'OpenCode Library status unavailable during setup'
    state="$(python3 - "$status_file" <<'PY'
import json, sys
item=json.load(open(sys.argv[1],encoding="utf-8"))
print(item["state"])
PY
)"
    [[ "$state" == running ]] && break
    if python3 - "$status_file" <<'PY'
import json, sys
item=json.load(open(sys.argv[1],encoding="utf-8"))
raise SystemExit(0 if (item.get("progress") or {}).get("status") == "error" else 1)
PY
    then
        fail 'OpenCode setup reported an error'
    fi
    sleep 5
done
[[ "$state" == running ]] || fail "OpenCode did not reach running state after $attempt polls"
systemctl --user is-active --quiet opencode-web.service || fail 'managed OpenCode user service is not active'
curl -fsS --max-time 20 http://127.0.0.1:3003/global/health >/dev/null \
    || fail 'managed OpenCode did not answer health'
printf 'PASS: Dashboard Library added and started managed OpenCode\n'

python3 - "$INSTALL_DIR/.env" "$HOME/.config/opencode/opencode.json" <<'PY' || fail 'OpenCode configuration did not match the external LiteLLM route'
import json, os, stat, sys
values = {}
for line in open(sys.argv[1], encoding="utf-8"):
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.rstrip("\n").split("=", 1)
        values[key] = value.strip().strip("\"'")
key = values.get("LITELLM_KEY")
assert key
config_path = sys.argv[2]
assert stat.S_IMODE(os.stat(config_path).st_mode) == 0o600
config = json.load(open(config_path, encoding="utf-8"))
assert config["model"] == "llama-server/ods-acceptance-mock"
provider = config["provider"]["llama-server"]
assert provider["name"] == "External LLM via ODS gateway"
assert provider["options"] == {
    "baseURL": "http://127.0.0.1:4000/v1", "apiKey": key,
}
PY

binary="$HOME/.opencode/bin/opencode"
[[ -x "$binary" ]] || fail 'managed OpenCode executable is missing'
"$binary" run --help >"$audit_root/opencode-run-help.txt" 2>&1 \
    || fail 'pinned OpenCode does not expose a run command'
requests_before="$(grep -Fc 'accept=chat' "$audit_root/mock.log" || true)"
if ! (cd "$audit_root" && timeout 180 "$binary" run --model llama-server/ods-acceptance-mock \
    'Reply exactly OK.') >"$audit_root/opencode-run.out" 2>"$audit_root/opencode-run.err"; then
    # Keep the private gateway key out of diagnostics even if a dependency logs it.
    python3 - "$INSTALL_DIR/.env" "$audit_root/opencode-run.err" <<'PY' >&2
import sys
values = {}
for line in open(sys.argv[1], encoding="utf-8"):
    if "=" in line:
        key, value = line.rstrip("\n").split("=", 1)
        values[key] = value.strip().strip("\"'")
message = open(sys.argv[2], encoding="utf-8", errors="replace").read()[-1200:]
secret = values.get("LITELLM_KEY", "")
print(message.replace(secret, "[redacted]") if secret else "OpenCode run failed")
PY
    fail 'OpenCode did not complete through the ODS external model route'
fi
grep -Fq OK "$audit_root/opencode-run.out" || fail 'OpenCode completion did not contain OK'
requests_after="$(grep -Fc 'accept=chat' "$audit_root/mock.log" || true)"
[[ "$requests_after" -gt "$requests_before" ]] \
    || fail 'external upstream did not receive a new OpenCode completion'
printf 'PASS: OpenCode completed through authenticated LiteLLM and the external model\n'
