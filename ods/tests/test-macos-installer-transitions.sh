#!/usr/bin/env bash
# Focused contracts for macOS installer local/cloud transitions.

if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[FAIL] Bash 4+ is required" >&2
    exit 1
fi

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$ROOT_DIR/installers/macos/install-macos.sh"
ENV_GENERATOR="$ROOT_DIR/installers/macos/lib/env-generator.sh"
TMP_DIR="$(mktemp -d)"
SERVER_PID=""
trap '[[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

extract_installer_function() {
    sed -n "/^${1}() {/,/^}$/p" "$INSTALLER"
}

python_cmd="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
[[ -n "$python_cmd" ]] || fail "python is required"
"$python_cmd" -c 'import yaml' 2>/dev/null || fail "PyYAML is required"

ai() { :; }
ai_ok() { :; }
ai_warn() { :; }
ai_err() { echo "[ERROR] $*" >&2; }
log() { :; }

# Canonical extension state must survive resolver cache invalidation.
eval "$(extract_installer_function _macos_set_builtin_compose_state)"
INSTALL_DIR="$TMP_DIR/state-install"
mkdir -p "$INSTALL_DIR/extensions/services/hermes"
printf 'services: {}\n' > "$INSTALL_DIR/extensions/services/hermes/compose.yaml"
_macos_set_builtin_compose_state hermes false
[[ -f "$INSTALL_DIR/extensions/services/hermes/compose.yaml.disabled" ]] \
    || fail "disabled built-in did not use compose.yaml.disabled"
[[ ! -e "$INSTALL_DIR/extensions/services/hermes/compose.yaml" ]] \
    || fail "disabled built-in left compose.yaml active"
_macos_set_builtin_compose_state hermes false
_macos_set_builtin_compose_state hermes true
[[ -f "$INSTALL_DIR/extensions/services/hermes/compose.yaml" ]] \
    || fail "selected built-in did not restore compose.yaml"
printf 'stale\n' > "$INSTALL_DIR/extensions/services/hermes/compose.yaml.disabled"
_macos_set_builtin_compose_state hermes true
[[ ! -e "$INSTALL_DIR/extensions/services/hermes/compose.yaml.disabled" ]] \
    || fail "selected built-in retained a stale disabled twin"
pass "canonical built-in compose state is idempotent"

# Persisted Hermes config is patched through a root container execution path.
eval "$(extract_installer_function _macos_patch_hermes_persisted_config)"
INSTALL_DIR="$TMP_DIR/hermes-install"
mkdir -p "$INSTALL_DIR/data/hermes"
cat > "$INSTALL_DIR/data/hermes/config.yaml" <<'YAML'
model:
  default: old-model
  base_url: http://old.invalid/v1
  context_length: 1024
  api_key: stale-secret
auxiliary:
  compression:
    context_length: 1024
custom:
  preserve: true
YAML
docker() {
    if [[ "$1" == "inspect" ]]; then
        printf 'running\n'
        return 0
    fi
    if [[ "$1" == "exec" && "${6:-}" == "-c" ]]; then
        return 0
    fi
    [[ "$1" == "exec" && "$2" == "--user" && "$3" == "0:0" ]] \
        || fail "Hermes live patch did not execute as container root"
    command cat > "$TMP_DIR/hermes-live-patch.py"
    sed \
        -e 's|Path("/opt/data/config.yaml")|Path(os.environ["HERMES_TEST_PATH"])|' \
        -e 's|os.chown(tmp, st.st_uid, st.st_gid)|getattr(os, "chown", lambda *_: None)(tmp, st.st_uid, st.st_gid)|' \
        "$TMP_DIR/hermes-live-patch.py" > "$TMP_DIR/hermes-live-patch-test.py"
    HERMES_TEST_PATH="$INSTALL_DIR/data/hermes/config.yaml" \
        "$python_cmd" "$TMP_DIR/hermes-live-patch-test.py" "${8}" "${9}" "${10}"
}
read_env_value() { printf '\n'; }
_macos_patch_hermes_persisted_config default http://litellm:4000/v1 200000 \
    || fail "Hermes persisted config patch failed"
"$python_cmd" - "$INSTALL_DIR/data/hermes/config.yaml" <<'PY'
import sys, yaml
data = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
model = data["model"]
assert model["default"] == "default"
assert model["base_url"] == "http://litellm:4000/v1"
assert model["context_length"] == 200000
assert "api_key" not in model
assert data["auxiliary"]["compression"]["context_length"] == 200000
assert data["custom"]["preserve"] is True
PY
pass "Hermes persisted routing is container-patched and verified"

# Disabled Hermes still has authoritative persisted state. Its cached runtime
# image may exist without PyYAML, so the installer must select a verified
# dashboard API helper rather than silently accepting stale routing.
cat > "$INSTALL_DIR/data/hermes/config.yaml" <<'YAML'
model:
  default: stale-local-model
  base_url: http://host.docker.internal:8080/v1
  context_length: 1024
auxiliary:
  compression:
    context_length: 1024
YAML
docker() {
    if [[ "$1" == "inspect" ]]; then
        case "${3:-}:${4:-}" in
            "{{.State.Status}}:ods-hermes") printf 'exited\n' ;;
            "{{.Config.Image}}:ods-hermes") printf 'missing-hermes:latest\n' ;;
            "{{.Config.Image}}:ods-dashboard-api") printf '\n' ;;
        esac
        return 0
    fi
    if [[ "$1" == "image" && "$2" == "inspect" ]]; then
        [[ "$3" == "hermes-install-dashboard-api:latest" || "$3" == "missing-hermes:latest" ]]
        return $?
    fi
    if [[ "$1" == "run" && " $* " == *" -c import yaml "* ]]; then
        [[ " $* " == *" --entrypoint python3 hermes-install-dashboard-api:latest -c import yaml "* ]]
        return $?
    fi
    [[ "$1" == "run" && " $* " == *" --entrypoint python3 hermes-install-dashboard-api:latest - cloud-default http://litellm:4000/v1 131072 "* ]] \
        || fail "persisted Hermes fallback did not use the dashboard API image"
    command cat > "$TMP_DIR/hermes-helper-patch.py"
    sed \
        -e 's|Path("/opt/data/config.yaml")|Path(os.environ["HERMES_TEST_PATH"])|' \
        -e 's|os.chown(tmp, st.st_uid, st.st_gid)|getattr(os, "chown", lambda *_: None)(tmp, st.st_uid, st.st_gid)|' \
        "$TMP_DIR/hermes-helper-patch.py" > "$TMP_DIR/hermes-helper-patch-test.py"
    HERMES_TEST_PATH="$INSTALL_DIR/data/hermes/config.yaml" \
        "$python_cmd" "$TMP_DIR/hermes-helper-patch-test.py" \
            cloud-default http://litellm:4000/v1 131072
}
_macos_patch_hermes_persisted_config cloud-default http://litellm:4000/v1 131072 \
    || fail "disabled Hermes persisted routing was not patched through the helper image"
"$python_cmd" - "$INSTALL_DIR/data/hermes/config.yaml" <<'PY'
import sys, yaml
data = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
assert data["model"]["default"] == "cloud-default"
assert data["model"]["base_url"] == "http://litellm:4000/v1"
assert data["model"]["context_length"] == 131072
assert data["auxiliary"]["compression"]["context_length"] == 131072
PY

docker() {
    if [[ "$1" == "inspect" && "${4:-}" == "ods-hermes" ]]; then
        [[ "${3:-}" == "{{.State.Status}}" ]] && printf 'exited\n' \
            || printf 'missing-hermes:latest\n'
        return 0
    fi
    return 1
}
if _macos_patch_hermes_persisted_config cloud-default http://litellm:4000/v1 131072; then
    fail "persisted Hermes routing succeeded without any safe helper image"
else
    [[ "$?" -eq 4 ]] || fail "missing Hermes helper image did not fail closed"
fi
pass "disabled Hermes routing patches through a safe image or fails closed"

# OpenCode and OpenClaw must update only their managed routes across modes.
eval "$(extract_installer_function _write_macos_opencode_config | sed "s|/usr/bin/python3|$python_cmd|g")"
opencode_path="$TMP_DIR/opencode/opencode.json"
mkdir -p "$(dirname "$opencode_path")"
printf '{"custom":{"preserve":true}}\n' > "$opencode_path"
opencode_secret="sk-opencode-transition-secret"
opencode_output="$(_write_macos_opencode_config "$opencode_path" default \
    http://127.0.0.1:4000/v1 "$opencode_secret" 200000 2>&1)"
[[ "$opencode_output" != *"$opencode_secret"* ]] || fail "OpenCode secret was logged"
"$python_cmd" - "$opencode_path" "$opencode_secret" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["custom"]["preserve"] is True
assert data["model"] == "llama-server/default"
opts = data["provider"]["llama-server"]["options"]
assert opts == {"baseURL": "http://127.0.0.1:4000/v1", "apiKey": sys.argv[2]}
PY
_write_macos_opencode_config "$opencode_path" "ods/current" \
    http://127.0.0.1:4000/v1 "$opencode_secret" 131072 >/dev/null
"$python_cmd" - "$opencode_path" "$opencode_secret" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["model"] == "llama-server/ods/current"
provider = data["provider"]["llama-server"]
assert provider["models"]["ods/current"]["limit"] == {"context": 131072, "output": 32768}
assert provider["options"] == {"baseURL": "http://127.0.0.1:4000/v1", "apiKey": sys.argv[2]}
PY

# shellcheck source=/dev/null
source "$ENV_GENERATOR"
openclaw_dir="$TMP_DIR/openclaw-install"
mkdir -p "$openclaw_dir/data/openclaw/home"
printf '{"custom":{"preserve":true}}\n' > "$openclaw_dir/data/openclaw/home/openclaw.json"
openclaw_secret="sk-openclaw-transition-secret"
openclaw_output="$(generate_openclaw_config "$openclaw_dir" default 200000 token \
    http://litellm:4000 false "$openclaw_secret" 2>&1)"
[[ "$openclaw_output" != *"$openclaw_secret"* ]] || fail "OpenClaw secret was logged"
"$python_cmd" - "$openclaw_dir" "$openclaw_secret" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]) / "data/openclaw/home"
home = json.load(open(root / "openclaw.json", encoding="utf-8"))
auth = json.load(open(root / "agents/main/agent/auth-profiles.json", encoding="utf-8"))
provider = home["models"]["providers"]["local-llama"]
assert home["custom"]["preserve"] is True
assert provider["baseUrl"] == "http://litellm:4000"
assert provider["apiKey"] == sys.argv[2]
assert home["agents"]["defaults"]["model"]["primary"] == "local-llama/default"
assert auth["profiles"]["local-llama:default"]["key"] == sys.argv[2]
PY
generate_openclaw_config "$openclaw_dir" local.gguf 65536 token \
    http://host.docker.internal:8080 false none >/dev/null
"$python_cmd" - "$openclaw_dir/data/openclaw/home/openclaw.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
provider = data["models"]["providers"]["local-llama"]
assert provider["baseUrl"] == "http://host.docker.internal:8080"
assert provider["apiKey"] == "none"
assert data["agents"]["defaults"]["model"]["primary"] == "local-llama/local.gguf"
PY
pass "OpenCode and OpenClaw routes transition without secret output"

# Fake the pinned Perplexica provider/config API, including its fresh state with
# no OpenAI provider, then exercise cloud -> local updates through production code.
cat > "$TMP_DIR/perplexica-server.py" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

values = {
    "version": 1,
    "setupComplete": False,
    "preferences": {},
    "modelProviders": [{
        "id": "transformers-1", "name": "Transformers", "type": "transformers",
        "config": {}, "chatModels": [],
        "embeddingModels": [{"key": "Xenova/all-MiniLM-L6-v2", "name": "all-MiniLM-L6-v2"}],
    }],
}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def send_json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
    def do_GET(self):
        if self.path == "/api/config":
            self.send_json({"values": values, "fields": {}})
        else:
            self.send_json({}, 404)
    def do_POST(self):
        body = self.body()
        if self.path == "/api/providers":
            provider = {"id": "ods-openai-1", "chatModels": [], "embeddingModels": [], **body}
            values["modelProviders"].append(provider)
            self.send_json({"provider": provider})
        elif self.path == "/api/config":
            values[body["key"]] = body["value"]
            self.send_json({"message": "ok"})
        elif self.path == "/api/config/setup-complete":
            values["setupComplete"] = True
            self.send_json({"message": "ok"})
        elif self.path.endswith("/models"):
            provider = next(p for p in values["modelProviders"] if p["id"] in self.path)
            provider["chatModels"].append({"key": body["key"], "name": body["name"]})
            self.send_json({"message": "ok"})
        else:
            self.send_json({}, 404)
    def do_PATCH(self):
        body = self.body()
        provider = next(p for p in values["modelProviders"] if p["id"] in self.path)
        provider["name"] = body["name"]
        provider["config"] = body["config"]
        self.send_json({"provider": provider})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
open(sys.argv[1], "w", encoding="ascii").write(str(server.server_port))
server.serve_forever()
PY
"$python_cmd" "$TMP_DIR/perplexica-server.py" "$TMP_DIR/perplexica.port" &
SERVER_PID=$!
for _ in $(seq 1 50); do [[ -s "$TMP_DIR/perplexica.port" ]] && break; sleep 0.1; done
[[ -s "$TMP_DIR/perplexica.port" ]] || fail "Perplexica fixture did not start"
perplexica_port="$(cat "$TMP_DIR/perplexica.port")"
perplexica_secret="sk-perplexica-transition-secret"
perplexica_output="$(configure_perplexica "$perplexica_port" default \
    http://litellm:4000 "$perplexica_secret" 2>&1)" \
    || fail "fresh Perplexica cloud configuration failed"
[[ "$perplexica_output" != *"$perplexica_secret"* ]] || fail "Perplexica secret was logged"
configure_perplexica "$perplexica_port" local.gguf \
    http://host.docker.internal:8080 no-key >/dev/null \
    || fail "Perplexica cloud-to-local transition failed"
"$python_cmd" - "$perplexica_port" <<'PY'
import json, sys, urllib.request
data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/config"))["values"]
providers = [p for p in data["modelProviders"] if p["type"] == "openai"]
assert len(providers) == 1
provider = providers[0]
assert provider["config"] == {"baseURL": "http://host.docker.internal:8080/v1", "apiKey": "no-key"}
assert any(m["key"] == "local.gguf" for m in provider["chatModels"])
assert data["preferences"]["defaultChatProvider"] == provider["id"]
assert data["preferences"]["defaultChatModel"] == "local.gguf"
assert data["setupComplete"] is True
PY
pass "Perplexica fresh and cloud-to-local provider transitions verify"

# Fail-closed and dry-run contracts remain explicit at the installer boundary.
grep -Fq 'if [[ "$CLOUD_REQUIRED_HEALTHY" != "true" ]]; then' "$INSTALLER" \
    || fail "required cloud health does not gate installer success"
grep -A3 -F 'if [[ "$CLOUD_REQUIRED_HEALTHY" != "true" ]]; then' "$INSTALLER" | grep -Fq 'exit 1' \
    || fail "required cloud health failure does not exit nonzero"
grep -Fq '[DRY RUN] Would install, configure, and verify the authenticated dashboard host-agent path' "$INSTALLER" \
    || fail "dry-run does not bypass host-agent and bridge mutation"
bootstrap_dry_line="$(grep -n '\[ "\$_ods_bootstrap_dry_run" = true \]' "$INSTALLER" | head -1 | cut -d: -f1)"
brew_install_line="$(grep -n '^  brew install bash' "$INSTALLER" | head -1 | cut -d: -f1)"
[[ -n "$bootstrap_dry_line" && -n "$brew_install_line" && "$bootstrap_dry_line" -lt "$brew_install_line" ]] \
    || fail "Bash bootstrap can mutate the host before honoring --dry-run"
[[ "$(grep -Fc 'if _ods_bash_is_modern "$candidate"; then' "$INSTALLER")" -eq 2 ]] \
    || fail "Bash bootstrap can hand off to an unsupported shell and recurse"
pass "cloud health fails closed and dry-run skips host-agent mutation"

echo "[OK] macOS installer transition contracts hold"
