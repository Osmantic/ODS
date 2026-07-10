#!/bin/bash
# Tests for rootless Docker data-directory ownership fix (issue #1702)
# Exercises lib/rootless-fix.sh in a hermetic filesystem simulation.
# No Docker daemon or live install required.
#
# Run: bash ods/tests/test-rootless-docker-ownership.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB="$ROOT_DIR/lib/rootless-fix.sh"
ODS_CLI="$ROOT_DIR/ods-cli"
ODS_PREFLIGHT="$ROOT_DIR/ods-preflight.sh"
ODS_DOCTOR="$ROOT_DIR/scripts/ods-doctor.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

# ── Static checks ─────────────────────────────────────────────────────────────

info "Static: lib/rootless-fix.sh exists and is non-empty"
[[ -f "$LIB" ]] || fail "rootless-fix.sh not found at $LIB"
[[ -s "$LIB" ]] || fail "rootless-fix.sh is empty"
pass "rootless-fix.sh present"

info "Static: ods_is_rootless_docker function defined"
grep -q 'ods_is_rootless_docker()' "$LIB" \
    || fail "ods_is_rootless_docker not found in rootless-fix.sh"
pass "ods_is_rootless_docker defined"

info "Static: ods_fix_rootless_ownership function defined"
grep -q 'ods_fix_rootless_ownership()' "$LIB" \
    || fail "ods_fix_rootless_ownership not found in rootless-fix.sh"
pass "ods_fix_rootless_ownership defined"

info "Static: _ods_rootless_chown_dir function defined"
grep -q '_ods_rootless_chown_dir()' "$LIB" \
    || fail "_ods_rootless_chown_dir not found in rootless-fix.sh"
pass "_ods_rootless_chown_dir defined"

info "Static: _ods_rootless_get_uid covers all affected services"
(
    . "$LIB"
    for svc in n8n whisper tts token-spy privacy-shield openclaw; do
        [[ "$(_ods_rootless_get_uid "$svc")" == "1000" ]] || exit 1
    done
    [[ "$(_ods_rootless_get_uid "ape")" == "100" ]] || exit 1
    [[ "$(_ods_rootless_get_uid "hermes")" == "10000" ]] || exit 1
) || fail "_ods_rootless_get_uid check failed or missing services"
pass "_ods_rootless_get_uid covers all 8 generic services"

info "Static: chown helper uses --user 0:0 (host user in rootless)"
grep -q '\-\-user 0:0' "$LIB" \
    || fail "chown helper does not use --user 0:0"
pass "chown helper uses --user 0:0"

info "Static: chown helper uses alpine container (not sudo)"
grep -q 'alpine' "$LIB" \
    || fail "chown helper does not use alpine container"
# Confirm the chown helper function itself references alpine (not raw chown)
awk '/^_ods_rootless_chown_dir\(\)/,/^}/' "$LIB" | grep -q 'alpine' \
    || fail "_ods_rootless_chown_dir does not use alpine container"
pass "chown uses alpine container (no sudo)"

info "Static: detection uses docker info --format SecurityOptions"
grep -q "SecurityOptions" "$LIB" \
    || fail "rootless detection does not check SecurityOptions"
grep -q "grep -q rootless" "$LIB" \
    || fail "rootless detection does not grep for 'rootless'"
pass "Detection checks SecurityOptions for 'rootless'"

info "Static: fix is no-op when not rootless (guard in ods_fix_rootless_ownership)"
grep -q 'ods_is_rootless_docker' "$LIB" \
    || fail "ods_fix_rootless_ownership does not call ods_is_rootless_docker"
pass "ods_fix_rootless_ownership guards on rootless detection"

info "Static: chown helper is non-fatal on failure (return 0)"
grep -A5 '_ods_rootless_chown_dir()' "$LIB" | grep -q 'return 0' \
    || grep -q 'non-fatal' "$LIB" \
    || fail "_ods_rootless_chown_dir does not handle failure non-fatally"
pass "_ods_rootless_chown_dir handles docker failure non-fatally"

info "Static: 06-directories.sh sources rootless-fix.sh"
grep -q 'rootless-fix.sh' "$ROOT_DIR/installers/phases/06-directories.sh" \
    || fail "06-directories.sh does not source rootless-fix.sh"
pass "06-directories.sh sources rootless-fix.sh"

info "Static: 06-directories.sh calls ods_fix_rootless_ownership"
grep -q 'ods_fix_rootless_ownership' "$ROOT_DIR/installers/phases/06-directories.sh" \
    || fail "06-directories.sh does not call ods_fix_rootless_ownership"
pass "06-directories.sh calls ods_fix_rootless_ownership"

info "Static: ods-preflight.sh warns on rootless mode"
grep -q 'rootless' "$ODS_PREFLIGHT" \
    || fail "ods-preflight.sh does not mention rootless mode"
grep -q 'warn.*rootless\|rootless.*warn' "$ODS_PREFLIGHT" \
    || grep -q 'warn "Docker rootless' "$ODS_PREFLIGHT" \
    || fail "ods-preflight.sh does not call warn() for rootless mode"
pass "ods-preflight.sh warns on rootless mode"

info "Static: ods-doctor.sh detects rootless mode (DOCKER_ROOTLESS)"
grep -q 'DOCKER_ROOTLESS' "$ODS_DOCTOR" \
    || fail "ods-doctor.sh does not define DOCKER_ROOTLESS"
grep -q 'grep -q rootless' "$ODS_DOCTOR" \
    || fail "ods-doctor.sh does not check for rootless in SecurityOptions"
pass "ods-doctor.sh detects rootless mode"

info "Static: ods-doctor.sh includes docker_rootless in JSON report"
grep -q 'docker_rootless' "$ODS_DOCTOR" \
    || fail "ods-doctor.sh does not include docker_rootless in report"
pass "ods-doctor.sh includes docker_rootless in report"

info "Static: ods-doctor.sh emits autofix hint for rootless mode"
grep -q 'ods repair rootless-ownership' "$ODS_DOCTOR" \
    || fail "ods-doctor.sh does not emit 'ods repair rootless-ownership' hint"
pass "ods-doctor.sh emits rootless-ownership autofix hint"

info "Static: ods-cli has rootless-ownership repair sub-command"
grep -q 'rootless-ownership' "$ODS_CLI" \
    || fail "ods-cli does not have rootless-ownership repair sub-command"
pass "ods-cli has rootless-ownership repair sub-command"

info "Static: ods-cli repair rootless-ownership sources rootless-fix.sh"
awk '/rootless-ownership\|rootless\)/,/;;/' "$ODS_CLI" \
    | grep -q 'rootless-fix.sh' \
    || fail "repair rootless-ownership does not source rootless-fix.sh"
pass "repair rootless-ownership sources rootless-fix.sh"

info "Static: ods-cli repair rootless-ownership calls ods_fix_rootless_ownership"
awk '/rootless-ownership\|rootless\)/,/;;/' "$ODS_CLI" \
    | grep -q 'ods_fix_rootless_ownership' \
    || fail "repair rootless-ownership does not call ods_fix_rootless_ownership"
pass "repair rootless-ownership calls ods_fix_rootless_ownership"

info "Static: ods help mentions rootless-ownership"
grep -q 'rootless-ownership' "$ODS_CLI" \
    || fail "ods-cli help does not mention rootless-ownership"
pass "ods-cli help mentions rootless-ownership"

# ── UID map correctness checks ─────────────────────────────────────────────────

info "UID map: n8n UID is 1000 (node user)"
( . "$LIB" && [[ "$(_ods_rootless_get_uid n8n)" == "1000" ]] ) || fail "n8n UID is not 1000"
pass "n8n UID = 1000 (node)"

info "UID map: hermes UID is 10000"
( . "$LIB" && [[ "$(_ods_rootless_get_uid hermes)" == "10000" ]] ) || fail "hermes UID is not 10000"
pass "hermes UID = 10000"

info "UID map: ape UID is 100"
( . "$LIB" && [[ "$(_ods_rootless_get_uid ape)" == "100" ]] ) || fail "ape UID is not 100"
pass "ape UID = 100"

info "UID map: langfuse database UIDs (postgres=70, clickhouse=101)"
grep -q 'chown -R 70:70' "$LIB" || fail "postgres UID 70 not set"
grep -q 'chown -R 101:101' "$LIB" || fail "clickhouse UID 101 not set"
pass "langfuse database UIDs correct (postgres=70, clickhouse=101)"

# ── Filesystem simulation: ods_fix_rootless_ownership logic ────────────────────

info "Filesystem: ods_fix_rootless_ownership iterates all affected dirs"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

INSTALL="$TMP/ods"
mkdir -p "$INSTALL/data/n8n"
mkdir -p "$INSTALL/data/hermes"
mkdir -p "$INSTALL/data/tts"
mkdir -p "$INSTALL/data/whisper"
mkdir -p "$INSTALL/data/langfuse/postgres"
mkdir -p "$INSTALL/data/langfuse/clickhouse"

CHOWN_LOG="$TMP/chown.log"
> "$CHOWN_LOG"

stub_simulate() {
    # shellcheck disable=SC1090
    . "$LIB"
    ods_is_rootless_docker() { return 0; }
    _ods_rootless_chown_dir() {
        local dir="$1" uid="$2"
        [[ -d "$dir" ]] || return 0
        echo "${uid}:${dir##*/}" >> "$CHOWN_LOG"
    }
    ods_fix_rootless_ownership "$INSTALL"
}
stub_simulate

grep -q '^1000:n8n$'        "$CHOWN_LOG" || fail "n8n not chowned (present dir)"
grep -q '^10000:hermes$'    "$CHOWN_LOG" || fail "hermes not chowned (present dir)"
grep -q '^1000:tts$'        "$CHOWN_LOG" || fail "tts not chowned (present dir)"
grep -q '^1000:whisper$'    "$CHOWN_LOG" || fail "whisper not chowned (present dir)"
grep -q '^70:postgres$'     "$CHOWN_LOG" || fail "langfuse/postgres not chowned (present dir)"
grep -q '^101:clickhouse$'  "$CHOWN_LOG" || fail "langfuse/clickhouse not chowned (present dir)"
grep -q ':ape$'             "$CHOWN_LOG" && fail "ape was processed but dir does not exist"
pass "ods_fix_rootless_ownership only processes existing directories and nested database paths"

info "Filesystem: ownership fix is no-op when not rootless"
CHOWN_LOG2="$TMP/chown2.log"
> "$CHOWN_LOG2"

stub_no_rootless() {
    . "$LIB"
    ods_is_rootless_docker() { return 1; }
    _ods_rootless_chown_dir() { echo "${2}:${1##*/}" >> "$CHOWN_LOG2"; }
    ods_fix_rootless_ownership "$INSTALL"
}
stub_no_rootless

[[ ! -s "$CHOWN_LOG2" ]] \
    || fail "ods_fix_rootless_ownership ran chown even when not rootless"
pass "ods_fix_rootless_ownership is a no-op in standard (non-rootless) mode"

# ── Agent network tests ────────────────────────────────────────────────────────

info "Static: ods-cli repair rootless-ownership calls ods_fix_rootless_agent_network"
awk '/rootless-ownership\|rootless\)/,/;;/' "$ODS_CLI" \
    | grep -q 'ods_fix_rootless_agent_network' \
    || fail "repair rootless-ownership does not call ods_fix_rootless_agent_network"
pass "repair rootless-ownership calls ods_fix_rootless_agent_network"

info "Static: ods_fix_rootless_agent_network function defined in lib"
grep -q 'ods_fix_rootless_agent_network()' "$LIB" \
    || fail "ods_fix_rootless_agent_network not found in rootless-fix.sh"
pass "ods_fix_rootless_agent_network defined"

info "Static: agent network fix sets ODS_AGENT_BIND to 0.0.0.0"
grep -q 'ODS_AGENT_BIND.*0\.0\.0\.0\|0\.0\.0\.0.*ODS_AGENT_BIND' "$LIB" \
    || fail "rootless-fix.sh does not set ODS_AGENT_BIND=0.0.0.0"
pass "Agent network fix sets ODS_AGENT_BIND=0.0.0.0"

info "Static: agent network fix sets ODS_AGENT_HOST"
grep -q 'ODS_AGENT_HOST' "$LIB" \
    || fail "rootless-fix.sh does not reference ODS_AGENT_HOST"
pass "Agent network fix handles ODS_AGENT_HOST"

info "Static: agent network fix auto-detects LAN IP"
grep -q '_ods_detect_lan_ip' "$LIB" \
    || fail "rootless-fix.sh does not call _ods_detect_lan_ip"
pass "Agent network fix auto-detects LAN IP"

info "Static: agent network fix does NOT overwrite a user-set IP (idempotency)"
grep -q 'host\.docker\.internal' "$LIB" \
    || fail "rootless-fix.sh does not check for host.docker.internal default"
pass "Agent network fix skips already-set non-default ODS_AGENT_HOST"

info "Static: 06-directories.sh calls ods_fix_rootless_agent_network"
grep -q 'ods_fix_rootless_agent_network' "$ROOT_DIR/installers/phases/06-directories.sh" \
    || fail "06-directories.sh does not call ods_fix_rootless_agent_network"
pass "06-directories.sh calls ods_fix_rootless_agent_network"

info "Static: doctor hint mentions ODS_AGENT_BIND and ODS_AGENT_HOST"
grep -q 'ODS_AGENT_BIND\|ODS_AGENT_HOST' "$ODS_DOCTOR" \
    || fail "ods-doctor.sh autofix hint does not mention ODS_AGENT_BIND/ODS_AGENT_HOST"
pass "Doctor hint mentions ODS_AGENT_BIND and ODS_AGENT_HOST"

# ── Filesystem simulation: ods_fix_rootless_agent_network ─────────────────────

info "Filesystem: ods_fix_rootless_agent_network sets BIND and HOST in .env"
TMP_NET="$(mktemp -d)"
INSTALL_NET="$TMP_NET/ods"
mkdir -p "$INSTALL_NET"
printf "ODS_VERSION=2.5.3\n# ODS_AGENT_BIND=\n# ODS_AGENT_HOST=host.docker.internal\n" \
    > "$INSTALL_NET/.env"

stub_agent_net() {
    # shellcheck disable=SC1090
    . "$LIB"
    ods_is_rootless_docker() { return 0; }   # simulate rootless
    _ods_detect_lan_ip() { echo "192.168.2.22"; }   # stub LAN IP
    ods_fix_rootless_agent_network "$INSTALL_NET"
}
stub_agent_net

grep -q '^ODS_AGENT_BIND=0\.0\.0\.0$' "$INSTALL_NET/.env" \
    || fail "ODS_AGENT_BIND=0.0.0.0 not written to .env (contents: $(cat "$INSTALL_NET/.env"))"
grep -q '^ODS_AGENT_HOST=192\.168\.2\.22$' "$INSTALL_NET/.env" \
    || fail "ODS_AGENT_HOST=192.168.2.22 not written to .env (contents: $(cat "$INSTALL_NET/.env"))"
pass "ods_fix_rootless_agent_network writes BIND=0.0.0.0 and HOST=<LAN IP>"
rm -rf "$TMP_NET"

info "Filesystem: agent network fix is no-op when not rootless"
TMP_NORL="$(mktemp -d)"
INSTALL_NORL="$TMP_NORL/ods"
mkdir -p "$INSTALL_NORL"
printf "ODS_VERSION=2.5.3\n" > "$INSTALL_NORL/.env"

stub_agent_norl() {
    . "$LIB"
    ods_is_rootless_docker() { return 1; }   # not rootless
    _ods_env_set() { echo "UNEXPECTED _ods_env_set call: $*" >&2; exit 1; }
    ods_fix_rootless_agent_network "$INSTALL_NORL"
}
stub_agent_norl

grep -q 'ODS_AGENT_BIND' "$INSTALL_NORL/.env" \
    && fail "ODS_AGENT_BIND was set when Docker is not rootless"
pass "ods_fix_rootless_agent_network is a no-op in non-rootless mode"
rm -rf "$TMP_NORL"

info "Filesystem: agent network fix respects existing manual ODS_AGENT_HOST override"
TMP_MANUAL="$(mktemp -d)"
INSTALL_MANUAL="$TMP_MANUAL/ods"
mkdir -p "$INSTALL_MANUAL"
printf "ODS_VERSION=2.5.3\nODS_AGENT_HOST=10.0.0.5\nODS_AGENT_BIND=0.0.0.0\n" > "$INSTALL_MANUAL/.env"

stub_agent_manual() {
    . "$LIB"
    ods_is_rootless_docker() { return 0; }   # rootless
    _ods_detect_lan_ip() { echo "192.168.2.22"; }   # would change if not guarded
    ods_fix_rootless_agent_network "$INSTALL_MANUAL"
}
stub_agent_manual

grep -q '^ODS_AGENT_HOST=10\.0\.0\.5$' "$INSTALL_MANUAL/.env" \
    || fail "Manually-set ODS_AGENT_HOST=10.0.0.5 was overwritten"
pass "Existing manual ODS_AGENT_HOST is not overwritten"
rm -rf "$TMP_MANUAL"
# ── Integration: phase-06 ordering simulation ──────────────────────────────────

info "Integration: Simulates phase-06 ordering (Fresh Install)"
TMP_PH06_FRESH="$(mktemp -d)"
INSTALL_PH06_FRESH="$TMP_PH06_FRESH/ods"
mkdir -p "$INSTALL_PH06_FRESH"

# 1. Fresh install: No existing .env. The variables get default values:
ODS_AGENT_BIND=""
ODS_AGENT_HOST="host.docker.internal"

# 2. Phase 06 writes the initial .env file:
cat > "$INSTALL_PH06_FRESH/.env" << ENV_EOF
ODS_VERSION=2.5.3
ODS_AGENT_BIND=${ODS_AGENT_BIND}
ODS_AGENT_HOST=${ODS_AGENT_HOST}
ENV_EOF

# 3. Phase 06 calls the rootless helper after .env is written:
stub_ph06_fresh() {
    . "$LIB"
    ods_is_rootless_docker() { return 0; }
    _ods_detect_lan_ip() { echo "192.168.2.22"; }
    ods_fix_rootless_agent_network "$INSTALL_PH06_FRESH"
}
stub_ph06_fresh

# 4. Assert the final .env contains the correct values:
grep -q '^ODS_AGENT_BIND=0\.0\.0\.0$' "$INSTALL_PH06_FRESH/.env" \
    || fail "BIND=0.0.0.0 not written to env"
grep -q '^ODS_AGENT_HOST=192\.168\.2\.22$' "$INSTALL_PH06_FRESH/.env" \
    || fail "HOST=192.168.2.22 not written to env"
pass "Fresh install phase-06 ordering correctly writes rootless settings"
rm -rf "$TMP_PH06_FRESH"

info "Integration: Simulates phase-06 ordering (Upgrade / Reinstall preservation)"
TMP_PH06_UPGRADE="$(mktemp -d)"
INSTALL_PH06_UPGRADE="$TMP_PH06_UPGRADE/ods"
mkdir -p "$INSTALL_PH06_UPGRADE"

# 1. Existing .env exists from previous rootless run:
cat > "$INSTALL_PH06_UPGRADE/.env" << ENV_EOF
ODS_VERSION=2.5.3
ODS_AGENT_BIND=0.0.0.0
ODS_AGENT_HOST=192.168.2.22
ENV_EOF

# 2. Phase 06 runs. It reads existing values:
_env_existing="$INSTALL_PH06_UPGRADE/.env"
_env_get_mock() {
    local key="$1" default="${2:-}"
    local val=""
    if grep -q "^${key}=" "$_env_existing" 2>/dev/null; then
        val=$(grep -m1 "^${key}=" "$_env_existing" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
    fi
    if [[ -n "$val" ]]; then
        echo "$val"
    else
        echo "$default"
    fi
}

ODS_AGENT_BIND=$(_env_get_mock ODS_AGENT_BIND "")
ODS_AGENT_HOST=$(_env_get_mock ODS_AGENT_HOST "host.docker.internal")

# 3. Phase 06 writes the new .env template:
cat > "$INSTALL_PH06_UPGRADE/.env" << ENV_EOF
ODS_VERSION=2.5.3
ODS_AGENT_BIND=${ODS_AGENT_BIND}
ODS_AGENT_HOST=${ODS_AGENT_HOST}
ENV_EOF

# 4. Phase 06 calls the rootless helper after .env is written:
stub_ph06_upgrade() {
    . "$LIB"
    ods_is_rootless_docker() { return 0; }
    _ods_detect_lan_ip() { echo "192.168.2.22"; }
    ods_fix_rootless_agent_network "$INSTALL_PH06_UPGRADE"
}
stub_ph06_upgrade

# 5. Assert the values are fully preserved and survive:
grep -q '^ODS_AGENT_BIND=0\.0\.0\.0$' "$INSTALL_PH06_UPGRADE/.env" \
    || fail "BIND=0.0.0.0 not preserved on upgrade"
grep -q '^ODS_AGENT_HOST=192\.168\.2\.22$' "$INSTALL_PH06_UPGRADE/.env" \
    || fail "HOST=192.168.2.22 not preserved on upgrade"
pass "Upgrade phase-06 ordering correctly preserves rootless settings"
rm -rf "$TMP_PH06_UPGRADE"

echo ""
echo -e "${GREEN}All rootless-docker-ownership tests passed.${NC}"
