#!/usr/bin/env bash
# The install summary's success card and the ods.desktop launcher must open
# the configured Dashboard port (DASHBOARD_PORT -> SERVICE_PORTS[dashboard]),
# as the readiness check and text summary in the same phase already do.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUMMARY="$ROOT/installers/phases/13-summary.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

card_block="$(awk '
    /^    _summary_chat_url=""$/ { emit = 1 }
    emit { print }
    emit && /^    unset _summary_chat_url / { exit }
' "$SUMMARY")"
[[ -n "$card_block" ]] || fail "could not extract the success card block"
desktop_block="$(awk '
    /^    cat > "\$DESKTOP_FILE" << DESKTOP_EOF$/ { emit = 1 }
    emit { print }
    emit && /^DESKTOP_EOF$/ { exit }
' "$SUMMARY")"
[[ -n "$desktop_block" ]] || fail "could not extract the desktop entry block"

show_success_card() { printf '%s\n' "$2" > "$TMP_DIR/card-dashboard-url"; }

# The eval'd phase blocks read these.
# shellcheck disable=SC2034
run_summary() {
    INSTALL_DIR="$TMP_DIR"
    LOCAL_IP=""
    ENABLE_OPEN_WEBUI=true
    DESKTOP_FILE="$TMP_DIR/ods.desktop"
    : > "$INSTALL_DIR/.env"
    eval "$card_block"
    eval "$desktop_block"
}

declare -A SERVICE_PORTS=([dashboard]=3101 [open-webui]=3000)
run_summary
[[ "$(cat "$TMP_DIR/card-dashboard-url")" == "http://localhost:3101" ]] \
    || fail "success card links $(cat "$TMP_DIR/card-dashboard-url"), expected http://localhost:3101"
grep -qx 'Exec=xdg-open http://localhost:3101' "$TMP_DIR/ods.desktop" \
    || fail "ods.desktop opens $(grep '^Exec=' "$TMP_DIR/ods.desktop"), expected port 3101"

# shellcheck disable=SC2034
SERVICE_PORTS=()
run_summary
[[ "$(cat "$TMP_DIR/card-dashboard-url")" == "http://localhost:3001" ]] \
    || fail "success card without a configured port links $(cat "$TMP_DIR/card-dashboard-url")"
grep -qx 'Exec=xdg-open http://localhost:3001' "$TMP_DIR/ods.desktop" \
    || fail "ods.desktop without a configured port opens $(grep '^Exec=' "$TMP_DIR/ods.desktop")"

echo "PASS: success card and desktop launcher use the configured Dashboard port"
