#!/usr/bin/env bash
# Disposable installed proof of the SearXNG Library add-back.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
audit_root="${RUNNER_TEMP:?runner temp is required}/ods-searxng-library"
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
    exit 1
}

[[ "${GITHUB_ACTIONS:-}" == true ]] || fail 'refusing non-disposable host'
[[ "$RUNNER_TEMP" == /* && "$INSTALL_DIR" == "$RUNNER_TEMP"/* ]] || fail 'install path is outside runner temp'
[[ "$(git -C "$(dirname "$product")" rev-parse HEAD)" == 56113ce18f9697bb820cb16fd9a8ad04d0a054f6 ]] || fail 'product source changed'
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

selected="$INSTALL_DIR/extensions/services/searxng/compose.yaml"
disabled="$INSTALL_DIR/extensions/services/searxng/compose.yaml.disabled"
settings="$INSTALL_DIR/config/searxng/settings.yml"
[[ -f "$disabled" && ! -e "$selected" ]] || fail 'fresh lean install selected SearXNG'
[[ -s "$settings" ]] || fail 'fresh lean install omitted SearXNG settings'
settings_hash="$(sha256sum "$settings" | cut -d ' ' -f 1)"
if docker image ls --format '{{.Repository}}' | grep -Eq '^searxng/searxng$'; then
    fail 'fresh lean install pulled the unselected SearXNG image'
fi
printf 'PASS: fresh lean install omitted SearXNG while retaining its configuration\n'

api_instance="$(docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}' ods-dashboard-api)" \
    || fail 'Dashboard API was not running before Add'
catalog_row() {
    curl -fsS --max-time 30 http://127.0.0.1:3001/api/extensions/catalog \
        | python3 -c 'import json,sys; item=next(x for x in json.load(sys.stdin)["extensions"] if x["id"]=="searxng"); print(json.dumps({k:item.get(k) for k in ("source","status","library_manageable","library_selected")}))'
}
before="$(catalog_row)" || fail 'Library catalog unavailable before Add'
python3 - "$before" <<'PY' || fail 'SearXNG was not Available for Add'
import json,sys
row=json.loads(sys.argv[1])
assert row=={"source":"core","status":"disabled","library_manageable":True,"library_selected":False}, row
PY

enable_search() {
    local response="$1" code
    code="$(curl -sS --max-time 900 -o "$response" -w '%{http_code}' \
        -X POST http://127.0.0.1:3001/api/extensions/searxng/enable)" \
        || fail 'SearXNG Add request was unreachable'
    [[ "$code" == 200 ]] || fail "SearXNG Add returned HTTP $code"
    python3 - "$response" <<'PY' || fail 'SearXNG Add response reported failure'
import json,sys
value=json.load(open(sys.argv[1],encoding="utf-8"))
assert "searxng" in value.get("enabled_services",[]), value
assert value.get("failed_services")==[], value
PY
    [[ -f "$selected" && ! -e "$disabled" ]] || fail 'SearXNG fragment was not selected'
    local health=""
    for attempt in {1..90}; do
        health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' ods-searxng 2>/dev/null || true)"
        [[ "$health" == healthy ]] && break
        [[ "$health" == unhealthy ]] && fail 'SearXNG became unhealthy'
        sleep 5
    done
    [[ "$health" == healthy ]] || fail 'SearXNG did not become healthy'
}

enable_search "$audit_root/add.json"
after=""
for attempt in {1..30}; do
    after="$(catalog_row)" || fail 'Library catalog unavailable after Add'
    [[ "$(python3 - "$after" <<'PY'
import json,sys
print(json.loads(sys.argv[1])["status"])
PY
)" == enabled ]] && break
    sleep 5
done
python3 - "$after" <<'PY' || fail 'Library status did not reflect healthy SearXNG'
import json,sys
row=json.loads(sys.argv[1])
assert row["status"]=="enabled" and row["library_selected"] is True, row
PY
[[ "$(docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}' ods-dashboard-api)" == "$api_instance" ]] \
    || fail 'Dashboard API restarted during Add'
printf 'PASS: Library Add started healthy SearXNG without restarting Dashboard API\n'

for query in 'OpenAI' 'Linux kernel' 'Wikipedia'; do
    curl -fsS --max-time 90 -G 'http://127.0.0.1:8888/search' \
        --data-urlencode "q=$query" --data-urlencode 'format=json' \
        >"$audit_root/search.json" || fail 'SearXNG JSON search request failed'
    if python3 - "$audit_root/search.json" <<'PY'
import json,sys
body=json.load(open(sys.argv[1],encoding="utf-8"))
assert isinstance(body.get("results"),list), body.keys()
assert body["results"], "Search returned no results"
PY
    then
        break
    fi
    sleep 5
done
python3 - "$audit_root/search.json" <<'PY' || fail 'SearXNG returned no usable JSON search response'
import json,sys
body=json.load(open(sys.argv[1],encoding="utf-8"))
assert isinstance(body.get("results"),list), body.keys()
assert body["results"], "Search returned no results"
print("PASS: SearXNG returned real JSON search results")
PY

disable_code="$(curl -sS --max-time 900 -o "$audit_root/disable.json" -w '%{http_code}' \
    -X POST http://127.0.0.1:3001/api/extensions/searxng/disable)" \
    || fail 'SearXNG Disable request was unreachable'
[[ "$disable_code" == 200 ]] || fail "SearXNG Disable returned HTTP $disable_code"
[[ -f "$disabled" && ! -e "$selected" ]] || fail 'SearXNG fragment remained selected after Disable'
[[ "$(sha256sum "$settings" | cut -d ' ' -f 1)" == "$settings_hash" ]] \
    || fail 'SearXNG settings changed during Disable'
printf 'PASS: Disable preserved the SearXNG settings\n'

enable_search "$audit_root/readd.json"
[[ "$(sha256sum "$settings" | cut -d ' ' -f 1)" == "$settings_hash" ]] \
    || fail 'SearXNG settings changed during re-add'
[[ "$(docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}' ods-dashboard-api)" == "$api_instance" ]] \
    || fail 'Dashboard API restarted during re-add'
printf 'PASS: SearXNG was re-added healthy with settings retained\n'
