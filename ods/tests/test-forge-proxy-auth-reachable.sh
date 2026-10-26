#!/usr/bin/env bash
# The ai-dock Forge image fronts the WebUI with a Caddy proxy on FORGE_PORT_HOST
# (7861). With WEB_ENABLE_AUTH unset (upstream default: auth on), every
# unauthenticated request is redirected to the serviceportal login on :1111.
# ODS disables serviceportal via SUPERVISOR_NO_AUTOSTART and never publishes
# 1111, so the UI and the --api endpoints are unreachable with defaults while
# the curl healthcheck (which accepts the 302) still reports healthy.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${ODS_FORGE_COMPOSE_UNDER_TEST:-$ROOT_DIR/extensions/library/services/forge/compose.yaml}"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$COMPOSE" ]] || fail "missing $COMPOSE"

grep -qE '^\s*-\s*SUPERVISOR_NO_AUTOSTART=.*serviceportal' "$COMPOSE" \
    || fail "precondition changed: serviceportal is no longer disabled; revisit this contract"
grep -qE '^\s*-\s*"?127\.0\.0\.1:\$\{FORGE_PORT:-7861\}:7861"?' "$COMPOSE" \
    || fail "Forge port is not bound to 127.0.0.1; disabling proxy auth would expose it"
pass "serviceportal is disabled and Forge is loopback-only"

grep -qE '^\s*-\s*WEB_ENABLE_AUTH=false\s*$' "$COMPOSE" \
    || fail "WEB_ENABLE_AUTH is not false: Caddy redirects every request to the disabled serviceportal login"
pass "Forge proxy serves the WebUI without the unreachable serviceportal login"
