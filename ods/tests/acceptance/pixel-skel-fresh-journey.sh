#!/usr/bin/env bash
# Disposable exact-head install proof for the Pixel broker account home.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
expected=dfdc5fd56715339867e2010cba15adbdeaef3a40
audit_root="${RUNNER_TEMP:?runner temp is required}/ods-pixel-skel-acceptance"
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
    exit 1
}

show_install_diagnostics() {
    python3 - "$LOG_FILE" "$key_file" "$INSTALL_DIR/.env" <<'PY' >&2
from pathlib import Path
import re
import sys

log, key_file, env_file = map(Path, sys.argv[1:])
if not log.exists():
    raise SystemExit(0)
secrets = []
if key_file.exists():
    secrets.append(key_file.read_text(encoding="ascii").strip())
if env_file.exists():
    for item in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^(?:export )?([A-Z0-9_]+)=(.*)$", item)
        if match and any(word in match[1] for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            secrets.append(match[2].strip("\"'"))
print("Sanitized installer log tail:")
for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]:
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

[[ "${GITHUB_ACTIONS:-}" == true ]] || fail 'refusing non-disposable host'
[[ "$RUNNER_TEMP" == /* && "$INSTALL_DIR" == "$RUNNER_TEMP"/* ]] || fail 'install path escaped runner temp'
[[ "$(git -C "$(dirname "$product")" rev-parse HEAD)" == "$expected" ]] || fail 'wrong product head'
[[ "$(cat /proc/1/comm)" == systemd ]] || fail 'runner is not a systemd host'
docker info >/dev/null || fail 'Docker Engine unavailable'
[[ ! -e "$INSTALL_DIR" ]] || fail 'install path is not fresh'
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

if ! (umask 022; cd "$product" && timeout 2400s bash install-core.sh \
    --non-interactive --skip-docker --no-bootstrap \
    --external-llm-url "http://127.0.0.1:$mock_port" \
    --external-llm-provider openai-compatible \
    --external-llm-model ods-acceptance-mock \
    --external-llm-key-file "$key_file") >>"$LOG_FILE" 2>&1; then
    show_install_diagnostics
    fail 'fresh exact-head install did not finish'
fi

sudo systemctl is-active --quiet pixel-ops-broker.service \
    || fail 'Pixel Operations Broker is not active'
sudo test -f /var/lib/pixel-ops-broker/inventory.json \
    || fail 'Pixel Operations Broker inventory is missing'
sudo python3 - <<'PY'
import os
from pathlib import Path
import pwd
import stat

root = Path('/var/lib/pixel-ops-broker')
info = root.lstat()
broker = pwd.getpwnam('pixel-ops-broker')
assert stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (broker.pw_uid, broker.pw_gid, 0o750)
assert broker.pw_dir == str(root)
skel_names = {child.name for child in Path('/etc/skel').iterdir()} - {'.ssh'}
assert skel_names, 'runner skeleton has no distinguishing entries'
copied = skel_names & {child.name for child in root.iterdir()}
assert not copied, f'broker home contains host skeleton entries: {sorted(copied)}'
for current, directories, files in os.walk(root, topdown=True, followlinks=False):
    for name in (*directories, *files):
        entry = Path(current) / name
        entry_info = entry.lstat()
        assert not stat.S_ISLNK(entry_info.st_mode), f'broker state contains symlink: {entry}'
        assert not entry_info.st_mode & 0o007, f'broker state has world access: {entry}'
print(f'PASS: fresh Pixel broker home has private state and no copied /etc/skel entries ({len(skel_names)} skel names checked)')
PY
