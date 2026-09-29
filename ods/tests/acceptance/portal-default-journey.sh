#!/usr/bin/env bash
# Disposable runner proof for a pinned #6970 stack plus the #6964 GID fix.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
expected=54186765bfb2ab562003e6e135ea11b92aa9fd9c
audit_root="${RUNNER_TEMP:?runner temp is required}/ods-portal-acceptance"
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
        if match and any(word in match[1] for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            secrets.append(match[2].strip("\"'"))
print("Sanitized installer log tail:")
for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-90:]:
    for secret in secrets:
        if secret:
            line = line.replace(secret, "<redacted>")
    line = re.sub(r"(?i)Bearer\s+\S+", "Bearer <redacted>", line)
    line = re.sub(r"(?i)([?&](?:key|token|secret|password)=)[^&\s]+", r"\1<redacted>", line)
    line = re.sub(r"\b(?:sk-|mock-)[A-Za-z0-9_-]{12,}\b", "<redacted>", line)
    print(line[:500])
PY
}

compose_services() (
    cd "$INSTALL_DIR"
    [[ -s .compose-flags ]] || return 1
    local -a flags=()
    read -r -a flags < .compose-flags
    docker compose "${flags[@]}" config --services
)

check_api() {
    python3 "$harness/portal-requests.py" "$1" "$INSTALL_DIR/.env"
}

wait_portal() {
    for attempt in {1..60}; do
        if check_api status >/dev/null 2>&1; then
            check_api status
            return 0
        fi
        sleep 5
    done
    fail 'Portal was not ready after the installed startup window'
}

run_installer() {
    printf 'Installing exact product head %s\n' "$expected"
    if ! (cd "$product" && timeout 2400s bash install-core.sh \
        --non-interactive --skip-docker --no-bootstrap \
        --external-llm-url "http://127.0.0.1:$mock_port" \
        --external-llm-provider openai-compatible \
        --external-llm-model "$model" \
        --external-llm-key-file "$key_file") >>"$LOG_FILE" 2>&1; then
        show_install_diagnostics
        fail 'fresh standard installer did not finish'
    fi
}

[[ "${GITHUB_ACTIONS:-}" == true ]] || fail 'refusing non-disposable host'
[[ "$RUNNER_TEMP" == /* && "$INSTALL_DIR" == "$RUNNER_TEMP"/* ]] || fail 'install path is outside runner temp'
[[ "$(git -C "$product" rev-parse HEAD)" == "$expected" ]] || fail 'product checkout is not the pinned PR head'
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

docker image ls --format '{{.Repository}}' | sort -u >"$audit_root/images-before.txt"
run_installer

grep -qx 'ENABLE_OPEN_WEBUI=false' "$INSTALL_DIR/.env" \
    || fail 'fresh qualified standard install did not persist Portal-only choice'
services="$(compose_services)" || fail 'installed Compose selection cannot be resolved'
for required in dashboard dashboard-api litellm pixel-edge pixel-model-relay; do
    grep -qx "$required" <<<"$services" || fail "Portal stack omitted $required"
done
for forbidden in open-webui llama-server model-router perplexica whisper tts n8n qdrant searxng comfyui hermes token-spy; do
    if grep -qx "$forbidden" <<<"$services"; then
        fail "Portal stack unexpectedly selected $forbidden"
    fi
done
if docker ps -a --format '{{.Names}}' | grep -Eq '^ods-(webui|llama-server)$'; then
    fail 'WebUI or llama-server container exists in the fresh Portal selection'
fi
docker image ls --format '{{.Repository}}' | sort -u >"$audit_root/images-after.txt"
if comm -13 "$audit_root/images-before.txt" "$audit_root/images-after.txt" \
    | grep -Ei 'open-webui|llama.cpp|perplexica|whisper|kokoro|searxng|comfyui'; then
    fail 'fresh Portal install pulled an unselected optional image'
fi
printf 'PASS: fresh installed Portal stack excluded optional images and containers\n'

wait_portal
check_api selection-off || fail 'Library did not report WebUI as addable'
check_api chat || fail 'Portal chat did not complete through the mock upstream'

# The API retains a completed response independently of the browser. This is
# not a claim that the browser's conversation-history UI has been exercised.
docker restart ods-dashboard-api >/dev/null || fail 'Dashboard API restart failed'
wait_portal
check_api result || fail 'Portal completion receipt did not survive API restart'

mkdir -p "$INSTALL_DIR/data/open-webui"
printf 'retained-webui-data\n' >"$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt"
sentinel_hash="$(sha256sum "$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt" | cut -d' ' -f1)"
cp "$INSTALL_DIR/.env" "$audit_root/env-before"
cp "$INSTALL_DIR/docker-compose.base.yml" "$audit_root/base-before.yml"

# Make only WebUI's image unavailable to exercise the installed host-agent
# failure/rollback path. The product source and real user data are untouched.
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
check_api expect-add-failure || fail 'host agent did not report failed WebUI startup'
cmp -s "$audit_root/env-before" "$INSTALL_DIR/.env" || fail 'failed add-back changed the installed selection'
check_api selection-off || fail 'failed add-back left WebUI selected'
cmp -s "$audit_root/base-before.yml" "$INSTALL_DIR/docker-compose.base.yml" \
    && fail 'failure injection did not modify only the installed Compose file'
cp "$audit_root/base-before.yml" "$INSTALL_DIR/docker-compose.base.yml"
[[ "$(sha256sum "$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt" | cut -d' ' -f1)" == "$sentinel_hash" ]] \
    || fail 'failed add-back changed retained WebUI data'
printf 'PASS: controlled WebUI startup failure restored the prior selection and data\n'

check_api add-webui || fail 'Library add-back failed after restoring the image'
check_api selection-on || fail 'Library add-back was not retained'
compose_services | grep -qx open-webui || fail 'WebUI was not added to the active Compose stack'
curl -fLsS --max-time 30 http://127.0.0.1:3000/ >/dev/null \
    || fail 'WebUI was not reachable after Library add-back'
[[ "$(sha256sum "$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt" | cut -d' ' -f1)" == "$sentinel_hash" ]] \
    || fail 'Library add-back changed retained WebUI data'
docker ps -a --format '{{.Names}}' | grep -Eq '^ods-llama-server$' \
    && fail 'WebUI add-back started llama-server on an external route'
printf 'PASS: Library add-back started WebUI, retained its data, and kept llama-server absent\n'
