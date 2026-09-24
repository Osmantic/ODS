#!/usr/bin/env bash
set -euo pipefail

# `ods status` (macOS CLI) loads .env into ENV_* vars via read_ods_env before
# probing endpoints, but the probe list still hardcodes the Open WebUI and
# Dashboard ports. An install with WEBUI_PORT=4000 in .env reports
# "Chat UI: not responding" while the service is fine on 4000.
#
# Contract: status probes use the ENV_-prefixed vars populated by
# read_ods_env, mirroring the existing ENV_LITELLM_PORT precedent.

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/installers/macos/ods-macos.sh"

fail() {
    printf '[FAIL] %s\n' "$1" >&2
    exit 1
}

grep -Fq 'ENV_WEBUI_PORT' "$script" \
    || fail "status probe ignores WEBUI_PORT from .env"
grep -Fq 'ENV_DASHBOARD_PORT' "$script" \
    || fail "status probe ignores DASHBOARD_PORT from .env"

# The status endpoint array must not keep the literal ports.
if grep -Eq 'ep_urls=.*127\.0\.0\.1:3000|ep_urls=.*127\.0\.0\.1:3001' "$script"; then
    fail "status endpoint array still hardcodes :3000/:3001"
fi

tr -d '\r' < "$script" | bash -n

echo 'macOS status port resolution contract passed'
