#!/bin/bash
# ============================================================================
# ODS CLI update post-verification test
# ============================================================================
# The verification loop in cmd_update counted services with
# ((total_services++)) / ((failed_services++)). Bash post-increment
# evaluates to the old value, so the first increment (0 -> 1) returns
# status 1 and set -e aborts the CLI right there: every `ods update` with
# at least one enabled extension died during "Verifying update..." —
# before persisting the new ODS_VERSION, restarting the host agent, or
# printing "Update complete" — and exited 1 despite a successful update.
#
# Strategy: throwaway INSTALL_DIR fixture (ODS_HOME) with one enabled
# extension, plus docker/curl shims on PATH so pull/up/ps succeed without
# a real daemon, then run the real CLI through `ods update`.
#
# Usage: ./tests/test-cli-update-verification.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-cli-update-verify.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

# ---------------------------------------------------------------------------
# Fixture: minimal install dir plus docker/curl shims
# ---------------------------------------------------------------------------
mkdir -p "$FIXTURE/lib" "$FIXTURE/installers/lib" "$FIXTURE/extensions/services/bsvc" "$FIXTURE/extensions/services/oneshot" "$FIXTURE/bin"
cp "$ROOT_DIR/ods-cli" "$FIXTURE/ods-cli"
cp "$ROOT_DIR"/lib/*.sh "$FIXTURE/lib/"
cp "$ROOT_DIR/installers/lib/compose-images.sh" "$FIXTURE/installers/lib/compose-images.sh"
: > "$FIXTURE/docker-compose.base.yml"

cat > "$FIXTURE/extensions/services/bsvc/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: bsvc
  name: bsvc
  container_name: ods-bsvc
  health: /health
  type: docker
  gpu_backends: [all]
  compose_file: compose.yaml
  category: optional
  depends_on: []
EOF
echo "services: {}" > "$FIXTURE/extensions/services/bsvc/compose.yaml"

cat > "$FIXTURE/extensions/services/oneshot/manifest.yaml" <<'EOF'
schema_version: ods.services.v1
service:
  id: oneshot
  name: oneshot
  container_name: ods-oneshot
  health: ""
  type: docker
  startup_check: false
  gpu_backends: [all]
  compose_file: compose.yaml
  category: optional
  depends_on: []
EOF
echo "services: {}" > "$FIXTURE/extensions/services/oneshot/compose.yaml"

# Update target: installed 2.0.0 (.env) -> target 2.0.1 (manifest.json)
echo '{"ods_version": "2.0.1"}' > "$FIXTURE/manifest.json"

# docker shim: pull/up succeed; `ps` reports the container running unless
# FAKE_DOCKER_PS_EMPTY=1; `info` supplies a CPU count for the budget calc
cat > "$FIXTURE/bin/docker" <<'SH'
#!/usr/bin/env bash
echo "DOCKER_ARGS: $*" >> "${DOCKER_CALL_LOG:?}"
case "${1:-}" in
    compose)
        if [[ "$*" == *"config --format json"* ]]; then
            cat <<'JSON'
{
  "services": {
    "llama-server": {
      "build": {"context": "./extensions/services/llama-server"},
      "image": "ods-lemonade-server:latest"
    },
    "remote-helper": {
      "image": "caddy:2.11.3-alpine"
    }
  }
}
JSON
            exit 0
        fi
        exit 0
        ;;
    pull)
        case "${2:-}" in
            ods-*|ods-*:*|docker.io/library/ods-*)
                echo "unexpected local image pull: ${2:-}" >&2
                exit 42
                ;;
        esac
        exit 0
        ;;
    ps)
        [[ "${FAKE_DOCKER_PS_EMPTY:-}" == "1" ]] && exit 0
        echo "ods-bsvc"
        ;;
    info) echo "8" ;;
esac
exit 0
SH
# curl shim: fail fast so trailing status/health probes don't stall the test
cat > "$FIXTURE/bin/curl" <<'SH'
#!/usr/bin/env bash
echo "curl: (7) Failed to connect" >&2
exit 7
SH
chmod +x "$FIXTURE/bin/docker" "$FIXTURE/bin/curl"

reset_env() {
    cat > "$FIXTURE/.env" <<'EOF'
ODS_VERSION=2.0.0
GPU_BACKEND=nvidia
SHIELD_API_KEY=test-fixture-key
EOF
    : > "$FIXTURE/docker.log"
}

run_update() {
    # Never let a non-zero CLI exit kill the test; callers assert on output/state
    PATH="$FIXTURE/bin:$PATH" ODS_HOME="$FIXTURE" DOCKER_CALL_LOG="$FIXTURE/docker.log" \
        bash "$FIXTURE/ods-cli" update 2>&1 || true
}

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   CLI update verification test                ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# 1. update survives verification with an enabled extension running
# ---------------------------------------------------------------------------
reset_env
output=$(run_update)

if echo "$output" | grep -q "Update complete"; then
    pass "update reaches 'Update complete' past the verification loop"
else
    fail "update aborted during verification: $(echo "$output" | tail -5)"
fi
if grep -q "^ODS_VERSION=2.0.1" "$FIXTURE/.env"; then
    pass "update persisted the new ODS_VERSION"
else
    fail "update did not persist ODS_VERSION: $(grep '^ODS_VERSION=' "$FIXTURE/.env")"
fi
if echo "$output" | grep -q "Update verification failed"; then
    fail "update reported a false verification failure"
else
    pass "update reported no verification failure"
fi
if grep -q 'DOCKER_ARGS: pull caddy:2.11.3-alpine' "$FIXTURE/docker.log" &&
   ! grep -q 'DOCKER_ARGS: pull ods-lemonade-server:latest' "$FIXTURE/docker.log"; then
    pass "update pulls registry images without pulling local build tags"
else
    fail "update did not filter local build tags during pull: $(grep 'DOCKER_ARGS: pull' "$FIXTURE/docker.log" | tr '\n' ';')"
fi

# ---------------------------------------------------------------------------
# 2. a non-running service is counted and reported, not crashed on
# ---------------------------------------------------------------------------
reset_env
export FAKE_DOCKER_PS_EMPTY=1
output=$(run_update)
unset FAKE_DOCKER_PS_EMPTY

if echo "$output" | grep -q "Update verification failed: 1/1 services are not running"; then
    pass "update counts and reports the non-running service"
else
    fail "update did not report the failed-service count: $(echo "$output" | tail -5)"
fi
if echo "$output" | grep -q "Update complete"; then
    fail "update claimed success despite failed verification"
else
    pass "update does not claim success on failed verification"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]] || exit 1
exit 0
