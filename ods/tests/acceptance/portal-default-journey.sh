#!/usr/bin/env bash
# Disposable runner proof for the exact current #6970 stack.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
expected=67dc0bd1613bc2c4fc337fb1cc8608f9859b1196
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

capture_probe() {
    local label="$1"
    shift
    if "$@" >"$audit_root/$label.json" 2>"$audit_root/$label.err"; then
        printf '0\n' >"$audit_root/$label.rc"
    else
        printf '%s\n' "$?" >"$audit_root/$label.rc"
    fi
}

collect_pixel_probes() {
    [[ -f /opt/pixel-ops-broker/ods-extension-search.py ]] || return 0
    capture_probe extension-search sudo -u pixel-ops-broker /usr/bin/python3 \
        /opt/pixel-ops-broker/ods-extension-search.py \
        /opt/pixel-ops-broker/ods-extension-catalog.json all
    local probe_id
    probe_id="$(python3 - "$audit_root/extension-search.json" <<'PY'
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
    print(value["matches"][0]["id"])
except (OSError, ValueError, KeyError, IndexError, TypeError):
    pass
PY
    )"
    if [[ "$probe_id" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]]; then
        capture_probe extension-manager sudo -u pixel-ops-broker /usr/bin/python3 \
            /opt/pixel-ops-broker/ods-extension-manager.py client \
            /run/ods-pixel-manager/extension-manager.sock inspect "$probe_id"
        capture_probe dashboard-extension python3 "$harness/portal-diagnostics.py" \
            "$INSTALL_DIR/.env" "$probe_id"
    fi
    capture_probe artifact-promoter /usr/bin/python3 \
        /usr/local/libexec/ods-pixel-artifact-promoter.py health \
        /run/ods-pixel-artifact-promoter/promoter.sock
    capture_probe workspace-preview /usr/bin/python3 \
        /usr/local/libexec/ods-pixel-workspace-preview.py health \
        /run/ods-pixel-preview/control.sock
}

show_install_diagnostics() {
    if [[ -e "$INSTALL_DIR/logs/pixel-install.log" ]]; then
        collect_pixel_probes
        docker logs --tail 100 ods-dashboard-api >"$audit_root/dashboard-api.log" 2>&1 || true
        docker logs --tail 80 ods-pixel-model-relay >"$audit_root/pixel-model-relay.log" 2>&1 || true
        docker logs --tail 80 ods-litellm >"$audit_root/litellm.log" 2>&1 || true
        sudo journalctl -u pixel-ingress.service -u openclaw-gateway.service \
            -u pixel-extension-manager.service -u pixel-artifact-promoter.service \
            -u pixel-workspace-preview.service -n 90 --no-pager -o short-iso \
            >"$audit_root/pixel-journal.log" 2>&1 || true
        for unit in pixel-ingress.service openclaw-gateway.service \
            pixel-extension-manager.service pixel-artifact-promoter.service \
            pixel-workspace-preview.service; do
            printf '%s: ' "$unit" >&2
            systemctl show "$unit" -p ActiveState -p SubState -p Result \
                -p ExecMainStatus --no-pager | tr '\n' ' ' >&2 || true
            printf '\n' >&2
        done
    fi
    python3 - "$LOG_FILE" "$key_file" "$INSTALL_DIR/.env" \
        "$INSTALL_DIR/logs/pixel-install.log" "$audit_root/pixel-journal.log" \
        "$audit_root" "$audit_root/dashboard-api.log" \
        "$audit_root/pixel-model-relay.log" "$audit_root/litellm.log" "$mock_log" <<'PY' >&2
from pathlib import Path
import json
import re
import sys

log_path, key_path, env_path, pixel_path, journal_path, audit_path, api_path, relay_path, litellm_path, mock_path = map(Path, sys.argv[1:])
secrets = []
if key_path.exists():
    secrets.append(key_path.read_text(encoding="utf-8").strip())
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^(?:export )?([A-Z0-9_]+)=(.*)$", line)
        if match and any(word in match[1] for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            secrets.append(match[2].strip("\"'"))
for label, path, limit in (("installer", log_path, 55), ("Pixel", pixel_path, 70),
                           ("systemd", journal_path, 70), ("Dashboard API", api_path, 90),
                           ("model relay", relay_path, 45), ("LiteLLM", litellm_path, 45),
                           ("mock upstream", mock_path, 20)):
    if not path.exists():
        continue
    print(f"Sanitized {label} log tail:")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        for secret in secrets:
            if secret:
                line = line.replace(secret, "<redacted>")
        line = re.sub(r"(?i)Bearer\s+\S+", "Bearer <redacted>", line)
        line = re.sub(r"(?i)([?&](?:key|token|secret|password)=)[^&\s]+", r"\1<redacted>", line)
        line = re.sub(r"\b(?:sk-|mock-)[A-Za-z0-9_-]{12,}\b", "<redacted>", line)
        line = re.sub(r"\b[A-Za-z0-9_/-]{40,}\b", "<redacted>", line)
        print(line[:500])
for label in ("extension-search", "extension-manager", "dashboard-extension",
              "artifact-promoter", "workspace-preview"):
    status = audit_path / f"{label}.rc"
    if not status.exists():
        continue
    print(f"{label} read-only probe exit={status.read_text(encoding='ascii').strip()}")
    output = audit_path / f"{label}.json"
    if output.exists():
        try:
            value = json.loads(output.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                if label == "dashboard-extension":
                    print(json.dumps(value)[:1200])
                    continue
                fields = ("schemaVersion", "kind", "query", "action", "extensionId", "outcome",
                          "status", "changed", "externalEffectOccurred", "boundary")
                print(json.dumps({field: value[field] for field in fields if field in value})[:700])
        except (OSError, ValueError):
            print("probe returned non-JSON output")
    error = audit_path / f"{label}.err"
    if error.exists():
        for line in error.read_text(encoding="utf-8", errors="replace").splitlines()[-4:]:
            for secret in secrets:
                if secret:
                    line = line.replace(secret, "<redacted>")
            line = re.sub(r"\b[A-Za-z0-9_/-]{40,}\b", "<redacted>", line)
            print("probe stderr: " + line[:400])
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
    if ! (umask 022; cd "$product" && timeout 2400s bash install-core.sh \
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
if ! check_api chat; then
    show_install_diagnostics
    fail 'Portal chat did not complete through the mock upstream'
fi
grep -q '"POST /v1/chat/completions HTTP/1.1" 200' "$mock_log" \
    || fail 'mock upstream did not receive the installed Portal chat'

# The API retains a completed response independently of the browser. This is
# not a claim that the browser's conversation-history UI has been exercised.
docker restart ods-dashboard-api >/dev/null || fail 'Dashboard API restart failed'
wait_portal
check_api result || fail 'Portal completion receipt did not survive API restart'

# Exercise the actual Dashboard UI through its local nginx listener. Nginx
# supplies the API credential; the browser never receives the private key.
curl -fsS --max-time 15 -X POST http://127.0.0.1:3001/api/setup/complete >/dev/null \
    || fail 'fresh-run setup could not be completed for browser acceptance'
(cd "$harness/portal-browser" && npm ci --no-audit --no-fund \
    && npx playwright install --with-deps chromium \
    && ODS_PORTAL_BROWSER_URL=http://127.0.0.1:3001 node check.mjs) \
    || fail 'installed Portal browser chat or reload history failed'

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
if ! check_api expect-add-failure; then
    show_install_diagnostics
    fail 'host agent did not report failed WebUI startup'
fi
cmp -s "$audit_root/env-before" "$INSTALL_DIR/.env" || fail 'failed add-back changed the installed selection'
check_api selection-off || fail 'failed add-back left WebUI selected'
cmp -s "$audit_root/base-before.yml" "$INSTALL_DIR/docker-compose.base.yml" \
    && fail 'failure injection did not modify only the installed Compose file'
cp "$audit_root/base-before.yml" "$INSTALL_DIR/docker-compose.base.yml"
cmp -s "$audit_root/base-before.yml" "$INSTALL_DIR/docker-compose.base.yml" \
    || fail 'failure injection Compose file was not restored'
[[ "$(sha256sum "$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt" | cut -d' ' -f1)" == "$sentinel_hash" ]] \
    || fail 'failed add-back changed retained WebUI data'
printf 'PASS: controlled WebUI startup failure restored the prior selection and data\n'

check_api add-webui || fail 'Library add-back failed after restoring the image'
check_api selection-on || fail 'Library add-back was not retained'
[[ "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' ods-webui)" == unless-stopped ]] \
    || fail 'Library-added WebUI lacks restart persistence'
curl -fLsS --max-time 30 http://127.0.0.1:3000/ >/dev/null \
    || fail 'WebUI was not reachable after Library add-back'
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
[[ "$(sha256sum "$INSTALL_DIR/data/open-webui/acceptance-sentinel.txt" | cut -d' ' -f1)" == "$sentinel_hash" ]] \
    || fail 'Library add-back changed retained WebUI data'
docker ps -a --format '{{.Names}}' | grep -Eq '^ods-llama-server$' \
    && fail 'WebUI add-back started llama-server on an external route'
printf 'PASS: Library add-back started WebUI, retained its data, and kept llama-server absent\n'
