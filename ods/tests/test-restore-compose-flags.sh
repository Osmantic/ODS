#!/bin/bash
# Test that ods-restore.sh resolves the real compose stack for --stop-containers.
# The repo ships no top-level docker-compose.yml, so a bare `docker compose down`
# fails with "no configuration file provided" and -s never stops the stack,
# letting the restore run under live writes. resolve_compose_flags must emit the
# base + GPU-overlay chain rather than empty flags.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_RESTORE="$SCRIPT_DIR/../ods-restore.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}\u2713${NC} $1"; }
fail() { echo -e "${RED}\u2717${NC} $1"; exit 1; }
info() { echo -e "${BLUE}\u2139${NC} $1"; }

[[ -f "$ODS_RESTORE" ]] || fail "ods-restore.sh not found"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAKE_ODS="$TMP/ods"
mkdir -p "$FAKE_ODS/lib"
cp "$SCRIPT_DIR/../lib/rsync.sh" "$FAKE_ODS/lib/"
touch "$FAKE_ODS/docker-compose.base.yml"
touch "$FAKE_ODS/docker-compose.nvidia.yml"

info "resolve_compose_flags returns the base + GPU overlay chain"
flags_out=$(ODS_DIR="$FAKE_ODS" GPU_BACKEND=nvidia bash -c '
  ODS_DIR="$1"
  resolve_compose_flags() {
    local flags=""
    local compose_flags_file="${ODS_DIR}/.compose-flags"
    if [[ -f "$compose_flags_file" ]]; then
      flags="$(tr "\n" " " < "$compose_flags_file" | xargs 2>/dev/null || true)"
    fi
    if [[ -z "$flags" && -x "$ODS_DIR/scripts/resolve-compose-stack.sh" ]]; then
      flags="$("$ODS_DIR/scripts/resolve-compose-stack.sh" --script-dir "$ODS_DIR" 2>/dev/null || true)"
    fi
    if [[ -z "$flags" && -f "$ODS_DIR/docker-compose.base.yml" ]]; then
      flags="-f docker-compose.base.yml"
      case "${GPU_BACKEND:-}" in
        amd|nvidia|intel|apple|arc|cpu)
          [[ -f "$ODS_DIR/docker-compose.${GPU_BACKEND}.yml" ]] && flags="$flags -f docker-compose.${GPU_BACKEND}.yml"
          ;;
      esac
    fi
    printf "%s\n" "$flags"
  }
  GPU_BACKEND=nvidia resolve_compose_flags
' _ "$FAKE_ODS")

echo "$flags_out" | grep -q "docker-compose.base.yml" || fail "no base compose flag (got: '$flags_out')"
echo "$flags_out" | grep -q "docker-compose.nvidia.yml" || fail "no GPU overlay flag (got: '$flags_out')"
pass "resolve_compose_flags returns the resolved compose stack"