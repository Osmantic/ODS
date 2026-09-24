#!/usr/bin/env bash
set -euo pipefail

# The install summary must show the ports the operator actually configured.
# WEBUI_PORT / DASHBOARD_PORT are documented .env overrides and resolved into
# SERVICE_PORTS at the top of phase 13; surfaces that still print literal
# 3000/3001 send users to the wrong URL after a customized install.

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
summary="$root/installers/phases/13-summary.sh"
ui="$root/installers/lib/ui.sh"

fail() {
    printf '[FAIL] %s\n' "$1" >&2
    exit 1
}

# Success card must be fed the resolved ports, not literal 3000/3001.
if grep -Fq 'show_success_card "http://localhost:3000" "http://localhost:3001"' "$summary"; then
    fail "success card still prints literal ports"
fi
grep -Fq 'SERVICE_PORTS[open-webui]' "$summary" \
    || fail "success card does not use the resolved Open WebUI port"
grep -Fq 'SERVICE_PORTS[dashboard]' "$summary" \
    || fail "success card does not use the resolved Dashboard port"

# The desktop shortcut port is intentionally not asserted here: PR #2917
# already fixes that line; this PR covers the remaining surfaces.

# The LAN URL printed inside the card must not assume port 3001 either.
if grep -Eq '":3001"|3001' "$ui" | grep -F 'ip_addr' >/dev/null 2>&1 \
    || grep -Eq '\$\{?ip_addr\}?:3001' "$ui"; then
    fail "success card LAN URL still hardcodes port 3001"
fi

tr -d '\r' < "$summary" | bash -n
tr -d '\r' < "$ui" | bash -n

echo 'Install summary port surface contract passed'
