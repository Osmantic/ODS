#!/usr/bin/env bash
# Exercise the installed CLI: an empty successful Compose snapshot means the
# stack is stopped, not unknown. Optional health ports may have other listeners.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT
install_dir="$FIXTURE/install"
mkdir -p "$install_dir/lib" "$install_dir/extensions/services" "$FIXTURE/bin" "$FIXTURE/foreign/ods"
cp "$ROOT_DIR/ods-cli" "$install_dir/ods-cli"
for library in safe-env.sh service-registry.sh python-cmd.sh preflight-llm-route.sh; do
    cp "$ROOT_DIR/lib/$library" "$install_dir/lib/"
done
printf 'services: {}\n' > "$install_dir/docker-compose.base.yml"
printf 'services: {}\n' > "$install_dir/docker-compose.host-native-llm.yml"
printf 'foreign configuration\n' > "$FIXTURE/foreign/ods/.env"
printf 'foreign updater\n' > "$FIXTURE/foreign/ods/ods-update.sh"

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$*"; }

for entry in 'dashboard core 3301' 'whisper optional 9000' 'whisper-helper optional 9001' \
    'llama-server core 8080' 'model-router core 8200'; do
    read -r service category port <<< "$entry"
    mkdir -p "$install_dir/extensions/services/$service"
    cat > "$install_dir/extensions/services/$service/manifest.yaml" <<MANIFEST
schema_version: ods.services.v1
service:
  id: $service
  name: $service
  category: $category
  port: $port
  health: /health
MANIFEST
done

cat > "$FIXTURE/bin/docker" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$STATUS_DOCKER_CALLS"
[[ "$*" == 'compose -f docker-compose.base.yml'*' ps --format {{.Service}}' ]] || exit 64
case "$STATUS_SCENARIO" in
    empty|native) ;;
    running) printf 'dashboard\nwhisper-helper\n' ;;
    race)
        if [[ "$(wc -l < "$STATUS_DOCKER_CALLS")" -eq 1 ]]; then
            printf 'dashboard\n'
        else
            printf 'whisper\n'
        fi
        ;;
    *) printf 'whisper\n'; printf 'Cannot connect to the Docker daemon\n' >&2; exit 1 ;;
esac
STUB
cat > "$FIXTURE/bin/docker-compose" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$STATUS_V1_CALLS"
[[ "$*" == '-f docker-compose.base.yml ps --services --filter status=running' ]] || exit 64
case "$STATUS_SCENARIO" in
    v1-empty) ;;
    v1-running) printf 'dashboard\nwhisper\n' ;;
    *) printf 'whisper\n'; exit 1 ;;
esac
STUB
cat > "$FIXTURE/bin/curl" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
# Every port answers, including unrelated listeners at stopped services' ports.
printf '%s\n' "$*" >> "$STATUS_HTTP_CALLS"
exit 0
STUB
for command in sudo systemctl launchctl wget; do
    cat > "$FIXTURE/bin/$command" <<'STUB'
#!/usr/bin/env bash
printf 'Unexpected host/network mutation: %s\n' "$0 $*" >> "$STATUS_MUTATOR_CALLS"
exit 99
STUB
done
chmod +x "$FIXTURE/bin/"*

export STATUS_DOCKER_CALLS="$FIXTURE/docker-calls" STATUS_V1_CALLS="$FIXTURE/v1-calls"
export STATUS_HTTP_CALLS="$FIXTURE/http-calls" STATUS_MUTATOR_CALLS="$FIXTURE/mutator-calls"
run_status() {
    local scenario="$1" native="${2:-false}" command="${3:-status}"
    printf '%s\n' 'GPU_BACKEND=cpu' 'ODS_MODE=local' \
        'HERMES_DASHBOARD_SESSION_TOKEN=fixture-session' > "$install_dir/.env"
    printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags"
    if [[ "$native" == true ]]; then
        printf '%s\n' 'NATIVE_LLM_BASE_URL=http://127.0.0.1:8080' >> "$install_dir/.env"
        printf '%s\n' '-f docker-compose.base.yml -f docker-compose.host-native-llm.yml' \
            > "$install_dir/.compose-flags"
    fi
    : > "$STATUS_DOCKER_CALLS"; : > "$STATUS_V1_CALLS"; : > "$STATUS_HTTP_CALLS"
    cp "$install_dir/.env" "$FIXTURE/expected-env"
    if [[ "$command" == status ]]; then
        HOME="$FIXTURE/foreign" INSTALL_DIR="$install_dir" ODS_HOME="$install_dir" \
            PATH="$FIXTURE/bin:$PATH" STATUS_SCENARIO="$scenario" \
            bash "$install_dir/ods-cli" status --json > "$FIXTURE/report.json"
    else
        HOME="$FIXTURE/foreign" INSTALL_DIR="$install_dir" ODS_HOME="$install_dir" \
            PATH="$FIXTURE/bin:$PATH" STATUS_SCENARIO="$scenario" \
            bash "$install_dir/ods-cli" status-json > "$FIXTURE/report.json"
    fi
    jq -e '.services | length > 0' "$FIXTURE/report.json" >/dev/null || fail 'missing JSON services'
    cmp "$FIXTURE/expected-env" "$install_dir/.env" || fail 'status changed installed configuration'
}
assert_service() {
    jq -e --arg id "$1" --arg container "$2" --arg health "$3" \
        '.services[] | select(.id == $id) | .containerStatus == $container and .status == $health' \
        "$FIXTURE/report.json" >/dev/null || fail "$1 should be $2/$3: $(cat "$FIXTURE/report.json")"
}
assert_no_optional_probe() {
    if grep -Eq '127\.0\.0\.1:900[01]/' "$STATUS_HTTP_CALLS"; then
        fail 'stopped optional service probed an unrelated listener'
    fi
}

run_status empty
assert_service whisper stopped stopped
assert_service whisper-helper stopped stopped
assert_service dashboard stopped healthy
assert_no_optional_probe
pass 'successful empty Compose snapshot reports stopped optional services without HTTP probes'

run_status running
assert_service dashboard running healthy
assert_service whisper stopped stopped
assert_service whisper-helper running healthy
grep -q '127.0.0.1:9001/health' "$STATUS_HTTP_CALLS" || fail 'running optional service was not probed'
grep -q '127.0.0.1:9000/health' "$STATUS_HTTP_CALLS" && fail 'partial service name matched whisper'
pass 'partial running stack probes only running optional services with exact service IDs'

run_status race
[[ "$(wc -l < "$STATUS_DOCKER_CALLS")" -eq 1 ]] || fail 'Compose snapshot read more than once'
assert_service dashboard running healthy
assert_service whisper stopped stopped
assert_service whisper-helper stopped stopped
assert_no_optional_probe
pass 'one Compose snapshot drives the whole report'

run_status unavailable
assert_service whisper unknown healthy
assert_service dashboard unknown healthy
pass 'failed Compose reads with partial output retain unknown state and best-effort HTTP health'

mv "$FIXTURE/bin/docker-compose" "$FIXTURE/unused-docker-compose"
run_status unavailable
assert_service whisper unknown healthy
[[ ! -s "$STATUS_V1_CALLS" ]] || fail 'attempted a missing Compose v1 binary'
mv "$FIXTURE/unused-docker-compose" "$FIXTURE/bin/docker-compose"
pass 'failed Compose v2 without Compose v1 retains unknown state'

run_status v1-empty
assert_service whisper stopped stopped
assert_no_optional_probe
[[ "$(wc -l < "$STATUS_V1_CALLS")" -eq 1 ]] || fail 'missing Compose v1 snapshot'
pass 'successful empty Compose v1 fallback uses the same stack flags and reports stopped'

run_status v1-running false status-json
assert_service whisper running healthy
assert_service whisper-helper stopped stopped
pass 'legacy status-json entrypoint shares the Compose v1 running snapshot'

run_status native true
jq -e 'all(.services[]; .id != "llama-server")' "$FIXTURE/report.json" >/dev/null \
    || fail 'host-native model was treated as a stopped container'
assert_service model-router stopped healthy
grep -q '127.0.0.1:8080/health' "$STATUS_HTTP_CALLS" && fail 'host-native model route was probed as a container'
assert_no_optional_probe
pass 'host-native inference routing and core HTTP checks remain available'

[[ ! -e "$STATUS_MUTATOR_CALLS" ]] || fail "unexpected mutator: $(cat "$STATUS_MUTATOR_CALLS")"
[[ "$(cat "$FIXTURE/foreign/ods/.env")" == 'foreign configuration' ]] || fail 'foreign configuration changed'
[[ "$(cat "$FIXTURE/foreign/ods/ods-update.sh")" == 'foreign updater' ]] || fail 'foreign updater changed'
pass 'installed and foreign configuration remain unchanged; no host mutators ran'
