#!/usr/bin/env bash
# Phase 06 must write the installer tier to .env as TIER. ods-cli
# (enable/disable), the host agent and ods-update read it back and pass it to
# resolve-compose-stack.sh; without it they resolve tier 1 and drop the tier 0
# memory overlay (docker-compose.tier0.yml) that the installer applied.
# Renders phase 06's shipped template line, loads the result the way ods-cli
# does, and runs the real resolver with that tier.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/06-directories.sh"
# shellcheck source=../lib/safe-env.sh
. "$ROOT_DIR/lib/safe-env.sh"
# shellcheck source=../lib/dotenv-quote.sh
. "$ROOT_DIR/lib/dotenv-quote.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf '[PASS] %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '[FAIL] %s\n' "$1" >&2; }

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

template="$(awk '
    /cat > "\$INSTALL_DIR\/.env" << ENV_EOF/ { body = 1; next }
    body && $0 == "ENV_EOF" { exit }
    body && /^TIER=/ { print }
' "$PHASE")"
[[ -n "$template" ]] || { fail "phase 06 does not write TIER to .env"; printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"; exit 1; }

# Usage: resolved_overlays INSTALLER_TIER -> the compose files ods-cli would resolve
resolved_overlays() {
    (
        TIER="$1"
        eval "printf '%s\n' \"$template\"" > "$tmp_dir/.env"
        unset TIER
        load_env_file "$tmp_dir/.env"
        bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" --script-dir "$ROOT_DIR" \
            --tier "${TIER:-1}" --gpu-backend cpu
    )
}

for tier in 0 T0; do
    overlays="$(resolved_overlays "$tier")"
    [[ "$overlays" == *docker-compose.tier0.yml* ]] \
        && pass "tier $tier keeps the tier 0 memory overlay after .env is read back" \
        || fail "tier $tier lost docker-compose.tier0.yml: $overlays"
done

overlays="$(resolved_overlays 2)"
[[ "$overlays" != *docker-compose.tier0.yml* ]] \
    && pass "tier 2 does not get the tier 0 memory overlay" \
    || fail "tier 2 resolved docker-compose.tier0.yml: $overlays"

TIER=SH_LARGE
[[ "$(eval "printf '%s\n' \"$template\"")" == "TIER=SH_LARGE" ]] \
    && pass "named tiers are written unquoted" \
    || fail "named tier rendered as: $(eval "printf '%s\n' \"$template\"")"

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
