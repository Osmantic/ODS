#!/usr/bin/env bash
# Regression test for manifest-owned HTTP health probe metadata.

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$TEST_DIR")"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

mkdir -p \
    "$SANDBOX/bin" \
    "$SANDBOX/lib" \
    "$SANDBOX/extensions/services/fakeheader" \
    "$SANDBOX/extensions/services/fakeport" \
    "$SANDBOX/extensions/services/fakeredirect"

cp "$PROJECT_DIR/ods-cli" "$SANDBOX/ods-cli"
cp "$PROJECT_DIR/lib/service-registry.sh" "$PROJECT_DIR/lib/safe-env.sh" "$SANDBOX/lib/"
if [[ -f "$PROJECT_DIR/lib/python-cmd.sh" ]]; then
    cp "$PROJECT_DIR/lib/python-cmd.sh" "$SANDBOX/lib/"
fi

cat > "$SANDBOX/.env" <<'ENV'
ODS_MODE=local
TIER=test
LLM_MODEL=test-model
GPU_BACKEND=cpu
ENV
printf 'services: {}\n' > "$SANDBOX/docker-compose.base.yml"
printf '%s\n' '-f docker-compose.base.yml' > "$SANDBOX/.compose-flags"

cat > "$SANDBOX/extensions/services/fakeheader/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: fakeheader
  name: Header-aware service
  category: optional
  container_name: ods-fakeheader
  external_port_default: 3101
  health: /ready
  health_header: "X-Health: ready"
MANIFEST

cat > "$SANDBOX/extensions/services/fakeredirect/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: fakeredirect
  name: Redirecting service
  category: optional
  container_name: ods-fakeredirect
  external_port_default: 3301
  health: /redirect
MANIFEST

cat > "$SANDBOX/extensions/services/fakeport/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: fakeport
  name: Dedicated-health-port service
  category: optional
  container_name: ods-fakeport
  external_port_default: 3201
  health: /healthz
  health_port: 9191
  health_port_env: FAKE_HEALTH_PORT
MANIFEST

cat > "$SANDBOX/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
if [[ "$*" == *"ps --format {{.Service}}"* ]]; then
    printf 'fakeheader\nfakeport\nfakeredirect\n'
elif [[ "$*" == *"ps --format {{.Name}}"* ]]; then
    printf 'ods-fakeheader\nods-fakeport\nods-fakeredirect\n'
fi
exit 0
DOCKER
chmod +x "$SANDBOX/bin/docker"

cat > "$SANDBOX/bin/curl" <<'CURL'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CURL_LOG"
case "$*" in
    *"-H X-Health: ready"*":3101/ready"*) printf '200'; exit 0 ;;
    *":9292/healthz"*) printf '204'; exit 0 ;;
    *":3301/redirect"*) printf '302'; exit 0 ;;
    *) exit 22 ;;
esac
CURL
chmod +x "$SANDBOX/bin/curl"

export CURL_LOG="$SANDBOX/curl.log"
status_json="$({
    cd "$SANDBOX"
    FAKE_HEALTH_PORT=9292 ODS_HOME="$SANDBOX" PATH="$SANDBOX/bin:$PATH" "$SANDBOX/ods-cli" status --json
})"

jq -e '
  (.services[] | select(.id == "fakeheader") | .status) == "healthy"
  and (.services[] | select(.id == "fakeport") | .status) == "healthy"
  and (.services[] | select(.id == "fakeredirect") | .status) == "unhealthy"
' <<< "$status_json" >/dev/null

grep -Fq -- '-H X-Health: ready' "$CURL_LOG"
grep -Fq -- 'http://127.0.0.1:9292/healthz' "$CURL_LOG"

status_text="$({
    cd "$SANDBOX"
    FAKE_HEALTH_PORT=9292 ODS_HOME="$SANDBOX" PATH="$SANDBOX/bin:$PATH" "$SANDBOX/ods-cli" status
} 2>&1)"
grep -Fq 'Header-aware service: healthy' <<< "$status_text"
grep -Fq 'Dedicated-health-port service: healthy' <<< "$status_text"
grep -Fq 'Redirecting service: not responding' <<< "$status_text"

chromadb_compose="$PROJECT_DIR/extensions/library/services/chromadb/compose.yaml"
if grep -Fq '/dev/tcp/' "$chromadb_compose"; then
    echo "ChromaDB healthcheck must use the image-provided curl probe, not a hand-written HTTP parser" >&2
    exit 1
fi
grep -Fq 'curl' "$chromadb_compose"
grep -Fq -- '-f' "$chromadb_compose"
grep -Fq 'http://localhost:8000/api/v2/heartbeat' "$chromadb_compose"

echo "ODS_CLI_HEALTH_PROBE_CONTRACT_OK"
