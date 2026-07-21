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
    "$SANDBOX/extensions/services/fakebadenv" \
    "$SANDBOX/extensions/services/fakebadportenv" \
    "$SANDBOX/extensions/services/fakeenv" \
    "$SANDBOX/extensions/services/fakehealthenv" \
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

cat > "$SANDBOX/extensions/services/fakebadenv/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: fakebadenv
  name: Invalid health-port environment service
  category: optional
  container_name: ods-fakebadenv
  external_port_default: 0
  health: /health
  health_port: 9199
  health_port_env: BAD-NAME
MANIFEST

cat > "$SANDBOX/extensions/services/fakebadportenv/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: fakebadportenv
  name: Invalid public-port environment service
  category: optional
  container_name: ods-fakebadportenv
  external_port_default: 9198
  external_port_env: ALSO-BAD
  health: /health
MANIFEST

cat > "$SANDBOX/extensions/services/fakeenv/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: fakeenv
  name: Environment-port service
  category: optional
  container_name: ods-fakeenv
  external_port_default: 3401
  external_port_env: FAKE_PUBLIC_PORT
  health: /health
MANIFEST

cat > "$SANDBOX/extensions/services/fakehealthenv/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: fakehealthenv
  name: Environment-only health-port service
  category: optional
  container_name: ods-fakehealthenv
  external_port_default: 0
  health: /health-only
  health_port_env: FAKE_ONLY_HEALTH_PORT
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
    printf 'fakeheader\nfakeenv\nfakehealthenv\nfakeport\nfakeredirect\n'
elif [[ "$*" == *"ps --format {{.Name}}"* ]]; then
    printf 'ods-fakeheader\nods-fakeenv\nods-fakehealthenv\nods-fakeport\nods-fakeredirect\n'
fi
exit 0
DOCKER
chmod +x "$SANDBOX/bin/docker"

cat > "$SANDBOX/bin/curl" <<'CURL'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CURL_LOG"
case "$*" in
    *"-H X-Health: ready"*":3101/ready"*) printf '200'; exit 0 ;;
    *":9494/health"*) printf '200'; exit 0 ;;
    *":9595/health-only"*) printf '200'; exit 0 ;;
    *":9292/healthz"*) printf '204'; exit 0 ;;
    *":3301/redirect"*) printf '302'; exit 0 ;;
    *) exit 22 ;;
esac
CURL
chmod +x "$SANDBOX/bin/curl"

export CURL_LOG="$SANDBOX/curl.log"

# Fail closed: registry load requires a PyYAML-capable interpreter. Without it
# sr_load returns an empty SERVICE_IDS and invalid-env SKIP checks would false-pass.
if [[ -f "$SANDBOX/lib/python-cmd.sh" ]]; then
    # shellcheck source=/dev/null
    source "$SANDBOX/lib/python-cmd.sh"
elif [[ -f "$PROJECT_DIR/lib/python-cmd.sh" ]]; then
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/lib/python-cmd.sh"
else
    echo "ERROR: python-cmd.sh not found; cannot resolve PyYAML-capable Python" >&2
    exit 1
fi

YAML_PYTHON=""
if ! YAML_PYTHON="$(ods_detect_python_cmd_with_module yaml 2>/dev/null)" || [[ -z "$YAML_PYTHON" ]]; then
    echo "ERROR: PyYAML-capable Python is required for health probe contract (import yaml failed)" >&2
    echo "ERROR: Install PyYAML (e.g. uv pip install pyyaml) or set ODS_PYTHON_CMD to a yaml-capable interpreter" >&2
    exit 1
fi
export ODS_PYTHON_CMD="$YAML_PYTHON"

registry_load_rc=0
# stderr from SKIP lines must not be captured into registry_ids; keep it on the
# test process stderr. Do not place `2>/dev/null` inside the double quotes after
# `)` — that would append the redirect text to the captured stdout.
registry_ids="$(
    cd "$SANDBOX"
    SCRIPT_DIR="$SANDBOX"
    export ODS_PYTHON_CMD
    # shellcheck source=/dev/null
    source "$SANDBOX/lib/service-registry.sh"
    sr_load
    printf '%s\n' "${SERVICE_IDS[@]}"
)" || registry_load_rc=$?

if [[ "$registry_load_rc" -ne 0 ]]; then
    echo "ERROR: service registry load failed (exit $registry_load_rc); cannot validate health probe contract" >&2
    exit 1
fi
if [[ -z "${registry_ids//[$'\t\r\n ']/}" ]]; then
    echo "ERROR: service registry is empty after sr_load (PyYAML missing or sandbox manifests not loaded)" >&2
    exit 1
fi
for required_id in fakeheader fakeenv fakeport fakeredirect fakehealthenv; do
    if ! grep -Fxq "$required_id" <<< "$registry_ids"; then
        echo "ERROR: registry missing required fake service: $required_id (incomplete registry load)" >&2
        exit 1
    fi
done
if grep -Fxq 'fakebadenv' <<< "$registry_ids"; then
    echo "Registry must reject an invalid health_port_env name before indirect expansion" >&2
    exit 1
fi
if grep -Fxq 'fakebadportenv' <<< "$registry_ids"; then
    echo "Registry must reject an invalid external_port_env name before indirect expansion" >&2
    exit 1
fi

status_json="$({
    cd "$SANDBOX"
    FAKE_PUBLIC_PORT=9494 FAKE_HEALTH_PORT=9292 FAKE_ONLY_HEALTH_PORT=9595 \
        ODS_HOME="$SANDBOX" ODS_PYTHON_CMD="$ODS_PYTHON_CMD" PATH="$SANDBOX/bin:$PATH" \
        "$SANDBOX/ods-cli" status --json
})"

jq -e '
  (.services[] | select(.id == "fakeheader") | .status) == "healthy"
  and (.services[] | select(.id == "fakeenv") | .status) == "healthy"
  and (.services[] | select(.id == "fakehealthenv") | .status) == "healthy"
  and (.services[] | select(.id == "fakeport") | .status) == "healthy"
  and (.services[] | select(.id == "fakeredirect") | .status) == "unhealthy"
' <<< "$status_json" >/dev/null

grep -Fq -- '-H X-Health: ready' "$CURL_LOG"
grep -Fq -- 'http://127.0.0.1:9494/health' "$CURL_LOG"
grep -Fq -- 'http://127.0.0.1:9595/health-only' "$CURL_LOG"
grep -Fq -- 'http://127.0.0.1:9292/healthz' "$CURL_LOG"

status_text="$({
    cd "$SANDBOX"
    FAKE_PUBLIC_PORT=9494 FAKE_HEALTH_PORT=9292 FAKE_ONLY_HEALTH_PORT=9595 \
        ODS_HOME="$SANDBOX" ODS_PYTHON_CMD="$ODS_PYTHON_CMD" PATH="$SANDBOX/bin:$PATH" \
        "$SANDBOX/ods-cli" status
} 2>&1)"
grep -Fq 'Header-aware service: healthy' <<< "$status_text"
grep -Fq 'Environment-port service: healthy' <<< "$status_text"
grep -Fq 'Environment-only health-port service: healthy' <<< "$status_text"
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
