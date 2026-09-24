#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CADDYFILE="$PROJECT_DIR/extensions/services/hermes-proxy/Caddyfile"
AUTH_PAGE="$PROJECT_DIR/extensions/services/hermes-proxy/auth-required/index.html"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

[[ -f "$CADDYFILE" ]] || fail "Hermes proxy Caddyfile not found"
[[ -f "$AUTH_PAGE" ]] || fail "Hermes proxy auth-required page not found"

route_line=$(grep -nE '^[[:space:]]*route[[:space:]]*\{' "$CADDYFILE" | head -n 1 | cut -d: -f1)
health_line=$(grep -nE '^[[:space:]]*@health[[:space:]]+path[[:space:]]+/health' "$CADDYFILE" | head -n 1 | cut -d: -f1)
self_update_line=$(grep -nE '^[[:space:]]*@hermes_self_update[[:space:]]*\{' "$CADDYFILE" | head -n 1 | cut -d: -f1)
forward_auth_line=$(grep -nE '^[[:space:]]*forward_auth[[:space:]]+' "$CADDYFILE" | head -n 1 | cut -d: -f1)

[[ -n "$route_line" ]] || fail "Hermes proxy Caddyfile must use route to preserve handler order"
[[ -n "$health_line" ]] || fail "Hermes proxy health matcher not found"
[[ -n "$self_update_line" ]] || fail "Hermes proxy must block the in-container self-update endpoint"
[[ -n "$forward_auth_line" ]] || fail "Hermes proxy forward_auth not found"
[[ "$route_line" -lt "$health_line" && "$health_line" -lt "$self_update_line" && "$self_update_line" -lt "$forward_auth_line" ]] \
    || fail "Hermes proxy must reject self-update before authenticated upstream forwarding"

grep -Eq '^[[:space:]]*method[[:space:]]+POST([[:space:]]|$)' "$CADDYFILE" \
    || fail "Hermes self-update matcher must be limited to POST"
grep -Eq '^[[:space:]]*path[[:space:]]+/api/hermes/update([[:space:]]|$)' "$CADDYFILE" \
    || fail "Hermes self-update matcher must cover /api/hermes/update"
grep -Eq '^[[:space:]]*respond[[:space:]].*[[:space:]]409([[:space:]]|$)' "$CADDYFILE" \
    || fail "Hermes self-update rejection must return HTTP 409"

grep -Eq '^[[:space:]]*redir[[:space:]]+\*[[:space:]]+/auth/required[[:space:]]+303([[:space:]]*#.*)?$' "$CADDYFILE" \
    || fail "Hermes proxy denied auth response must redirect with an explicit wildcard matcher"

if grep -Eq '^[[:space:]]*redir[[:space:]]+/auth/required[[:space:]]+303([[:space:]]*#.*)?$' "$CADDYFILE"; then
    fail "Hermes proxy redirect is missing the wildcard matcher; Caddy parses the target as a path matcher"
fi

# Health matcher must cover BOTH /health and /healthz. The Docker healthcheck
# uses /health; Kubernetes-style and fleet verify probes use /healthz. Anything
# left out of the matcher falls through to forward_auth and gets bounced to
# /auth/required (303) — health monitors then mark the proxy unhealthy.
grep -Eq '^[[:space:]]*@health[[:space:]]+path([[:space:]]+/[A-Za-z]+)*[[:space:]]+/healthz([[:space:]]|$)' "$CADDYFILE" \
    || fail "Hermes proxy @health matcher must include /healthz (k8s-convention health path)"
grep -Eq '^[[:space:]]*@health[[:space:]]+path([[:space:]]+/[A-Za-z]+)*[[:space:]]+/health([[:space:]]|$)' "$CADDYFILE" \
    || fail "Hermes proxy @health matcher must include /health (Docker healthcheck path)"

echo "[PASS] Hermes proxy auth redirect uses explicit wildcard matcher"
echo "[PASS] Hermes proxy /health and /healthz both anonymous"
echo "[PASS] Hermes proxy blocks the container self-update endpoint"

# Cap request body size so abusive clients can't stream unbounded uploads
# through the Hermes proxy, while still leaving room for agent attachments.
grep -Eq '^[[:space:]]*request_body[[:space:]]*\{' "$CADDYFILE" \
    || fail "Hermes proxy must define a request_body block to cap upload size"
grep -Eq '^[[:space:]]*max_size[[:space:]]+50MB([[:space:]]|$)' "$CADDYFILE" \
    || fail "Hermes proxy request_body must cap at 50MB"
echo "[PASS] Hermes proxy caps request body at 50MB"

grep -q 'Setup / Owner' "$AUTH_PAGE" \
    || fail "Hermes auth-required page should point operators to Setup / Owner"
if grep -Eq 'Invites|scope[[:space:]]+<code>.*all|scope: chat or all' "$AUTH_PAGE"; then
    fail "Hermes auth-required page still contains stale Invites/scope-all copy"
fi
echo "[PASS] Hermes auth-required copy uses owner-card language"
