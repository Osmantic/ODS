#!/usr/bin/env bash
# Disposable installed proof that Library Disable keeps selected dependencies intact.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
audit_root="${RUNNER_TEMP:?runner temp is required}/ods-library-guard"
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
[[ "$(git -C "$(dirname "$product")" rev-parse HEAD)" == 01995e75e72b8c9a982163320e67429af635f658 ]] || fail 'product source changed'
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
    fail 'exact product installer did not finish; logs retained on disposable runner'
fi
[[ -f "$INSTALL_DIR/extensions/services/n8n/compose.yaml.disabled" ]] \
    || fail 'fresh Core did not keep n8n disabled'
[[ ! -e "$INSTALL_DIR/extensions/services/n8n/compose.yaml" ]] \
    || fail 'fresh Core selected n8n unexpectedly'
if docker image ls --format '{{.Repository}}' | grep -Eq '^n8nio/n8n$'; then
    fail 'fresh Core pulled the unselected n8n image'
fi
printf 'PASS: fresh installed Core omitted the n8n fragment and image\n'

api_instance="$(docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}' ods-dashboard-api)" \
    || fail 'Dashboard API was not running before n8n Add'

curl -fsS --max-time 30 http://127.0.0.1:3001/api/extensions/catalog >"$audit_root/catalog-before.json" \
    || fail 'installed Library catalog unavailable'
python3 - "$audit_root/catalog-before.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
item = next(item for item in value["extensions"] if item["id"] == "n8n")
assert item["source"] == "core", item["source"]
assert item["status"] == "disabled", item["status"]
assert item["library_manageable"] is True, item
assert item["library_selected"] is False, item
print("PASS: Library catalog reports bundled n8n disabled")
PY

sentinel="$INSTALL_DIR/data/n8n/ods-acceptance-retain.txt"
printf '%s\n' 'n8n-data-retained-through-Library-actions' >"$sentinel"
sentinel_hash="$(sha256sum "$sentinel" | cut -d ' ' -f 1)"

curl -fsS --max-time 900 -X POST http://127.0.0.1:3001/api/extensions/n8n/enable \
    >"$audit_root/enable.json" || fail 'n8n backend enable endpoint failed'
python3 - "$audit_root/enable.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert "n8n" in value.get("enabled_services", []), value
assert value.get("failed_services") == [], value
print("PASS: bundled n8n Library Add returned no failed service")
PY
[[ -f "$INSTALL_DIR/extensions/services/n8n/compose.yaml" ]] \
    || fail 'n8n backend did not activate its installed fragment'
for attempt in {1..90}; do
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' ods-n8n 2>/dev/null || true)"
    [[ "$health" == healthy ]] && break
    [[ "$health" == unhealthy ]] && fail 'n8n container became unhealthy'
    sleep 5
done
[[ "$health" == healthy ]] || fail 'n8n container did not become healthy'
docker exec ods-n8n test -f /tmp/.n8n/ods-acceptance-retain.txt \
    || fail 'n8n container did not mount the retained data directory'
printf 'PASS: n8n container became healthy after Library backend enable\n'

catalog_status=""
for attempt in {1..30}; do
    curl -fsS --max-time 30 http://127.0.0.1:3001/api/extensions/catalog >"$audit_root/catalog-after.json" \
        || fail 'Library catalog unavailable after n8n enable'
    catalog_status="$(python3 - "$audit_root/catalog-after.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
item = next(item for item in value["extensions"] if item["id"] == "n8n")
assert item["library_manageable"] is True, item
assert item["library_selected"] is True, item
print(item["status"])
PY
)"
    [[ "$catalog_status" == enabled ]] && break
    sleep 5
done
printf 'Catalog n8n status after healthy start: %s\n' "$catalog_status"
[[ "$catalog_status" == enabled ]] || fail 'Library catalog did not observe healthy n8n after enable'
[[ "$(docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}' ods-dashboard-api)" == "$api_instance" ]] \
    || fail 'Dashboard API restarted during n8n Add'
printf 'PASS: Library catalog reports n8n enabled with the original Dashboard API process\n'

# Add a disposable, genuinely running user extension whose manifest depends
# on n8n. This exercises the installed API, host agent, and merged Compose
# selection rather than only calling the dependency helper in a unit test.
consumer_dir="$INSTALL_DIR/data/user-extensions/n8n-consumer"
mkdir -p "$consumer_dir"
cat >"$consumer_dir/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: n8n-consumer
  name: Acceptance n8n consumer
  type: docker
  compose_file: compose.yaml
  depends_on: [n8n]
YAML
cat >"$consumer_dir/compose.yaml.disabled" <<'YAML'
services:
  n8n-consumer:
    image: busybox:1.36
    container_name: ods-n8n-consumer
    command: ["sh", "-c", "sleep 3600"]
    depends_on: [n8n]
    networks: [ods-network]
networks:
  ods-network:
    external: true
    name: ods-network
YAML
chown -R 1000:1000 "$consumer_dir"
curl -fsS --max-time 900 -X POST http://127.0.0.1:3001/api/extensions/n8n-consumer/enable \
    >"$audit_root/consumer-enable.json" || fail 'disposable dependent did not enable'
python3 - "$audit_root/consumer-enable.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert "n8n-consumer" in value.get("enabled_services", []), value
assert value.get("failed_services") == [], value
print("PASS: disposable dependent enabled through installed API")
PY
[[ "$(docker inspect --format '{{.State.Running}}' ods-n8n-consumer)" == true ]] \
    || fail 'disposable dependent container is not running'
n8n_started="$(docker inspect --format '{{.State.StartedAt}}' ods-n8n)"
disable_code="$(curl -sS --max-time 30 -o "$audit_root/blocked-disable.json" \
    -w '%{http_code}' -X POST http://127.0.0.1:3001/api/extensions/n8n/disable)" \
    || fail 'blocked Disable request was unreachable'
[[ "$disable_code" == 409 ]] || fail "n8n Disable returned $disable_code, expected 409"
python3 - "$audit_root/blocked-disable.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert "n8n-consumer" in value.get("detail", ""), value
print("PASS: installed Disable named its enabled dependent")
PY
[[ -f "$INSTALL_DIR/extensions/services/n8n/compose.yaml" ]] \
    || fail 'blocked Disable removed the n8n definition'
[[ "$(docker inspect --format '{{.State.Running}}' ods-n8n)" == true ]] \
    || fail 'blocked Disable stopped n8n'
[[ "$(docker inspect --format '{{.State.StartedAt}}' ods-n8n)" == "$n8n_started" ]] \
    || fail 'blocked Disable restarted n8n'
[[ "$(docker inspect --format '{{.State.Running}}' ods-n8n-consumer)" == true ]] \
    || fail 'blocked Disable affected its dependent'
[[ "$(sha256sum "$sentinel" | cut -d ' ' -f 1)" == "$sentinel_hash" ]] \
    || fail 'blocked Disable changed retained n8n data'
printf 'PASS: blocked Disable kept n8n, its dependent, and retained data intact\n'

curl -fsS --max-time 180 -X POST http://127.0.0.1:3001/api/extensions/n8n-consumer/disable \
    >"$audit_root/consumer-disable.json" || fail 'disposable dependent did not disable'
[[ -f "$consumer_dir/compose.yaml.disabled" ]] || fail 'dependent definition stayed enabled'
[[ "$(docker inspect --format '{{.State.Running}}' ods-n8n-consumer)" == false ]] \
    || fail 'dependent container stayed running'
printf 'PASS: dependent disabled first, making n8n removable\n'

curl -fsS --max-time 180 -X POST http://127.0.0.1:3001/api/extensions/n8n/disable \
    >"$audit_root/disable.json" || fail 'n8n Library Disable endpoint failed'
python3 - "$audit_root/disable.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["action"] == "disabled", value
print("PASS: bundled n8n Library Disable completed")
PY
[[ -f "$INSTALL_DIR/extensions/services/n8n/compose.yaml.disabled" ]] \
    || fail 'n8n Disable did not restore the omitted fragment'
[[ ! -e "$INSTALL_DIR/extensions/services/n8n/compose.yaml" ]] \
    || fail 'n8n fragment still selected after Disable'
[[ "$(sha256sum "$sentinel" | cut -d ' ' -f 1)" == "$sentinel_hash" ]] \
    || fail 'n8n data sentinel changed after Disable'
if docker inspect ods-n8n >/dev/null 2>&1; then
    [[ "$(docker inspect --format '{{.State.Running}}' ods-n8n)" == false ]] \
        || fail 'n8n container kept running after Disable'
fi
curl -fsS --max-time 30 http://127.0.0.1:3001/api/extensions/catalog >"$audit_root/catalog-disabled.json" \
    || fail 'Library catalog unavailable after Disable'
python3 - "$audit_root/catalog-disabled.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
item = next(item for item in value["extensions"] if item["id"] == "n8n")
assert item["status"] == "disabled", item
assert item["library_selected"] is False, item
print("PASS: Library catalog reports n8n disabled without Dashboard API restart")
PY
[[ "$(docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}' ods-dashboard-api)" == "$api_instance" ]] \
    || fail 'Dashboard API restarted during n8n Disable'
printf 'PASS: Library Disable retained n8n data and restored addable state\n'
