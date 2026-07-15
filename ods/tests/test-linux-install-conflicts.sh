#!/usr/bin/env bash
# Behavioral contract for the Linux installer conflict gate.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
. "$ROOT_DIR/installers/lib/install-conflicts.sh"

fail() {
    echo "[FAIL] $*"
    exit 1
}

pass() {
    echo "[PASS] $*"
}

assert_status() {
    local expected="$1"
    local actual="$2"
    local description="$3"
    [[ "$actual" == "$expected" ]] \
        || fail "$description (expected $expected, got $actual)"
    pass "$description"
}

assert_arg() {
    local expected="$1"
    local description="$2"
    grep -qFx -- "$expected" "$FAKE_ARGS_FILE" \
        || fail "$description (missing argument: $expected)"
    pass "$description"
}

assert_no_arg() {
    local unexpected="$1"
    local description="$2"
    if grep -qFx -- "$unexpected" "$FAKE_ARGS_FILE"; then
        fail "$description (unexpected argument: $unexpected)"
    fi
    pass "$description"
}

log() { :; }
ai() { :; }
ai_warn() { :; }
ods_progress() { :; }

run_check() {
    local status=0
    if ods_run_install_conflict_check; then
        status=0
    else
        status=$?
    fi
    printf '%s\n' "$status"
}

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

SCRIPT_DIR="$tmpdir/source"
INSTALL_DIR="$tmpdir/install"
FAKE_ARGS_FILE="$tmpdir/detector-args.txt"
export FAKE_ARGS_FILE
mkdir -p "$SCRIPT_DIR/scripts" "$INSTALL_DIR"

cat > "$SCRIPT_DIR/.env.example" <<'ENV'
ODS_MODE=local
GPU_BACKEND=nvidia
LANGFUSE_PORT=3006
ENV

cat > "$SCRIPT_DIR/scripts/check-install-conflicts.py" <<'DETECTOR'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$FAKE_ARGS_FILE"
exit "${FAKE_DETECTOR_STATUS:-0}"
DETECTOR

ODS_PYTHON_CMD="/bin/bash"
DOCKER_COMPOSE_CMD="docker compose"
DOCKER_CMD="docker"
COMPOSE_FLAGS="-f docker-compose.base.yml"
INSTALL_CONFLICT_REPORT_FILE="$tmpdir/conflicts.json"
DRY_RUN=false
BIND_ADDRESS="127.0.0.1"
BIND_ADDRESS_EXPLICIT=false
GPU_BACKEND=cpu
GPU_COUNT=0
ODS_MODE=local
LEMONADE_EXTERNAL=false
WEBUI_PORT=9090
export FAKE_DETECTOR_STATUS=0
ODS_ALLOW_CONFLICTS=""
ODS_ALLOW_LEGACY_PARALLEL=""
FORCE=false
INTERACTIVE=true
unset WHISPER_PORT

status="$(run_check)"
assert_status 0 "$status" "clear fresh install passes"
assert_no_arg "--update" "existing install directory alone is not update proof"
assert_arg "BIND_ADDRESS=127.0.0.1" "fresh install passes effective bind address"
assert_arg "WHISPER_PORT=9000" "fresh install passes default Whisper port"
assert_arg "LANGFUSE_PORT=3006" "fresh install passes default Langfuse port"
assert_arg "WEBUI_PORT=9090" "fresh install passes explicit service port"
assert_arg "ODS_MODE=local" "fresh install passes effective mode"

cat > "$INSTALL_DIR/.env" <<'ENV'
BIND_ADDRESS=0.0.0.0
WEBUI_PORT=3900
WHISPER_PORT=9200
LANGFUSE_PORT=3010
ENV
unset WEBUI_PORT
GPU_BACKEND=amd
GPU_COUNT=1
ODS_MODE=local
BIND_ADDRESS="127.0.0.1"
BIND_ADDRESS_EXPLICIT=false

status="$(run_check)"
assert_status 0 "$status" "existing .env enables update ownership checks"
assert_arg "--update" "update flag is based on existing .env"
assert_arg "BIND_ADDRESS=0.0.0.0" "update preserves configured bind address"
assert_arg "WEBUI_PORT=3900" "update preserves configured service ports"
assert_arg "WHISPER_PORT=9200" "update preserves configured Whisper port"
assert_arg "LANGFUSE_PORT=3010" "update preserves configured Langfuse port"
assert_arg "ODS_MODE=lemonade" "AMD local install renders its effective mode"

INSTALL_CONFLICT_UPDATE_MODE=false
INSTALL_CONFLICT_ENV_FILE="$INSTALL_DIR/.env"
status="$(run_check)"
assert_status 0 "$status" "explicit fresh mode can render an already-generated .env"
assert_no_arg "--update" "explicit fresh mode overrides post-generation .env presence"
unset INSTALL_CONFLICT_UPDATE_MODE INSTALL_CONFLICT_ENV_FILE

BIND_ADDRESS="127.0.0.1"
BIND_ADDRESS_EXPLICIT=true
status="$(run_check)"
assert_status 0 "$status" "explicit bind override passes"
assert_arg "BIND_ADDRESS=127.0.0.1" "explicit bind override wins over existing .env"

FAKE_DETECTOR_STATUS=1
FORCE=true
INTERACTIVE=false
ODS_ALLOW_LEGACY_PARALLEL=1
ODS_ALLOW_CONFLICTS=""
status="$(run_check)"
assert_status 1 "$status" "--force and non-interactive mode do not bypass conflicts"

ODS_ALLOW_CONFLICTS=1
status="$(run_check)"
assert_status 0 "$status" "explicit conflict override accepts verified conflicts"

FAKE_DETECTOR_STATUS=2
status="$(run_check)"
assert_status 2 "$status" "probe errors fail closed despite conflict override"

FAKE_DETECTOR_STATUS=7
status="$(run_check)"
assert_status 2 "$status" "unexpected detector exits fail closed"

phase06="$ROOT_DIR/installers/phases/06-directories.sh"
grep -qF 'done < <(ods_install_port_defaults)' "$phase06" \
    || fail "phase 06 must resolve the shared installer port defaults"
while IFS='=' read -r port_key _port_default; do
    [[ -n "$port_key" ]] || continue
    if [[ "$port_key" == "WHISPER_PORT" ]]; then
        expected='WHISPER_PORT=${WHISPER_PORT_VALUE}'
    else
        expected="${port_key}=\${${port_key}}"
    fi
    grep -qF "$expected" "$phase06" \
        || fail "phase 06 must persist effective $port_key"
done < <(ods_install_port_defaults)
pass "phase 06 persists every rendered installer port"

echo "Linux install conflict gate checks passed."
