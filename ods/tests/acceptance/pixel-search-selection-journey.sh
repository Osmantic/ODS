#!/usr/bin/env bash
# Disposable installed service-selection proof for exact draft #6956.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
expected=c00633804b601e30b1986edea4ff0bbe91e5e7dd
audit_root="${RUNNER_TEMP:?runner temp is required}/ods-pixel-search-acceptance"
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
    if [[ -f "$mock_log" ]]; then
        printf 'Mock upstream search diagnostic tail:\n' >&2
        grep -E 'search_probe_|search_tool_result_seen|accept=chat|reject=' "$mock_log" \
            | tail -n 35 >&2 || true
    fi
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
    secrets.append(key_path.read_text(encoding='utf-8').strip())
if env_path.exists():
    for line in env_path.read_text(encoding='utf-8', errors='replace').splitlines():
        match = re.match(r'^(?:export )?([A-Z0-9_]+)=(.*)$', line)
        if match and any(word in match[1] for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')):
            secrets.append(match[2].strip("\"'"))
if log_path.exists():
    print('Sanitized installer log tail:')
    for line in log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-85:]:
        for secret in secrets:
            if secret:
                line = line.replace(secret, '<redacted>')
        line = re.sub(r'(?i)Bearer\s+\S+', 'Bearer <redacted>', line)
        line = re.sub(r'\b(?:sk-|mock-)[A-Za-z0-9_-]{12,}\b', '<redacted>', line)
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

run_installer() {
    local label="$1" provider="$2" search_port="${3:-8888}"
    printf 'Installing %s with Pixel search provider %s on port %s\n' "$label" "$provider" "$search_port"
    if ! (umask 022; cd "$product" && PIXEL_WEB_SEARCH_PROVIDER="$provider" SEARXNG_PORT="$search_port" timeout 2400s bash install-core.sh \
        --non-interactive --skip-docker --no-bootstrap --no-recommended --no-hermes \
        --external-llm-url "http://127.0.0.1:$mock_port" \
        --external-llm-provider openai-compatible \
        --external-llm-model ods-acceptance-mock \
        --external-llm-key-file "$key_file") >>"$LOG_FILE" 2>&1; then
        show_install_diagnostics
        fail "$label installer did not finish"
    fi
}

[[ "${GITHUB_ACTIONS:-}" == true ]] || fail 'refusing non-disposable host'
[[ "$RUNNER_TEMP" == /* && "$INSTALL_DIR" == "$RUNNER_TEMP"/* ]] || fail 'install path is outside runner temp'
[[ "$(git -C "$product" rev-parse HEAD)" == "$expected" ]] || fail 'candidate source changed'
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
with open(sys.argv[1], 'w', encoding='ascii') as stream:
    stream.write('mock-' + secrets.token_hex(24))
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
run_installer 'fresh Pixel' parallel-free
systemctl is-active --quiet pixel-ops-broker.service || fail 'Pixel broker is not active'
grep -qx 'PIXEL_WEB_SEARCH_PROVIDER=parallel-free' "$INSTALL_DIR/.env" \
    || fail 'parallel-free selection was not persisted'
python3 - "$HOME/.openclaw/openclaw.json" <<'PY' \
    || fail 'installed Pixel did not bind the parallel-free search provider'
import json, pathlib, sys
config = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
assert config['tools']['web']['search']['provider'] == 'parallel-free'
assert 'searxng' not in config.get('plugins', {}).get('entries', {})
PY
services="$(compose_services)" || fail 'installed Compose selection cannot be resolved'
grep -qx searxng <<<"$services" && fail 'fresh parallel-free Pixel selected SearXNG'
docker ps -a --format '{{.Names}}' | grep -Eq '^ods-searxng$' \
    && fail 'fresh parallel-free Pixel started SearXNG'
docker image ls --format '{{.Repository}}' | sort -u >"$audit_root/images-parallel.txt"
if comm -13 "$audit_root/images-before.txt" "$audit_root/images-parallel.txt" | grep -Ei 'searxng'; then
    fail 'fresh parallel-free Pixel pulled SearXNG image'
fi
printf 'PASS: fresh parallel-free Pixel has no selected, running, or newly pulled SearXNG\n'
python3 "$harness/portal-requests.py" chat "$INSTALL_DIR/.env" \
    || fail 'ordinary installed Dashboard Pixel chat failed'
printf 'PASS: ordinary Dashboard Pixel chat reached the mock model after the lean selection\n'

run_installer 'selected local-search rerun' searxng
grep -qx 'PIXEL_WEB_SEARCH_PROVIDER=searxng' "$INSTALL_DIR/.env" \
    || fail 'SearXNG selection was not persisted'
python3 - "$HOME/.openclaw/openclaw.json" <<'PY' \
    || fail 'installed Pixel did not bind the SearXNG search provider'
import json, pathlib, sys
config = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
print('Pixel provider after selected rerun:', repr(config.get('tools', {}).get('web', {}).get('search', {}).get('provider')))
print('SearXNG plugin registered:', 'searxng' in config.get('plugins', {}).get('entries', {}))
assert config['tools']['web']['search']['provider'] == 'searxng'
assert 'searxng' in config.get('plugins', {}).get('entries', {})
PY
services="$(compose_services)" || fail 'selected Compose selection cannot be resolved'
grep -qx searxng <<<"$services" || fail 'selected Pixel omitted SearXNG Compose service'
[[ "$(docker inspect --format '{{.State.Running}}' ods-searxng)" == true ]] \
    || fail 'selected SearXNG is not running'
curl -fsS --max-time 30 'http://127.0.0.1:8888/search?q=OpenAI%20official%20website&format=json' \
    | python3 -c 'import json,sys; value=json.load(sys.stdin); assert isinstance(value.get("results"),list) and any(isinstance(item.get("url"),str) and item["url"].startswith("http") for item in value["results"])' \
    || fail 'selected SearXNG did not return a URL-bearing search result'
printf 'PASS: selected SearXNG serves a URL-bearing search result\n'
search_before_ok=false
if python3 "$harness/portal-requests.py" search "$INSTALL_DIR/.env"; then
    search_before_ok=true
else
    printf 'Selected-search mock request shape:\n' >&2
    grep -E 'search_probe_|search_tool_result_seen|accept=chat|reject=' "$mock_log" \
        | tail -n 18 >&2 || true
fi

run_installer 'same-provider SearXNG port change' searxng 8899
grep -qx 'SEARXNG_PORT=8899' "$INSTALL_DIR/.env" \
    || fail 'new SearXNG port was not persisted'
python3 - "$HOME/.openclaw/openclaw.json" <<'PY' \
    || fail 'same-provider rerun kept the old Pixel search origin'
import json, pathlib, sys
config = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
assert config['tools']['web']['search'] == {'provider': 'searxng'}
assert config['plugins']['entries']['searxng']['config']['webSearch']['baseUrl'] == 'http://127.0.0.1:8899'
PY
[[ "$(docker inspect --format '{{.State.Running}}' ods-searxng)" == true ]] \
    || fail 'SearXNG is not running after its port change'
curl -fsS --max-time 30 'http://127.0.0.1:8899/search?q=OpenAI%20official%20website&format=json' \
    | python3 -c 'import json,sys; value=json.load(sys.stdin); assert any(isinstance(item.get("url"),str) and item["url"].startswith("http") for item in value.get("results",[]))' \
    || fail 'new SearXNG origin did not return a URL-bearing result'
if curl -fsS --max-time 2 'http://127.0.0.1:8888/search?q=stale&format=json' >/dev/null 2>&1; then
    fail 'old SearXNG origin still served after the port change'
fi
search_after_ok=false
if python3 "$harness/portal-requests.py" search-port "$INSTALL_DIR/.env"; then
    search_after_ok=true
else
    printf 'Port-change search mock request shape:\n' >&2
    grep -E 'search_probe_|search_tool_result_seen|accept=chat|reject=' "$mock_log" \
        | tail -n 18 >&2 || true
fi
printf 'PASS: same-provider rerun changed the Pixel binding and SearXNG origin\n'
[[ "$search_before_ok" == true ]] || fail 'selected Pixel web_search did not return a result through Portal'
[[ "$search_after_ok" == true ]] || fail 'Pixel web_search did not use the new SearXNG origin'
printf 'PASS: real Pixel web_search reached both selected SearXNG origins\n'
