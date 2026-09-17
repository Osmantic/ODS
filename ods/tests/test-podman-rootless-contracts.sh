#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTRY_LIB="$ROOT_DIR/installers/lib/podman-registries.sh"
TMP_DIR="$(mktemp -d)"
cleanup() {
    if [[ -n "${agent_home:-}" && -f "$agent_home/data/ods-host-agent.pid" ]]; then
        kill "$(cat "$agent_home/data/ods-host-agent.pid")" 2>/dev/null || true
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

pass_count=0
fail() {
    echo "FAIL: $*" >&2
    exit 1
}
pass() {
    pass_count=$((pass_count + 1))
    echo "PASS: $*"
}

[[ -s "$REGISTRY_LIB" ]] || fail "Podman registry helper is missing"
bash -n "$REGISTRY_LIB"
pass "Podman registry helper parses"

ai_ok() { :; }
ai_warn() { :; }
log() { :; }
# shellcheck source=../installers/lib/podman-registries.sh
source "$REGISTRY_LIB"

export HOME="$TMP_DIR/home"
export XDG_CONFIG_HOME="$HOME/.config"
mkdir -p "$HOME"

fresh="$TMP_DIR/fresh.conf"
CONTAINERS_REGISTRIES_CONF="$fresh" ods_podman_ensure_dockerhub_search
grep -qx 'unqualified-search-registries = \["docker.io"\]' "$fresh" \
    || fail "fresh Podman config does not select Docker Hub"
pass "fresh Podman config is created without sudo"

default_home="$TMP_DIR/default-home"
export HOME="$default_home"
export XDG_CONFIG_HOME="$HOME/.config"
mkdir -p "$HOME"
# Invoked indirectly by ods_podman_effective_search_registries.
# shellcheck disable=SC2329
podman() {
    [[ "$*" == *"Registries.Search"* ]] || return 1
    printf '["registry.fedoraproject.org","quay.io"]\n'
}
ods_podman_ensure_dockerhub_search
default_dropin="$XDG_CONFIG_HOME/containers/registries.conf.d/99-ods-dockerhub.conf"
grep -qx 'unqualified-search-registries = \["registry.fedoraproject.org", "quay.io", "docker.io"\]' "$default_dropin" \
    || fail "default Podman drop-in did not preserve the effective search order"
[[ ! -e "$XDG_CONFIG_HOME/containers/registries.conf" ]] \
    || fail "default repair shadowed the system main registries.conf"
unset -f podman
export HOME="$TMP_DIR/home"
export XDG_CONFIG_HOME="$HOME/.config"
pass "default Podman repair uses a user drop-in and preserves effective registries"

existing="$TMP_DIR/existing.conf"
cat > "$existing" <<'EOF'
unqualified-search-registries = ["registry.fedoraproject.org"]

[[registry]]
prefix = "internal.example"
location = "mirror.internal.example"
blocked = true
EOF
CONTAINERS_REGISTRIES_CONF="$existing" ods_podman_ensure_dockerhub_search
python3 - "$existing" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
assignment = 'unqualified-search-registries = ["registry.fedoraproject.org", "docker.io"]'
assert assignment in text
assert text.index(assignment) < text.index("[[registry]]")
assert 'location = "mirror.internal.example"' in text
assert "blocked = true" in text
PY
pass "existing registry search order and tables are preserved"

before="$(sha256sum "$existing" | awk '{print $1}')"
CONTAINERS_REGISTRIES_CONF="$existing" ods_podman_ensure_dockerhub_search
after="$(sha256sum "$existing" | awk '{print $1}')"
[[ "$before" == "$after" ]] || fail "Podman registry repair is not idempotent"
pass "Podman registry repair is idempotent"

custom="$TMP_DIR/custom/registries.conf"
CONTAINERS_REGISTRIES_CONF="$custom" ods_podman_ensure_dockerhub_search
[[ -f "$custom" ]] || fail "CONTAINERS_REGISTRIES_CONF override was ignored"
[[ ! -e "$XDG_CONFIG_HOME/containers/registries.conf" ]] \
    || fail "override unexpectedly wrote the default user config"
pass "official Podman config override is respected"

symlink_target="$TMP_DIR/symlink-target.conf"
symlink_path="$TMP_DIR/symlink.conf"
printf 'unqualified-search-registries = ["quay.io"]\n' > "$symlink_target"
ln -s "$symlink_target" "$symlink_path"
CONTAINERS_REGISTRIES_CONF="$symlink_path" ods_podman_ensure_dockerhub_search
[[ -L "$symlink_path" ]] || fail "atomic update replaced a config symlink"
grep -q '"docker.io"' "$symlink_target" || fail "symlink target was not updated"
pass "atomic update preserves config symlinks"

invalid="$TMP_DIR/invalid.conf"
printf 'unqualified-search-registries = ["quay.io"\n' > "$invalid"
invalid_before="$(sha256sum "$invalid" | awk '{print $1}')"
if CONTAINERS_REGISTRIES_CONF="$invalid" ods_podman_ensure_dockerhub_search 2>/dev/null; then
    fail "malformed registry config was accepted"
fi
invalid_after="$(sha256sum "$invalid" | awk '{print $1}')"
[[ "$invalid_before" == "$invalid_after" ]] || fail "malformed config was partially rewritten"
pass "malformed registry config fails without mutation"

commented="$TMP_DIR/commented.conf"
cat > "$commented" <<'EOF'
unqualified-search-registries = [
  "registry.example/#team", # keep this registry first
  "quay.io",                # existing fallback
]
EOF
CONTAINERS_REGISTRIES_CONF="$commented" ods_podman_ensure_dockerhub_search
grep -qx 'unqualified-search-registries = \["registry.example/#team", "quay.io", "docker.io"\]' "$commented" \
    || fail "TOML comments or a quoted hash corrupted the registry list"
pass "Podman registry parser handles multiline TOML comments and quoted hashes"

if grep -Eq 'list\[|\|[[:space:]]*None' "$REGISTRY_LIB"; then
    fail "Podman helper requires a newer Python type-hint syntax"
fi
pass "Podman helper remains compatible with installer Python baselines"

# The Python invocation forwards fallback sources as argv. An unguarded
# array expansion aborts under `set -u` with "unbound variable" when no
# registry config exists on disk (fresh minimal hosts) on Bash < 4.4,
# killing phase 05 before Podman is usable.
grep -Fq '${source_confs[@]+"${source_confs[@]}"}' "$REGISTRY_LIB" \
    || fail "source_confs expansion must use the set -u safe guarded form"
pass "Podman helper guards empty source_confs under set -u (Bash < 4.4)"

# A Linux host may run systemd while ODS has no system unit because sudo was
# unavailable during installation. The CLI must use the session fallback and
# must not call sudo merely because systemd exists on the host.
agent_home="$TMP_DIR/agent-home"
fake_bin="$TMP_DIR/bin"
mkdir -p "$agent_home/bin" "$agent_home/data" "$fake_bin"
: > "$agent_home/docker-compose.base.yml"
real_python="$(command -v python3)"
agent_port="$($real_python - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
printf 'ODS_AGENT_BIND=127.0.0.1\nODS_AGENT_PORT=%s\n' "$agent_port" > "$agent_home/.env"
cat > "$agent_home/bin/ods-host-agent.py" <<'PY'
import argparse
import http.server
import os

parser = argparse.ArgumentParser()
parser.add_argument("--pid-file", required=True)
args = parser.parse_args()
with open(args.pid_file, "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok","version":"test"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass

http.server.HTTPServer(("127.0.0.1", int(os.environ["ODS_AGENT_PORT"])), Handler).serve_forever()
PY
cat > "$fake_bin/systemctl" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == "status" ]] && exit 0
[[ "${1:-}" == "cat" ]] && exit 1
exit 1
EOF
cat > "$fake_bin/sudo" <<EOF
#!/usr/bin/env bash
echo called >> "$TMP_DIR/sudo-calls"
exit 1
EOF
cat > "$fake_bin/python3" <<EOF
#!/usr/bin/env bash
exec "$real_python" "\$@"
EOF
chmod +x "$fake_bin/systemctl" "$fake_bin/sudo" "$fake_bin/python3"

agent_output="$TMP_DIR/agent-output"
if ! ODS_HOME="$agent_home" ODS_AGENT_BIND="127.0.0.1" ODS_AGENT_PORT="$agent_port" PATH="$fake_bin:$PATH" \
    ODS_AGENT_FORCE_SESSION=true bash "$ROOT_DIR/ods-cli" agent start > "$agent_output" 2>&1; then
    cat "$agent_output" >&2
    cat "$agent_home/data/ods-host-agent.log" >&2 2>/dev/null || true
    fail "CLI session host-agent fallback failed to start"
fi
grep -q 'Agent started (background' "$agent_output" \
    || fail "CLI did not select the background host-agent fallback"
[[ ! -s "$TMP_DIR/sudo-calls" ]] || fail "CLI called sudo without an installed system unit"
[[ -s "$agent_home/data/ods-host-agent.pid" ]] || fail "background host agent was not launched"
curl -fsS "http://127.0.0.1:$agent_port/health" >/dev/null \
    || fail "CLI reported success before the host agent became healthy"
pass "systemd host without an ODS unit uses a health-proven session fallback"

# An old system unit may remain after a previous privileged install. The
# installer must still be able to force the no-sudo session lifecycle without
# invoking that unit.
cat > "$fake_bin/systemctl" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == "status" ]] && exit 0
[[ "${1:-}" == "cat" ]] && exit 0
exit 1
EOF
: > "$TMP_DIR/sudo-calls"
if ! ODS_HOME="$agent_home" ODS_AGENT_FORCE_SESSION=true \
    ODS_AGENT_BIND="127.0.0.1" ODS_AGENT_PORT="$agent_port" PATH="$fake_bin:$PATH" \
    bash "$ROOT_DIR/ods-cli" agent restart > "$agent_output" 2>&1; then
    cat "$agent_output" >&2
    fail "forced session host-agent restart failed with an old system unit present"
fi
grep -q 'Agent started (background' "$agent_output" \
    || fail "forced session restart selected the stale system unit"
[[ ! -s "$TMP_DIR/sudo-calls" ]] \
    || fail "forced session restart invoked sudo through an old system unit"
pass "installer can force a session host-agent lifecycle when an old unit remains"

ODS_HOME="$agent_home" ODS_AGENT_BIND="127.0.0.1" ODS_AGENT_PORT="$agent_port" PATH="$fake_bin:$PATH" \
    ODS_AGENT_FORCE_SESSION=true bash "$ROOT_DIR/ods-cli" agent stop >/dev/null 2>&1
for _ in {1..20}; do
    if ! curl -sf --max-time 1 "http://127.0.0.1:${agent_port}/health" >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
done
if curl -sf --max-time 1 "http://127.0.0.1:${agent_port}/health" >/dev/null 2>&1; then
    fail "session host agent remained healthy after stop"
fi

sleep 30 &
unowned_pid=$!
printf '%s\n' "$unowned_pid" > "$agent_home/data/ods-host-agent.pid"
if ! ODS_HOME="$agent_home" ODS_AGENT_BIND="127.0.0.1" ODS_AGENT_PORT="$agent_port" PATH="$fake_bin:$PATH" \
    ODS_AGENT_FORCE_SESSION=true bash "$ROOT_DIR/ods-cli" agent start > "$agent_output" 2>&1; then
    cat "$agent_output" >&2
    fail "CLI did not recover from an unowned stale PID"
fi
kill -0 "$unowned_pid" 2>/dev/null \
    || fail "CLI killed an unowned process referenced by a stale PID file"
grep -q 'not owned by ODS' "$agent_output" \
    || fail "CLI did not diagnose the unowned stale PID"
kill "$unowned_pid" 2>/dev/null || true
pass "session fallback refuses to stop an unowned stale PID"

python3 - "$ROOT_DIR/installers/phases/01-preflight.sh" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index("if ! command -v jq")
end = text.index('log "jq:', start)
block = text[start:end]
assert "ods_sudo_available" in block
assert "sudo -n true" not in block
assert "ods_sudo dnf install" in block
assert "ods_sudo apt-get install" in block
assert " dnf install -y jq" not in block.replace("ods_sudo dnf install -y jq", "")
PY
pass "jq bootstrap uses the shared privilege contract"

grep -q '_ods_start_session_host_agent' "$ROOT_DIR/installers/phases/07-devtools.sh" \
    || fail "phase 07 does not start a rootless session host agent"
grep -q 'ods_python_pip_install_user' "$ROOT_DIR/installers/phases/07-devtools.sh" \
    || fail "rootless host-agent downloader dependencies are not installed as the user"
pass "rootless installer keeps host-agent model downloads operational"

grep -q 'No container runtime is installed and privileged package installation is unavailable' \
    "$ROOT_DIR/installers/phases/05-docker.sh" \
    || fail "runtime bootstrap does not fail clearly when privilege is required"
pass "required container-runtime installation is privilege-gated"

python3 - "$ROOT_DIR/installers/phases/05-docker.sh" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index('if [[ "$_docker_ver" == 29.3.* ]]')
end = text.index("# Decide whether to use sudo", start)
block = text[start:end]
gate = block.index("if ! ods_sudo_available")
apt_mutation = block.index("ods_sudo apt-get install")
dnf_mutation = block.index("ods_sudo dnf downgrade")
success = block.index('ai_ok "Docker downgraded')
assert gate < apt_mutation
assert gate < dnf_mutation
assert gate < success
assert "if ods_sudo_available" in block
PY
pass "AMD Docker downgrade cannot report a skipped privileged mutation as success"

if grep -q '! sudo -n true' "$ROOT_DIR/installers/phases/05-docker.sh"; then
    fail "runtime bootstrap still calls sudo directly after the shared privilege gate"
fi
pass "root installation without a sudo binary passes the shared privilege gate"

python3 - "$ROOT_DIR/installers/phases/05-docker.sh" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index('ai "Installing NVIDIA Container Toolkit..."')
gate = text.index("ods_sudo_available", start)
mutation = text.index("nvidia.github.io/libnvidia-container/gpgkey", start)
assert gate < mutation
PY
pass "NVIDIA toolkit bootstrap cannot mutate the host without privilege"

python3 - "$ROOT_DIR/installers/lib/detection.sh" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index("fix_nvidia_secure_boot()")
gate = text.index("ods_sudo_available", start)
mutation = text.index("ubuntu-drivers install", start)
assert gate < mutation
PY
pass "NVIDIA repair cannot report privileged mutations as successful without sudo"

python3 - "$ROOT_DIR/installers/phases/02-detection.sh" <<'PY'
import pathlib
import re
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index('ai "Attempting to install a compatible driver..."')
end = text.index("# Intel Arc validation", start)
block = text[start:end]
assert "ods_sudo_available" in block
assert "ods_sudo ubuntu-drivers install" in block
assert "ods_sudo apt-get install" in block
assert not re.search(r"(?m)^\s*sudo\s+", block)
PY
pass "outdated NVIDIA driver repair follows the shared privilege contract"

python3 - "$ROOT_DIR/installers/phases/10-amd-tuning.sh" <<'PY'
import pathlib
import re
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
assert "_phase10_privileged()" in text
assert "ods_sudo_available || return 1" in text
assert "ods_sudo \"$@\"" in text
assert not re.search(r"(?m)^\s*sudo(?:\s+-n)?\s+", text)
assert "mktemp \"${TMPDIR:-/tmp}/ods-gtt-tuning.XXXXXX\"" in text
PY
pass "AMD tuning honors no-sudo mode and uses a secure temporary config"

python3 - "$ROOT_DIR/installers/phases/06-directories.sh" <<'PY'
import pathlib
import re
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
assert "_phase06_repair_host_path" in text
assert "Hermes requires data/hermes ownership $_phase06_compose_uid:$_phase06_compose_gid and mode 700" in text
assert 'ods_sudo chown -R "$_phase06_compose_uid:$_phase06_compose_gid"' in text
assert not re.search(r"(?m)^\s*sudo\s+(?:chown|chmod)\s+", text)
PY
pass "rootful ownership repair uses persisted IDs and fails clearly without sudo"

if grep -q 'Ignoring placeholder .* from environment' \
    "$ROOT_DIR/installers/phases/06-directories.sh"; then
    fail "reinstall still deletes pre-existing short cloud API keys"
fi
pass "reinstall preserves existing cloud credential values"

echo "All $pass_count Podman/rootless contract tests passed."
