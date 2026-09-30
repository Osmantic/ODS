#!/usr/bin/env bash
# Disposable retained-WebUI upgrade proof for exact pre-lean and Portal heads.
set -euo pipefail

baseline="${ODS_ACCEPTANCE_BASELINE_ROOT:?baseline checkout is required}"
candidate="${ODS_ACCEPTANCE_CANDIDATE_ROOT:?candidate checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
audit_root="${RUNNER_TEMP:?runner temp is required}/ods-portal-upgrade"
export INSTALL_DIR="$audit_root/install"
export LOG_FILE="$audit_root/install.log"
key_file="$audit_root/mock.key"
mock_log="$audit_root/mock.log"
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

show_install_diagnostics() {
    python3 - "$LOG_FILE" "$key_file" "$INSTALL_DIR/.env" <<'PY' >&2
from pathlib import Path
import re
import sys

log_path, key_path, env_path = map(Path, sys.argv[1:])
secrets = []
if key_path.exists():
    secrets.append(key_path.read_text(encoding="utf-8").strip())
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^(?:export )?([A-Z0-9_]+)=(.*)$", line)
        if match and any(word in match[1] for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS")):
            secrets.append(match[2].strip("\"'"))
print("Sanitized installer log tail:")
if log_path.exists():
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-85:]:
        for secret in secrets:
            if secret:
                line = line.replace(secret, "<redacted>")
        line = re.sub(r"(?i)Bearer\s+\S+", "Bearer <redacted>", line)
        line = re.sub(r"(?i)([?&](?:key|token|secret|password)=)[^&\s]+", r"\1<redacted>", line)
        line = re.sub(r"\b(?:sk-|mock-)[A-Za-z0-9_-]{12,}\b", "<redacted>", line)
        line = re.sub(r"\b[A-Za-z0-9_/-]{40,}\b", "<redacted>", line)
        print(line[:500])
PY
}

run_installer() {
    local source="$1" label="$2"
    printf 'Installing %s at %s\n' "$label" "$(git -C "$(dirname "$source")" rev-parse --short=12 HEAD)"
    if ! (umask 022; cd "$source" && timeout 2400s bash install-core.sh \
        --non-interactive --skip-docker --no-bootstrap \
        --external-llm-url "http://127.0.0.1:$mock_port" \
        --external-llm-provider openai-compatible \
        --external-llm-model ods-acceptance-mock \
        --external-llm-key-file "$key_file") >>"$LOG_FILE" 2>&1; then
        show_install_diagnostics
        fail "$label installer did not finish; logs retained on disposable runner"
    fi
}

[[ "${GITHUB_ACTIONS:-}" == true ]] || fail 'refusing non-disposable host'
[[ "$RUNNER_TEMP" == /* && "$INSTALL_DIR" == "$RUNNER_TEMP"/* ]] || fail 'install path is outside runner temp'
[[ "$(git -C "$(dirname "$baseline")" rev-parse HEAD)" == 9b95d08ae9e9f79af8410dfcea442df6b99db0ba ]] || fail 'baseline source changed'
[[ "$(git -C "$(dirname "$candidate")" rev-parse HEAD)" == c5bf679be9fa0da794d2307bc264cdcb0727e25d ]] || fail 'candidate source changed'
[[ "$(cat /proc/1/comm)" == systemd ]] || fail 'runner is not a Pixel-qualified systemd host'
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
    --port "$mock_port" >"$mock_log" 2>&1 &
mock_pid=$!
for attempt in {1..30}; do
    curl -fsS --max-time 2 "http://127.0.0.1:$mock_port/healthz" >/dev/null 2>&1 && break
    sleep 1
done
curl -fsS --max-time 2 "http://127.0.0.1:$mock_port/healthz" >/dev/null \
    || fail 'mock upstream did not start'

run_installer "$baseline" 'pre-lean main baseline'
grep -qx 'ENABLE_OPEN_WEBUI=true' "$INSTALL_DIR/.env" \
    || fail 'baseline standard install did not select WebUI'
curl -fLsS --max-time 30 http://127.0.0.1:3000/ >/dev/null \
    || fail 'baseline WebUI was not reachable'
mkdir -p "$INSTALL_DIR/data/open-webui"
printf 'retained-webui-data\n' >"$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt"
sentinel_hash="$(sha256sum "$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt" | cut -d' ' -f1)"
printf 'PASS: pre-lean main installed WebUI with retained-data sentinel\n'

# The retained Pixel runtime is part of this upgrade proof. Report only
# ownership/mode metadata for a state entry if the fail-closed source
# transition later rejects it; no file contents or private keys are logged.
if [[ -e /var/lib/pixel-ops-broker/.composer || -L /var/lib/pixel-ops-broker/.composer ]]; then
    sudo stat -c 'Prior Pixel state entry: %F mode=%a uid=%u gid=%g links=%h device=%d path=%n' \
        /var/lib/pixel-ops-broker /var/lib/pixel-ops-broker/.composer
    printf 'Prior Pixel .composer first-level entry classes:\n'
    sudo find /var/lib/pixel-ops-broker/.composer -mindepth 1 -maxdepth 1 \
        -printf '%y mode=%m uid=%U gid=%G\n' | sort | uniq -c
fi
printf 'Prior Pixel state top-level metadata:\n'
sudo find /var/lib/pixel-ops-broker -mindepth 1 -maxdepth 1 \
    -printf '%f type=%y mode=%m uid=%U gid=%G\n' | sort
sudo python3 "$harness/pixel-skel-inventory.py"

# Preserve a unique broker record to prove the transition never classifies
# copied skeleton files as disposable user data.
printf 'retained-broker-data\n' | sudo tee /var/lib/pixel-ops-broker/ods-acceptance-sentinel.txt >/dev/null
sudo chown pixel-ops-broker:pixel-ops /var/lib/pixel-ops-broker/ods-acceptance-sentinel.txt
sudo chmod 0600 /var/lib/pixel-ops-broker/ods-acceptance-sentinel.txt
broker_sentinel_hash="$(sudo sha256sum /var/lib/pixel-ops-broker/ods-acceptance-sentinel.txt | cut -d' ' -f1)"
broker_root_inode="$(sudo stat -c '%i' /var/lib/pixel-ops-broker)"

run_installer "$candidate" 'current-main Portal integration upgrade'
grep -qx 'ENABLE_OPEN_WEBUI=true' "$INSTALL_DIR/.env" \
    || fail 'Portal upgrade changed the existing WebUI selection'
[[ "$(sha256sum "$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt" | cut -d' ' -f1)" == "$sentinel_hash" ]] \
    || fail 'Portal upgrade changed retained WebUI data'
[[ "$(docker inspect --format '{{.State.Running}}' ods-webui)" == true ]] \
    || fail 'Portal upgrade stopped selected WebUI'
curl -fLsS --max-time 30 http://127.0.0.1:3000/ >/dev/null \
    || fail 'WebUI was not reachable after Portal upgrade'
docker ps -a --format '{{.Names}}' | grep -Eq '^ods-llama-server$' \
    && fail 'external-route upgrade started llama-server'
systemctl is-active --quiet pixel-ops-broker.service \
    || fail 'new Pixel Operations Broker is not active after upgrade'
sudo python3 - "$broker_root_inode" "$broker_sentinel_hash" <<'PY'
import hashlib
import pathlib
import stat
import sys

old_inode = int(sys.argv[1])
old_hash = sys.argv[2]
root = pathlib.Path('/var/lib')
holders = list(root.glob('.pixel-ops-broker-custody-*/state'))
assert len(holders) == 1, f'expected one retained broker home, got {len(holders)}'
saved = holders[0]
holder = saved.parent
assert holder.lstat().st_uid == 0
assert stat.S_IMODE(holder.lstat().st_mode) == 0o700
assert saved.stat().st_ino == old_inode
assert hashlib.sha256((saved / 'ods-acceptance-sentinel.txt').read_bytes()).hexdigest() == old_hash
assert (saved / '.composer').is_dir()
assert (saved / '.ghcup').is_symlink()
assert pathlib.Path('/var/lib/pixel-ops-broker').stat().st_ino != old_inode
print('PASS: old broker home and unique data retained in root-only custody; fresh broker active')
PY
printf 'PASS: upgrade retained WebUI choice, data, availability, and local Pixel without llama-server\n'
