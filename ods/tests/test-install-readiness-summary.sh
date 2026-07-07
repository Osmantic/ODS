#!/bin/bash
# ============================================================================
# ODS install readiness summary tests
# ============================================================================
# Behavioral tests for installers/lib/readiness-summary.sh with mocked curl
# and docker commands.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SUMMARY_LIB="$ROOT_DIR/installers/lib/readiness-summary.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

assert_contains() {
    local file="$1" needle="$2" label="$3"
    if grep -Fq -- "$needle" "$file"; then
        pass "$label"
    else
        fail "$label"
        echo "    missing: $needle"
    fi
}

assert_not_contains() {
    local file="$1" needle="$2" label="$3"
    if grep -Fq -- "$needle" "$file"; then
        fail "$label"
        echo "    unexpected: $needle"
    else
        pass "$label"
    fi
}

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

mkdir -p "$TMP_DIR/bin"

cat > "$TMP_DIR/bin/curl" <<'EOF'
#!/usr/bin/env bash
url="${@: -1}"
case "$url" in
  *3001*) printf "200" ;;
  *3000*) printf "000"; exit 7 ;;
  *6333*) printf "000"; exit 7 ;;
  *) printf "404" ;;
esac
EOF
chmod +x "$TMP_DIR/bin/curl"

cat > "$TMP_DIR/bin/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "inspect" ]]; then
  container="${@: -1}"
  case "$container" in
    ods-dashboard) echo "healthy"; exit 0 ;;
    ods-webui) echo "running"; exit 0 ;;
    ods-qdrant) exit 1 ;;
    *) echo "missing"; exit 1 ;;
  esac
fi
exit 0
EOF
chmod +x "$TMP_DIR/bin/docker"

cat > "$TMP_DIR/bin/sudo" <<'EOF'
#!/usr/bin/env bash
[[ -n "${SUDO_MARKER:-}" ]] && printf 'sudo-called\n' >> "$SUDO_MARKER"
exec "$@"
EOF
chmod +x "$TMP_DIR/bin/sudo"

export PATH="$TMP_DIR/bin:$PATH"
export DOCKER_CMD="sudo docker"
export SUDO_MARKER="$TMP_DIR/sudo-marker"
export ODS_COMPOSE_LAUNCH_RECORD="$TMP_DIR/compose-launch.txt"
printf "compose_logs_command=cd '%s' && docker compose logs --tail 200\n" "$TMP_DIR" > "$ODS_COMPOSE_LAUNCH_RECORD"

source "$SUMMARY_LIB"

OUTPUT="$TMP_DIR/readiness.txt"
{
    printf 'Dashboard|http://127.0.0.1:3001|ods-dashboard|http://localhost:3001\n'
    printf 'Chat UI (Open WebUI)|http://127.0.0.1:3000|ods-webui|http://localhost:3000\n'
    printf 'Qdrant|http://127.0.0.1:6333|ods-qdrant|http://localhost:6333\n'
} | ods_readiness_summary "ods status" "/tmp/ods-install.log" "http://localhost:3001" > "$OUTPUT"

echo ""
echo "=== Install readiness summary tests ==="
echo ""

assert_contains "$OUTPUT" "INSTALL READINESS" "summary has heading"
assert_contains "$OUTPUT" "Ready now: 1/3" "summary counts ready services"
assert_contains "$OUTPUT" "[OK] Dashboard" "summary lists ready service"
[[ -s "$SUDO_MARKER" ]] && pass "summary honors DOCKER_CMD wrapper for docker inspect" || fail "summary ignored DOCKER_CMD wrapper"
assert_contains "$OUTPUT" "[!!] Chat UI (Open WebUI)" "summary lists starting service"
assert_contains "$OUTPUT" "starting - HTTP 000" "summary explains starting service"
assert_not_contains "$OUTPUT" "000000" "failed curl probe is normalized to a single 000 code"
assert_contains "$OUTPUT" "[!!] Qdrant" "summary lists missing service"
assert_contains "$OUTPUT" "not detected - missing" "summary explains missing container"
assert_contains "$OUTPUT" "HTTP 000" "summary includes failed HTTP code"
assert_contains "$OUTPUT" "Open dashboard: http://localhost:3001" "summary shows dashboard next step"
assert_contains "$OUTPUT" "Check status: ods status" "summary shows status command"
assert_contains "$OUTPUT" "Logs: /tmp/ods-install.log" "summary shows log path"
assert_contains "$OUTPUT" "Compose launch: $ODS_COMPOSE_LAUNCH_RECORD" "summary shows compose launch record"
assert_contains "$OUTPUT" "Compose logs: cd '$TMP_DIR' && docker compose logs --tail 200" "summary shows compose logs command"

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
