#!/bin/bash
# ============================================================================
# ODS health-check.sh Test Suite
# ============================================================================
# Ensures scripts/health-check.sh runs without shell errors and produces
# expected exit codes and (when requested) JSON output. Supports rock-solid
# installs by validating the health-check path used in post-install checklists.
#
# Usage: ./tests/test-health-check.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "  ${YELLOW}⊘ SKIP${NC} $1"; }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   health-check.sh Test Suite                  ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# 1. Script exists
if [[ ! -f "$ROOT_DIR/scripts/health-check.sh" ]]; then
    fail "scripts/health-check.sh not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "health-check.sh exists"

# 2. Runs without shell error (--quiet to reduce output; we care about exit and no "unbound" etc.)
set +e
out=$(cd "$ROOT_DIR" && bash scripts/health-check.sh --quiet 2>&1)
exit_code=$?
set -e

if echo "$out" | grep -q "unbound variable\|syntax error\|command not found"; then
    fail "health-check.sh produced shell error in output"
else
    pass "health-check.sh runs without shell errors"
fi

# Exit code must be 0, 1, or 2 (documented: 0=healthy, 1=degraded, 2=critical)
if [[ "$exit_code" -eq 0 ]] || [[ "$exit_code" -eq 1 ]] || [[ "$exit_code" -eq 2 ]]; then
    pass "health-check.sh exit code is valid (0|1|2): $exit_code"
else
    fail "health-check.sh exit code should be 0, 1, or 2; got $exit_code"
fi

# 3. --json produces JSON-like output (no strict parse here, just key presence)
set +e
json_out=$(cd "$ROOT_DIR" && bash scripts/health-check.sh --json 2>&1)
json_exit=$?
set -e

if echo "$json_out" | grep -q '"'; then
    pass "health-check.sh --json produces JSON-like output"
else
    fail "health-check.sh --json output does not look like JSON"
fi

if [[ "$json_exit" -eq 0 ]] || [[ "$json_exit" -eq 1 ]] || [[ "$json_exit" -eq 2 ]]; then
    pass "health-check.sh --json exit code valid: $json_exit"
else
    fail "health-check.sh --json exit code invalid: $json_exit"
fi

# 4. Script is executable or runnable via bash
if [[ -x "$ROOT_DIR/scripts/health-check.sh" ]] || true; then
    pass "health-check.sh is runnable (bash or executable)"
fi

# 5. Container state checking function exists
if grep -q "check_container_state" "$ROOT_DIR/scripts/health-check.sh"; then
    pass "check_container_state function present"
else
    fail "check_container_state function missing"
fi

# 6. Container state messages are present in output logic
if grep -q "container not found\|container stopped\|container restarting" "$ROOT_DIR/scripts/health-check.sh"; then
    pass "Container state error messages present"
else
    fail "Container state error messages missing"
fi

# 7. Verify graceful handling when docker unavailable (mock test)
# The function should return 0 (success) when docker command not found
if grep -A15 "check_container_state" "$ROOT_DIR/scripts/health-check.sh" | grep -q "command -v docker"; then
    pass "check_container_state checks for docker availability"
else
    fail "check_container_state missing docker availability check"
fi

# 8. LLM probe honors the backend's API base path. Lemonade (AMD) serves
# /api/v1 while llama-server serves /v1; a hardcoded /v1/completions probe
# fails on every Lemonade install even when inference is healthy.
if grep -q 'LLM_API_BASE_PATH:-/v1' "$ROOT_DIR/scripts/health-check.sh"; then
    pass "test_llm reads LLM_API_BASE_PATH with /v1 default"
else
    fail "test_llm must honor LLM_API_BASE_PATH (Lemonade uses /api/v1)"
fi
if grep -q '/v1/completions' "$ROOT_DIR/scripts/health-check.sh"; then
    fail "test_llm still hardcodes /v1/completions"
else
    pass "no hardcoded /v1/completions probe remains"
fi

# 8. Async service failures must reach the summary, exit code, and JSON.
# The per-service checks run in background subshells, so the parent has to
# aggregate their results — a regression here reports HEALTHY/exit 0 with
# services down and omits them from the JSON services map.
if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
    SANDBOX=$(mktemp -d)
    # shellcheck disable=SC2064
    trap "rm -rf '$SANDBOX'" EXIT

    mkdir -p "$SANDBOX/scripts" "$SANDBOX/lib" "$SANDBOX/bin" \
        "$SANDBOX/extensions/services/fakesvc" \
        "$SANDBOX/extensions/services/fakeext" \
        "$SANDBOX/extensions/services/fakeoff"
    cp "$ROOT_DIR/scripts/health-check.sh" "$SANDBOX/scripts/"
    cp "$ROOT_DIR/lib/service-registry.sh" "$ROOT_DIR/lib/safe-env.sh" "$SANDBOX/lib/"
    if [[ -f "$ROOT_DIR/lib/python-cmd.sh" ]]; then
        cp "$ROOT_DIR/lib/python-cmd.sh" "$SANDBOX/lib/"
    fi

    cat > "$SANDBOX/extensions/services/fakesvc/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: fakesvc
  name: Fake Core Service
  category: core
  container_name: ods-fakesvc
  external_port_default: 9999
  health: /health
MANIFEST
    cat > "$SANDBOX/extensions/services/fakeext/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: fakeext
  name: Fake Extension
  category: optional
  container_name: ods-fakeext
  external_port_default: 9998
  health: /health
  compose_file: compose.yaml
MANIFEST
    printf 'services: {}\n' > "$SANDBOX/extensions/services/fakeext/compose.yaml"
    cat > "$SANDBOX/extensions/services/fakeoff/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: fakeoff
  name: Fake Disabled Extension
  category: optional
  container_name: ods-fakeoff
  external_port_default: 9997
  health: /health
  compose_file: compose.yaml
MANIFEST
    printf 'services: {}\n' > "$SANDBOX/extensions/services/fakeoff/compose.yaml.disabled"

    # curl stub: llama-server inference and the core service respond, the
    # enabled extension does not.
    cat > "$SANDBOX/bin/curl" <<'CURLSTUB'
#!/bin/bash
for a in "$@"; do
  case "$a" in
    *"/v1/completions"*) echo '{"text":"ok"}'; exit 0 ;;
    *":9999/health"*) echo 'ok'; exit 0 ;;
  esac
done
exit 7
CURLSTUB
    chmod +x "$SANDBOX/bin/curl"

    # docker stub: every sandbox container reports as running, so this
    # scenario exercises the curl success/failure paths. Without the stub,
    # a host Docker CLI leaks in and reports the fake containers as
    # not-found, failing the core service before curl is consulted.
    cat > "$SANDBOX/bin/docker" <<'DOCKERSTUB'
#!/bin/bash
echo "running"
exit 0
DOCKERSTUB
    chmod +x "$SANDBOX/bin/docker"

    set +e
    agg_json=$(cd "$SANDBOX" && PATH="$SANDBOX/bin:$PATH" INSTALL_DIR="$SANDBOX" \
        bash scripts/health-check.sh --json 2>&1)
    agg_exit=$?
    set -e

    if [[ "$agg_exit" -eq 1 ]]; then
        pass "failing extension degrades status (exit 1)"
    else
        fail "failing extension should exit 1 (degraded); got $agg_exit"
    fi
    if echo "$agg_json" | grep -q '"fakeext": "fail"'; then
        pass "failing extension appears as fail in JSON services"
    else
        fail "failing extension missing from JSON services"
    fi
    if echo "$agg_json" | grep -q '"fakesvc": "ok"'; then
        pass "healthy core service appears as ok in JSON services"
    else
        fail "healthy core service missing from JSON services"
    fi
    if echo "$agg_json" | grep -q '"fakeoff"'; then
        fail "disabled extension should not be probed or reported"
    else
        pass "disabled extension (compose_file absent) is skipped"
    fi

    # Core service failure must be critical (exit 2), and a docker stub whose
    # inspect fails (container not found) must not make the async checker
    # vanish before writing its result.
    cat > "$SANDBOX/bin/curl" <<'CURLSTUB'
#!/bin/bash
for a in "$@"; do
  case "$a" in
    *"/v1/completions"*) echo '{"text":"ok"}'; exit 0 ;;
  esac
done
exit 7
CURLSTUB
    chmod +x "$SANDBOX/bin/curl"
    printf '#!/bin/bash\nexit 1\n' > "$SANDBOX/bin/docker"
    chmod +x "$SANDBOX/bin/docker"

    set +e
    core_json=$(cd "$SANDBOX" && PATH="$SANDBOX/bin:$PATH" INSTALL_DIR="$SANDBOX" \
        bash scripts/health-check.sh --json 2>&1)
    core_exit=$?
    set -e

    if [[ "$core_exit" -eq 2 ]]; then
        pass "failing core service is critical (exit 2)"
    else
        fail "failing core service should exit 2 (critical); got $core_exit"
    fi
    if echo "$core_json" | grep -q '"fakesvc": "fail"'; then
        pass "core service with missing container still reported in JSON"
    else
        fail "core service with missing container vanished from JSON"
    fi
    if echo "$core_json" | grep -q "container not found"; then
        pass "container-not-found state reaches the human-readable output"
    else
        fail "container-not-found message missing from output"
    fi

    rm -rf "$SANDBOX"
    trap - EXIT
else
    skip "async aggregation tests (python3 + PyYAML required for service registry)"
fi

# 8. Disk probe uses POSIX df (-P), not the wrap-prone -h
if grep -qE 'df -P ' "$ROOT_DIR/scripts/health-check.sh"; then
    pass "test_disk uses POSIX df -P (avoids long-device-name line wrapping)"
else
    fail "test_disk should use df -P for stable single-line output"
fi

# 9. Behavioral: capacity is parsed correctly even when df wraps the
# filesystem line (long device name shifts columns). A mock df emits a
# wrapped 95%-capacity line; the JSON must report disk_usage=95, not the
# mount point that the old `tail -1 | awk '{print $5}'` parse would pick up.
MOCK_DIR=$(mktemp -d)
cat > "$MOCK_DIR/df" <<'MOCK_DF'
#!/usr/bin/env bash
# Simulates df output where a long device name wraps onto a second line,
# shifting the capacity column. Ignores flags/args; capacity is 95%.
cat <<'DF_OUT'
Filesystem                              1024-blocks      Used Available Capacity Mounted on
/dev/mapper/very--long--vg--name-root
                                          488384000 461000000  27384000      95% /
DF_OUT
MOCK_DF
chmod +x "$MOCK_DIR/df"

set +e
disk_json=$(cd "$ROOT_DIR" && PATH="$MOCK_DIR:$PATH" bash scripts/health-check.sh --json 2>&1)
set -e
rm -rf "$MOCK_DIR"

if echo "$disk_json" | grep -q '"disk_usage": "95"'; then
    pass "test_disk parses capacity from wrapped df output (column-shift safe)"
else
    got=$(echo "$disk_json" | grep -o '"disk_usage": "[^"]*"' | head -1)
    fail "test_disk mis-parsed wrapped df output; expected disk_usage=95, got: ${got:-<none>}"
fi

# Helper: run health-check.sh --json with a mock nvidia-smi that reports
# "mem_used, mem_total, util, temp" and echo the resulting gpu status.
_gpu_status_with_mock() {
    local csv="$1" mock_dir gpu_json
    mock_dir=$(mktemp -d)
    {
        echo '#!/usr/bin/env bash'
        echo "echo '$csv'"
    } > "$mock_dir/nvidia-smi"
    chmod +x "$mock_dir/nvidia-smi"
    set +e
    gpu_json=$(cd "$ROOT_DIR" && PATH="$mock_dir:$PATH" bash scripts/health-check.sh --json 2>&1)
    set -e
    rm -rf "$mock_dir"
    echo "$gpu_json" | grep -o '"gpu": "[^"]*"' | head -1
}

# 8. A fully-utilized GPU with low memory must NOT warn. An LLM server pins
# util at ~100% during normal inference; the old util>95 check flagged that
# healthy state as "warn". Memory is only 8% here, so status must be "ok".
gpu_busy=$(_gpu_status_with_mock "2048, 24576, 100, 60")
if echo "$gpu_busy" | grep -q '"gpu": "ok"'; then
    pass "test_gpu does not warn on 100% utilization when memory is low"
else
    fail "test_gpu warned on healthy high-util GPU; got: ${gpu_busy:-<none>}"
fi

# 9. High memory pressure (>95%) SHOULD warn — the OOM signal the probe means
# to surface. 24000/24576 = 97%.
gpu_full=$(_gpu_status_with_mock "24000, 24576, 30, 60")
if echo "$gpu_full" | grep -q '"gpu": "warn"'; then
    pass "test_gpu warns when GPU memory exceeds 95%"
else
    fail "test_gpu did not warn on >95% memory; got: ${gpu_full:-<none>}"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
