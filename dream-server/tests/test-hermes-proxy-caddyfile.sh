#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CADDYFILE="$PROJECT_DIR/extensions/services/hermes-proxy/Caddyfile"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

[[ -f "$CADDYFILE" ]] || fail "Hermes proxy Caddyfile not found"

route_line=$(grep -nE '^[[:space:]]*route[[:space:]]*\{' "$CADDYFILE" | head -n 1 | cut -d: -f1)
health_line=$(grep -nE '^[[:space:]]*@health[[:space:]]+path[[:space:]]+/health' "$CADDYFILE" | head -n 1 | cut -d: -f1)
forward_auth_line=$(grep -nE '^[[:space:]]*forward_auth[[:space:]]+' "$CADDYFILE" | head -n 1 | cut -d: -f1)

[[ -n "$route_line" ]] || fail "Hermes proxy Caddyfile must use route to preserve handler order"
[[ -n "$health_line" ]] || fail "Hermes proxy health matcher not found"
[[ -n "$forward_auth_line" ]] || fail "Hermes proxy forward_auth not found"
[[ "$route_line" -lt "$health_line" && "$health_line" -lt "$forward_auth_line" ]] \
    || fail "Hermes proxy route block must put anonymous health handling before forward_auth"

grep -Eq '^[[:space:]]*redir[[:space:]]+\*[[:space:]]+/auth/required[[:space:]]+303([[:space:]]*#.*)?$' "$CADDYFILE" \
    || fail "Hermes proxy denied auth response must redirect with an explicit wildcard matcher"

if grep -Eq '^[[:space:]]*redir[[:space:]]+/auth/required[[:space:]]+303([[:space:]]*#.*)?$' "$CADDYFILE"; then
    fail "Hermes proxy redirect is missing the wildcard matcher; Caddy parses the target as a path matcher"
fi

# Health matcher must cover BOTH /health and /healthz. The Docker healthcheck
# uses /health; k8s and dream-fleet-test verify probes use /healthz. Anything
# left out of the matcher falls through to forward_auth and gets bounced to
# /auth/required (303) — health monitors then mark the proxy unhealthy.
grep -Eq '^[[:space:]]*@health[[:space:]]+path([[:space:]]+/[A-Za-z]+)*[[:space:]]+/healthz([[:space:]]|$)' "$CADDYFILE" \
    || fail "Hermes proxy @health matcher must include /healthz (k8s-convention health path)"
grep -Eq '^[[:space:]]*@health[[:space:]]+path([[:space:]]+/[A-Za-z]+)*[[:space:]]+/health([[:space:]]|$)' "$CADDYFILE" \
    || fail "Hermes proxy @health matcher must include /health (Docker healthcheck path)"

echo "[PASS] Hermes proxy auth redirect uses explicit wildcard matcher"
echo "[PASS] Hermes proxy /health and /healthz both anonymous"
