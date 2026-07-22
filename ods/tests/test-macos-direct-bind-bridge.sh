#!/usr/bin/env bash
# Regression: a direct native llama bind must replace the Colima LLM bridge,
# while the default loopback bind must leave the bridge in place.

# ods-macos.sh deliberately re-execs itself with Homebrew Bash on macOS. When
# sourced by this fixture under Apple's Bash 3.2, that re-exec terminates the
# fixture subshell before its assertions run. Re-exec the complete fixture first
# so every assertion executes with the same supported Bash runtime as the CLI.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [ -x "$candidate" ]; then
            exec "$candidate" "$0" "$@"
        fi
    done
    echo "[FAIL] Bash 4+ is required for the macOS CLI contract" >&2
    exit 1
fi

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/installers/macos/ods-macos.sh"
INSTALLER="$ROOT_DIR/installers/macos/install-macos.sh"
BRIDGE_MANAGER="$ROOT_DIR/installers/macos/lib/bridge-manager.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$CLI" ]] || fail "missing $CLI"
[[ -f "$INSTALLER" ]] || fail "missing $INSTALLER"
[[ -f "$BRIDGE_MANAGER" ]] || fail "missing $BRIDGE_MANAGER"

python_cmd="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
[[ -n "$python_cmd" ]] || fail "python is required to parse embedded installer programs"

"$python_cmd" - "$INSTALLER" "$BRIDGE_MANAGER" <<'PY'
import ast
import os
import re
import subprocess
import sys
from pathlib import Path


def skip_shell_layout(source: str, offset: int) -> int:
    while offset < len(source):
        if source.startswith("\\\r\n", offset):
            offset += 3
        elif source.startswith("\\\n", offset):
            offset += 2
        elif source[offset].isspace():
            offset += 1
        else:
            break
    return offset


def read_shell_word(source: str, offset: int) -> tuple[str, int]:
    offset = skip_shell_layout(source, offset)
    value: list[str] = []
    started = False

    while offset < len(source):
        char = source[offset]
        if char.isspace() and started:
            break
        if char in ";|&()<>" and not started:
            raise ValueError(f"expected a Python program at byte {offset}")
        if char in ";|&()<>" and started:
            break

        started = True
        if char == "'":
            end = source.find("'", offset + 1)
            if end < 0:
                raise ValueError(f"unterminated single-quoted word at byte {offset}")
            value.append(source[offset + 1 : end])
            offset = end + 1
            continue

        if char == '"':
            offset += 1
            while offset < len(source) and source[offset] != '"':
                if source.startswith("\\\r\n", offset):
                    offset += 3
                elif source.startswith("\\\n", offset):
                    offset += 2
                elif source[offset] == "\\" and offset + 1 < len(source):
                    escaped = source[offset + 1]
                    value.append(escaped if escaped in '$`"\\' else "\\" + escaped)
                    offset += 2
                else:
                    value.append(source[offset])
                    offset += 1
            if offset >= len(source):
                raise ValueError("unterminated double-quoted Python program")
            offset += 1
            continue

        if source.startswith("\\\r\n", offset):
            offset += 3
        elif source.startswith("\\\n", offset):
            offset += 2
        elif char == "\\" and offset + 1 < len(source):
            value.append(source[offset + 1])
            offset += 2
        else:
            value.append(char)
            offset += 1

    if not started:
        raise ValueError(f"missing Python program at byte {offset}")
    return "".join(value), offset


programs = []
for path_arg in sys.argv[1:]:
    path = Path(path_arg)
    source = path.read_text(encoding="utf-8")
    commands = list(re.finditer(r"(?<![A-Za-z0-9_./-])/usr/bin/python3[ \t]+-c(?=[\s\\])", source))
    for index, command in enumerate(commands, 1):
        line = source.count("\n", 0, command.start()) + 1
        try:
            program, end = read_shell_word(source, command.end())
            ast.parse(program, filename=f"{path}:{line}", mode="exec")
        except (SyntaxError, ValueError) as exc:
            raise SystemExit(f"embedded Python program {index} at {path}:{line} is invalid: {exc}") from exc
        programs.append((path, source, line, program, end))

if not programs:
    raise SystemExit("no embedded /usr/bin/python3 -c programs found")
print(f"[PASS] parsed {len(programs)} embedded /usr/bin/python3 -c programs")

route_programs = [item for item in programs if "ipaddress.ip_network" in item[3]]
if len(route_programs) != 1:
    raise SystemExit(f"expected one Colima route validator, found {len(route_programs)}")
route_path, route_source, route_line, route_program, route_end = route_programs[0]
route_tail = route_source[route_end : route_source.find("\n", route_end)]
if not re.search(r">\s*/dev/null\s+2>&1\s*\|\|\s*return\s+1", route_tail):
    raise SystemExit(f"Colima route validator at {route_path}:{route_line} does not propagate failure")


def route_result(vm: str, host: str) -> int:
    env = os.environ.copy()
    env.update(COLIMA_VM_IP=vm, COLIMA_HOST_IP=host)
    return subprocess.run(
        [sys.executable, "-c", route_program],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    ).returncode


if route_result("192.168.106.2", "192.168.106.1") != 0:
    raise SystemExit("valid private Colima route was rejected")
if route_result("192.168.106.2", "192.168.107.1") == 0:
    raise SystemExit("different-subnet Colima route was accepted")
if route_result("192.168.106.2", "192.168.106.2") == 0:
    raise SystemExit("identical Colima VM and host addresses were accepted")
print("[PASS] Colima route validation rejects invalid addresses and propagates failure")
PY

extract_installer_function() {
    local function_name="$1"
    sed -n "/^${function_name}() {/,/^}$/p" "$INSTALLER"
}

# Execute the production cloud-auth overlay writer. The generated file must
# contain only a Compose interpolation reference, never the master key itself.
eval "$(extract_installer_function _write_macos_cloud_auth_overlay)"
cloud_auth_overlay="$TMP_DIR/docker-compose.macos-cloud-auth.yml"
render_key="sk-render-contract-0123456789"
local_hermes_key="sk-ods-hermes-local"
_write_macos_cloud_auth_overlay "$cloud_auth_overlay"
grep -Fq 'OPENAI_API_KEY: "${LITELLM_KEY:?LITELLM_KEY must be set}"' "$cloud_auth_overlay" \
    || fail "cloud auth overlay does not source client auth from LITELLM_KEY"
[[ "$(grep -Fc 'OPENAI_API_KEY:' "$cloud_auth_overlay")" -eq 1 ]] \
    || fail "cloud auth overlay must configure only the always-present Open WebUI service"
if grep -q '^  hermes:$' "$cloud_auth_overlay"; then
    fail "cloud auth overlay creates a partial optional Hermes service"
fi
if grep -Fq "$render_key" "$cloud_auth_overlay"; then
    fail "cloud auth overlay persisted the LiteLLM master key"
fi
pass "cloud auth overlay keeps the LiteLLM key secret and covers enabled clients"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    cloud_render="$({
        WEBUI_SECRET="render-webui-secret" \
        LITELLM_KEY="$render_key" \
        HERMES_LLM_API_KEY="$render_key" \
        ODS_MODE=cloud \
        LLM_API_URL=http://litellm:4000 \
        HERMES_LLM_BASE_URL=http://litellm:4000/v1 \
        docker compose --project-directory "$ROOT_DIR" \
            -f "$ROOT_DIR/docker-compose.base.yml" \
            -f "$ROOT_DIR/docker-compose.cloud.yml" \
            -f "$ROOT_DIR/extensions/services/litellm/compose.yaml" \
            -f "$ROOT_DIR/extensions/services/hermes/compose.yaml" \
            -f "$cloud_auth_overlay" \
            config --format json
    } 2>/dev/null)" || fail "could not render cloud auth compose contract"
    printf '%s' "$cloud_render" | "$python_cmd" -c '
import json, sys
key = sys.argv[1]
services = json.load(sys.stdin)["services"]
assert services["litellm"]["environment"]["LITELLM_MASTER_KEY"] == key
assert services["open-webui"]["environment"]["OPENAI_API_KEY"] == key
assert services["hermes"]["environment"]["OPENAI_API_KEY"] == key
' "$render_key" || fail "cloud clients did not render with LiteLLM master-key parity"

    local_render="$({
        WEBUI_SECRET="render-webui-secret" \
        LITELLM_KEY="$render_key" \
        HERMES_LLM_API_KEY="$local_hermes_key" \
        ODS_MODE=local \
        LLM_API_URL=http://llama-server:8080 \
        HERMES_LLM_BASE_URL=http://llama-server:8080/v1 \
        docker compose --project-directory "$ROOT_DIR" \
            -f "$ROOT_DIR/docker-compose.base.yml" \
            -f "$ROOT_DIR/extensions/services/litellm/compose.yaml" \
            -f "$ROOT_DIR/extensions/services/hermes/compose.yaml" \
            config --format json
    } 2>/dev/null)" || fail "could not render local auth compose contract"
    printf '%s' "$local_render" | "$python_cmd" -c '
import json, sys
local_key = sys.argv[1]
services = json.load(sys.stdin)["services"]
assert services["open-webui"]["environment"]["OPENAI_API_KEY"] == ""
assert services["hermes"]["environment"]["OPENAI_API_KEY"] == local_key
' "$local_hermes_key" || fail "local client authentication behavior changed"
    pass "rendered cloud clients share LiteLLM auth while local auth stays unchanged"
else
    pass "cloud auth overlay contract holds (docker compose render unavailable)"
fi

# Exercise cancellation using production helper bodies. PID 4101 is owned by
# this install and ignores TERM; PID 4202 is a different install and must never
# receive a signal. The helper must wait, force only 4101, remove retry metadata,
# and leave an inactive status before cloud .env writes begin.
eval "$(extract_installer_function _macos_bootstrap_upgrade_pid_is_owned)"
eval "$(extract_installer_function _macos_collect_process_descendants)"
eval "$(extract_installer_function _macos_cancel_detached_bootstrap_upgrade)"
(
    INSTALL_DIR="$TMP_DIR/upgrade-install"
    mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/data"
    : > "$INSTALL_DIR/scripts/bootstrap-upgrade.sh"
    printf 'full.gguf\nhttps://example.invalid/full.gguf\n\nfull\n65536\nbootstrap.gguf\n' \
        > "$INSTALL_DIR/data/bootstrap-upgrade.args"
    printf '{"status":"downloading"}\n' > "$INSTALL_DIR/data/bootstrap-status.json"

    owned_alive=true
    kill_log="$TMP_DIR/bootstrap-kills.log"
    : > "$kill_log"
    pgrep() {
        [[ "$*" == *'[/]bootstrap-upgrade[.]sh'* ]] \
            || fail "bootstrap cancellation used an unscoped pgrep pattern"
        printf '4101\n4202\n'
    }
    ps() {
        local pid="" previous="" output=""
        for _arg in "$@"; do
            [[ "$previous" == "-p" ]] && pid="$_arg"
            [[ "$previous" == "-o" ]] && output="$_arg"
            previous="$_arg"
        done
        if [[ "$output" == "comm=" ]]; then
            printf 'bash\n'
            return
        fi
        if [[ "$output" == "pgid=" ]]; then
            printf '%s\n' "$pid"
            return
        fi
        case "$pid" in
            4101) printf 'bash %s/scripts/bootstrap-upgrade.sh %s full.gguf\n' "$INSTALL_DIR" "$INSTALL_DIR" ;;
            4202) printf 'bash %s/other-install/scripts/bootstrap-upgrade.sh %s/other-install other.gguf\n' "$TMP_DIR" "$TMP_DIR" ;;
        esac
    }
    kill() {
        local signal="$1"
        shift
        [[ "${1:-}" == "--" ]] && shift
        local pid="${1:-}"
        case "$signal:$pid" in
            -0:-4101) $owned_alive ;;
            -TERM:-4101) printf '%s %s\n' "$signal" "$pid" >> "$kill_log" ;;
            -KILL:-4101)
                printf '%s %s\n' "$signal" "$pid" >> "$kill_log"
                owned_alive=false
                ;;
            *:-4202|*:4202)
                printf '%s %s\n' "$signal" "$pid" >> "$kill_log"
                return 0
                ;;
            *) return 1 ;;
        esac
    }
    sleep() { :; }
    ai() { :; }
    ai_ok() { :; }
    ai_warn() { :; }
    ai_err() { fail "$*"; }

    _macos_cancel_detached_bootstrap_upgrade \
        || fail "install-owned bootstrap cancellation failed"
    grep -qx -- '-TERM -4101' "$kill_log" \
        || fail "install-owned bootstrap process group did not receive SIGTERM"
    grep -qx -- '-KILL -4101' "$kill_log" \
        || fail "stubborn install-owned bootstrap process group was not forced after the wait"
    if grep -q '4202' "$kill_log"; then
        fail "bootstrap cancellation signalled an unrelated install"
    fi
    [[ ! -e "$INSTALL_DIR/data/bootstrap-upgrade.args" ]] \
        || fail "cloud transition left bootstrap retry metadata enabled"
    grep -q '"status":"cancelled"' "$INSTALL_DIR/data/bootstrap-status.json" \
        || fail "cloud transition did not mark bootstrap state cancelled"
    grep -q '"reason":"cloud_mode"' "$INSTALL_DIR/data/bootstrap-status.json" \
        || fail "bootstrap cancellation status lacks its cloud-mode reason"
)
(
    INSTALL_DIR="$TMP_DIR/fresh-upgrade-install"
    mkdir -p "$INSTALL_DIR/data"
    printf 'full.gguf\nhttps://example.invalid/full.gguf\n\nfull\n65536\nbootstrap.gguf\n' \
        > "$INSTALL_DIR/data/bootstrap-upgrade.args"
    printf '{"status":"downloading"}\n' > "$INSTALL_DIR/data/bootstrap-status.json"
    pgrep() { return 1; }
    ai() { :; }
    ai_ok() { :; }
    ai_warn() { :; }
    ai_err() { fail "$*"; }

    _macos_cancel_detached_bootstrap_upgrade "fresh_install" "fresh install" \
        || fail "fresh install bootstrap cancellation failed"
    [[ ! -e "$INSTALL_DIR/data/bootstrap-upgrade.args" ]] \
        || fail "fresh install left bootstrap retry metadata enabled"
    grep -q '"status":"cancelled"' "$INSTALL_DIR/data/bootstrap-status.json" \
        || fail "fresh install did not mark bootstrap state cancelled"
    grep -q '"reason":"fresh_install"' "$INSTALL_DIR/data/bootstrap-status.json" \
        || fail "bootstrap cancellation status lacks its fresh-install reason"
)
cloud_cancel_line="$(grep -n '_macos_cancel_detached_bootstrap_upgrade "cloud_mode" "cloud transition"' "$INSTALLER" | sed -n '1p' | cut -d: -f1)"
fresh_cancel_line="$(grep -n '_macos_cancel_detached_bootstrap_upgrade "fresh_install" "fresh install"' "$INSTALLER" | sed -n '1p' | cut -d: -f1)"
env_line="$(grep -n 'generate_ods_env "\$INSTALL_DIR"' "$INSTALLER" | cut -d: -f1)"
[[ -n "$cloud_cancel_line" && -n "$env_line" && "$cloud_cancel_line" -lt "$env_line" ]] \
    || fail "cloud transition does not stop bootstrap workers before rewriting .env"
[[ -n "$fresh_cancel_line" && -n "$env_line" && "$fresh_cancel_line" -lt "$env_line" ]] \
    || fail "forced fresh install does not stop bootstrap workers before rewriting .env"
pass "cloud transition and forced fresh install disable stale bootstrap workers"

# The authenticated readiness helper must reject both absent and crashed core
# containers before probing, and must fail a running container whose bearer
# request never succeeds. No UI/log message may contain the key.
eval "$(extract_installer_function _verify_macos_dashboard_host_agent)"
(
    readiness_mode="missing"
    readiness_key="agent-readiness-secret"
    readiness_log="$TMP_DIR/readiness-ui.log"
    HOST_AGENT_BRIDGE_LOG="$TMP_DIR/host-agent-bridge.log"
    probe_calls=0
    : > "$readiness_log"
    read_env_value() {
        case "$2" in
            ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED) printf 'true\n' ;;
            ODS_AGENT_HOST) printf '192.168.106.1\n' ;;
            ODS_AGENT_PORT) printf '7710\n' ;;
            ODS_AGENT_KEY) printf '%s\n' "$readiness_key" ;;
        esac
    }
    docker() {
        case "$1" in
            inspect)
                case "$readiness_mode" in
                    missing) return 1 ;;
                    crashed) printf 'exited\n' ;;
                    *) printf 'running\n' ;;
                esac
                ;;
            exec)
                probe_calls=$((probe_calls + 1))
                [[ "$*" == *"Authorization: Bearer ${readiness_key}"* ]] \
                    || fail "authenticated readiness omitted the bearer key"
                [[ "$*" == *'/v1/model/status'* ]] \
                    || fail "authenticated readiness used an unauthenticated endpoint"
                [[ "$readiness_mode" == "probe-ok" ]]
                ;;
            *) return 2 ;;
        esac
    }
    sleep() { :; }
    ai() { printf 'AI %s\n' "$*" >> "$readiness_log"; }
    ai_ok() { printf 'OK %s\n' "$*" >> "$readiness_log"; }
    ai_warn() { printf 'WARN %s\n' "$*" >> "$readiness_log"; }
    ai_err() { printf 'ERR %s\n' "$*" >> "$readiness_log"; }

    if _verify_macos_dashboard_host_agent "$TMP_DIR/fake.env"; then
        fail "missing dashboard-api container passed authenticated readiness"
    fi
    [[ "$probe_calls" -eq 0 ]] || fail "missing dashboard-api container was probed"

    readiness_mode="crashed"
    if _verify_macos_dashboard_host_agent "$TMP_DIR/fake.env"; then
        fail "crashed dashboard-api container passed authenticated readiness"
    fi
    [[ "$probe_calls" -eq 0 ]] || fail "crashed dashboard-api container was probed"

    readiness_mode="probe-fails"
    readiness_key=""
    if _verify_macos_dashboard_host_agent "$TMP_DIR/fake.env"; then
        fail "empty ODS_AGENT_KEY passed authenticated readiness"
    fi
    [[ "$probe_calls" -eq 0 ]] || fail "empty ODS_AGENT_KEY reached the host-agent probe"

    readiness_key="agent-readiness-secret"
    if _verify_macos_dashboard_host_agent "$TMP_DIR/fake.env"; then
        fail "failed authenticated host-agent probe passed readiness"
    fi
    [[ "$probe_calls" -eq 20 ]] || fail "authenticated readiness did not exhaust its bounded retry budget"

    readiness_mode="probe-ok"
    _verify_macos_dashboard_host_agent "$TMP_DIR/fake.env" \
        || fail "healthy authenticated host-agent path failed readiness"
    if grep -Fq "$readiness_key" "$readiness_log"; then
        fail "authenticated readiness leaked ODS_AGENT_KEY into installer output"
    fi
)
grep -Fq 'if ! _verify_macos_dashboard_host_agent "$INSTALL_DIR/.env"; then' "$INSTALLER" \
    || fail "installer does not require authenticated dashboard-api readiness"
grep -Fq '[DRY RUN] Would install, configure, and verify the authenticated dashboard host-agent path' "$INSTALLER" \
    || fail "macOS dry-run does not skip live dashboard host-agent verification"
pass "dashboard-api readiness fails closed and never logs the host-agent key"

# The sourced CLI consumes these globals and mock functions dynamically.
# shellcheck disable=SC2034,SC2329
(
    export HOME="$TMP_DIR/home"
    export ODS_HOME="$TMP_DIR/install"
    mkdir -p "$HOME" "$ODS_HOME/data/models" "$ODS_HOME/bin"

    # Source the real CLI so the test exercises its production functions and
    # the shared macOS bind helper. The help command has no host side effects.
    # shellcheck source=/dev/null
    source "$CLI" help >/dev/null

    assert_direct_bind() {
        local expected="$1" bind_address="$2" gateway_address="$3" label="$4"
        local actual=false
        if macos_bind_uses_direct_gateway "$bind_address" "$gateway_address"; then
            actual=true
        fi
        [[ "$actual" == "$expected" ]] \
            || fail "$label: expected direct-bind=$expected, got $actual"
    }

    assert_direct_bind true "0.0.0.0" "192.168.106.1" "IPv4 wildcard"
    assert_direct_bind true "::" "192.168.106.1" "IPv6 wildcard"
    assert_direct_bind true "192.168.106.1" "192.168.106.1" "exact Colima gateway"
    assert_direct_bind true '"0.0.0.0"' "192.168.106.1" "quoted IPv4 wildcard"
    assert_direct_bind true "'::'" "192.168.106.1" "quoted IPv6 wildcard"
    assert_direct_bind false "127.0.0.1" "192.168.106.1" "IPv4 loopback"
    assert_direct_bind false "::1" "192.168.106.1" "IPv6 loopback"
    assert_direct_bind false "192.168.106.2" "192.168.106.1" "non-gateway address"
    pass "shared direct-bind helper distinguishes wildcard/gateway from loopback"

    grep -Fq 'macos_configure_llm_bridge_from_env' "$INSTALLER" \
        || fail "installer does not use the shared LLM bridge manager"
    grep -Fq 'macos_configure_llm_bridge_from_env' "$CLI" \
        || fail "macOS CLI does not use the shared LLM bridge manager"
    grep -Fq 'macos_bind_uses_direct_gateway "$bind_address" "$listen_host"' "$BRIDGE_MANAGER" \
        || fail "shared LLM bridge manager does not use the direct-bind decision"
    grep -Fq 'macos_bind_uses_direct_gateway "$agent_bind" "$listen_host"' "$INSTALLER" \
        || fail "installer host-agent bridge does not use the shared direct-bind decision"
    grep -Fq 'upsert_env_value "$env_file" "ODS_MACOS_LLM_BRIDGE_ENABLED" "$enabled"' "$BRIDGE_MANAGER" \
        || fail "shared LLM bridge manager does not persist its decision"
    grep -Fq 'upsert_env_value "$env_file" "ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED" "false"' "$INSTALLER" \
        || fail "installer does not persist the disabled host-agent bridge state"
    pass "installer bridge configuration uses and persists the shared decision"

    [[ "$(macos_bind_probe_host 0.0.0.0)" == "127.0.0.1" ]] \
        || fail "IPv4 wildcard health probe must use loopback"
    [[ "$(macos_bind_probe_host ::)" == "[::1]" ]] \
        || fail "IPv6 wildcard health probe must use bracketed loopback"
    [[ "$(macos_bind_probe_host 192.168.106.1)" == "192.168.106.1" ]] \
        || fail "exact-gateway health probe must use the bound address"
    [[ "$(macos_normalize_agent_bind ::)" == "0.0.0.0" ]] \
        || fail "unsupported IPv6 host-agent wildcard must normalize to IPv4 wildcard"
    pass "bind-aware health probes and host-agent normalization"

    grep -Fq 'upsert_env_value "${INSTALL_DIR}/.env" "ODS_MODE" "cloud"' "$INSTALLER" \
        || fail "cloud rerun does not persist ODS_MODE=cloud"
    grep -Fq 'upsert_env_value "${INSTALL_DIR}/.env" "LLM_API_URL" "http://litellm:4000"' "$INSTALLER" \
        || fail "cloud rerun does not route LLM traffic through LiteLLM"
    grep -Fq 'COMPOSE_FLAGS+=("-f" "docker-compose.cloud.yml")' "$INSTALLER" \
        || fail "macOS cloud mode does not select the cloud compose overlay"
    grep -Fq '$CLOUD_MODE && _hermes_model="default"' "$INSTALLER" \
        || fail "cloud rerun does not replace the persisted Hermes model"
    grep -Fq 'HEALTH_NAMES=("LiteLLM gateway" "Chat UI (Open WebUI)")' "$INSTALLER" \
        || fail "cloud verification still waits for native llama-server"
    grep -Fq "pgrep -f '[/]llama-server'" "$INSTALLER" \
        || fail "cloud transition does not reap install-owned native llama processes"
    stop_line="$(grep -n 'Stopping the old direct native listener before recreating the loopback Colima bridge' "$INSTALLER" | cut -d: -f1)"
    bridge_line="$(grep -n 'if ! _configure_macos_llm_bridge; then' "$INSTALLER" | cut -d: -f1)"
    [[ -n "$stop_line" && -n "$bridge_line" && "$stop_line" -lt "$bridge_line" ]] \
        || fail "direct-to-loopback transition recreates the bridge before stopping the old listener"
    grep -Fq 'upsert_env_value "${INSTALL_DIR}/.env" "ODS_AGENT_HOST" "host.docker.internal"' "$INSTALLER" \
        || fail "Docker Desktop transition does not restore the host-agent route"
    grep -Fq 'upsert_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_HOST_GATEWAY" ""' "$INSTALLER" \
        || fail "Docker Desktop transition retains the stale Colima host gateway"
    grep -Fq 'upsert_env_value "${INSTALL_DIR}/.env" "ODS_MACOS_VM_IP" ""' "$INSTALLER" \
        || fail "Docker Desktop transition retains the stale Colima VM address"
    pass "local/cloud reruns persist routing and retire native inference"

    EVENT_LOG="$TMP_DIR/events.log"
    export EVENT_LOG
    cat > "$ODS_HOME/bin/llama-server" <<'FAKE_LLAMA'
#!/usr/bin/env bash
printf 'exec' >> "$EVENT_LOG"
printf ' <%s>' "$@" >> "$EVENT_LOG"
printf '\n' >> "$EVENT_LOG"
FAKE_LLAMA
    chmod +x "$ODS_HOME/bin/llama-server"
    : > "$ODS_HOME/bin/ods-macos-llm-bridge.py"
    : > "$ODS_HOME/data/models/test.gguf"

    MACOS_BRIDGE_PYTHON="$TMP_DIR/fake-bridge-python"
    export MACOS_BRIDGE_PYTHON
    cat > "$MACOS_BRIDGE_PYTHON" <<'FAKE_PYTHON'
#!/usr/bin/env bash
exit 0
FAKE_PYTHON
    chmod +x "$MACOS_BRIDGE_PYTHON"

    LLAMA_SERVER_BIN="$ODS_HOME/bin/llama-server"
    LLAMA_SERVER_PID_FILE="$ODS_HOME/data/.llama-server.pid"
    LLAMA_SERVER_LOG="$ODS_HOME/data/llama-server.log"
    INSTALL_DIR="$ODS_HOME"

    launchctl() {
        {
            printf 'launchctl'
            printf ' <%s>' "$@"
            printf '\n'
        } >> "$EVENT_LOG"
    }
    id() {
        [[ "${1:-}" == "-u" ]] || return 2
        printf '501\n'
    }
    get_native_llama_status() {
        NATIVE_LLAMA_RUNNING=false
        NATIVE_LLAMA_PID=0
        NATIVE_LLAMA_HEALTHY=false
    }
    read_ods_env() {
        ENV_GGUF_FILE="test.gguf"
        ENV_CTX_SIZE="1024"
        ENV_ODS_NATIVE_LLAMA_PORT="8080"
        ENV_BIND_ADDRESS="$TEST_BIND"
        ENV_ODS_MACOS_HOST_GATEWAY="$TEST_GATEWAY"
        ENV_ODS_MODE="local"
        ENV_LLAMA_REASONING="off"
        unset ENV_LLAMA_ARG_FLASH_ATTN ENV_LLAMA_ARG_CACHE_TYPE_K \
            ENV_LLAMA_ARG_CACHE_TYPE_V ENV_LLAMA_ARG_N_CPU_MOE \
            ENV_LLAMA_ARG_SPEC_TYPE ENV_LLAMA_ARG_SPEC_DRAFT_N_MAX
    }
    read_env_value() {
        case "$2" in
            ODS_MODE) printf 'local\n' ;;
            BIND_ADDRESS) printf '%s\n' "$TEST_BIND" ;;
            ODS_MACOS_HOST_GATEWAY) printf '%s\n' "$TEST_GATEWAY" ;;
            ODS_MACOS_VM_IP) printf '192.168.106.2\n' ;;
            OLLAMA_PORT|ODS_NATIVE_LLAMA_PORT) printf '8080\n' ;;
            *) printf '\n' ;;
        esac
    }
    upsert_env_value() {
        if [[ "$2" == "ODS_MACOS_LLM_BRIDGE_ENABLED" ]]; then
            LAST_BRIDGE_ENABLED="$3"
        fi
    }
    ai() { :; }
    ai_ok() { :; }
    ai_warn() { :; }
    ai_err() { :; }
    sleep() { command sleep 0.05; }
    curl() { grep -q '^exec' "$EVENT_LOG"; }

    run_start_case() {
        local bind_address="$1" gateway_address="$2" expected_route="$3" label="$4"
        TEST_BIND="$bind_address"
        TEST_GATEWAY="$gateway_address"
        : > "$EVENT_LOG"

        start_native_llama
        wait "$(cat "$LLAMA_SERVER_PID_FILE")" 2>/dev/null || true

        mapfile -t events < "$EVENT_LOG"
        if [[ "$expected_route" == "direct" ]]; then
            [[ "${#events[@]}" -eq 2 ]] \
                || fail "$label: expected bridge bootout then llama exec, got ${#events[@]} events"
            [[ "${events[0]}" == "launchctl <bootout> <gui/501/com.ods.llm-bridge>" ]] \
                || fail "$label: bridge bootout was not the first event: ${events[0]}"
            [[ "${events[1]}" == exec\ \<--host\>\ \<"$bind_address"\>* ]] \
                || fail "$label: native llama did not receive bind $bind_address: ${events[1]}"
        else
            [[ "${#events[@]}" -eq 4 ]] \
                || fail "$label: expected bridge bootout/bootstrap/kickstart then llama exec, got ${#events[@]} events"
            [[ "${events[0]}" == "launchctl <bootout> <gui/501/com.ods.llm-bridge>" ]] \
                || fail "$label: stale bridge was not unloaded first: ${events[0]}"
            [[ "${events[1]}" == launchctl\ \<bootstrap\>* ]] \
                || fail "$label: loopback bridge was not bootstrapped: ${events[1]}"
            [[ "${events[2]}" == launchctl\ \<kickstart\>* ]] \
                || fail "$label: loopback bridge was not kickstarted: ${events[2]}"
            [[ "${events[3]}" == exec\ \<--host\>\ \<"$bind_address"\>* ]] \
                || fail "$label: native llama did not receive bind $bind_address: ${events[3]}"
            [[ "$LAST_BRIDGE_ENABLED" == "true" ]] \
                || fail "$label: restored bridge state was not persisted"
        fi
        pass "$label"
    }

    run_start_case "0.0.0.0" "192.168.106.1" direct "IPv4 wildcard boots out bridge before native llama"
    run_start_case "::" "192.168.106.1" direct "IPv6 wildcard boots out bridge before native llama"
    run_start_case "192.168.106.1" "192.168.106.1" direct "gateway bind boots out bridge before native llama"
    run_start_case "127.0.0.1" "192.168.106.1" bridge "returning to loopback recreates bridge before native llama"
)

echo "[OK] macOS direct-bind bridge contract holds"
