#!/usr/bin/env bash
# ============================================================================
# Test: scripts/resolve-proxy-config.sh
# ============================================================================
# Drives the resolver against a temp fixture tree and asserts:
#   - one Caddy fragment per service with a valid proxy.subdomain
#   - disabled / missing composes do NOT produce fragments (no 502 routes)
#   - reserved & malformed subdomains are skipped with a warning
#   - stale fragments from previous runs are swept
#   - DREAM_PROXY_EXCLUSIVE=true produces docker-compose.proxy-exclusive.yml
#     listing every proxied service with `ports: []`; unset removes it
#
# Run: bash tests/test-proxy-config.sh
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

pass() { echo "  ok: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

assert_file_exists() {
    local path="$1" label="$2"
    if [[ -f "$path" ]]; then pass "$label exists"
    else fail "$label missing at $path"; fi
}

assert_file_absent() {
    local path="$1" label="$2"
    if [[ ! -f "$path" ]]; then pass "$label absent"
    else fail "$label should not exist at $path"; fi
}

assert_grep() {
    local pattern="$1" file="$2" label="$3"
    if [[ -f "$file" ]] && grep -qE "$pattern" "$file"; then pass "$label"
    else fail "$label (pattern: $pattern, file: $file)"; fi
}

assert_no_grep() {
    local pattern="$1" file="$2" label="$3"
    if [[ ! -f "$file" ]] || ! grep -qE "$pattern" "$file"; then pass "$label"
    else fail "$label (pattern matched: $pattern, file: $file)"; fi
}

write_manifest() {
    local dir="$1" id="$2" port="$3" extra="$4"
    mkdir -p "$dir"
    cat > "$dir/manifest.yaml" <<EOF
schema_version: dream.services.v1
service:
  id: $id
  name: ${id}-name
  container_name: dream-$id
  default_host: $id
  port: $port
  external_port_env: ${id^^}_PORT
  external_port_default: $port
  health: /health
  type: docker
  gpu_backends: [all]
  compose_file: compose.yaml
  category: optional
${extra}
EOF
}

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

EXT_DIR="$TMPROOT/extensions/services"
mkdir -p "$EXT_DIR"

# Valid: compose present, proxy block valid -> fragment expected
write_manifest "$EXT_DIR/alpha" "alpha" "8001" "proxy:
  subdomain: alpha
  exposure: user
  auth: service"
touch "$EXT_DIR/alpha/compose.yaml"

# Valid + client_max_body
write_manifest "$EXT_DIR/beta" "beta" "8002" "proxy:
  subdomain: beta
  exposure: user
  auth: service
  client_max_body: 75MB"
touch "$EXT_DIR/beta/compose.yaml"

# Compose disabled -> NO fragment
write_manifest "$EXT_DIR/disabled" "disabled" "8003" "proxy:
  subdomain: disabled
  exposure: user
  auth: service"
touch "$EXT_DIR/disabled/compose.yaml.disabled"

# Reserved subdomain -> NO fragment (skipped with warning) — checked
# BEFORE the profile filter so the audit fires regardless of profile.
write_manifest "$EXT_DIR/reserve" "reserve" "8004" "proxy:
  subdomain: chat
  exposure: user
  auth: service"
touch "$EXT_DIR/reserve/compose.yaml"

# Malformed subdomain -> NO fragment
write_manifest "$EXT_DIR/bad" "bad" "8005" "proxy:
  subdomain: Bad_Name
  exposure: user
  auth: service"
touch "$EXT_DIR/bad/compose.yaml"

# No proxy block -> NO fragment (silent)
write_manifest "$EXT_DIR/plain" "plain" "8006" ""
touch "$EXT_DIR/plain/compose.yaml"

# Plant a stale fragment from a previous run that should be swept this run.
SITES_D="$TMPROOT/data/dream-proxy/sites.d"
mkdir -p "$SITES_D"
echo "# stale" > "$SITES_D/ghost-service.caddy"

echo "[resolver] basic run (EXCLUSIVE unset)"
unset DREAM_PROXY_EXCLUSIVE
bash "$SCRIPT_DIR/scripts/resolve-proxy-config.sh" --script-dir "$TMPROOT" 2>&1 | grep -v '^WARN' >/dev/null || true

assert_file_exists "$SITES_D/alpha.caddy" "alpha fragment"
assert_file_exists "$SITES_D/beta.caddy"  "beta fragment"
assert_file_absent "$SITES_D/disabled.caddy" "disabled-compose fragment"
assert_file_absent "$SITES_D/reserve.caddy"  "reserved-subdomain fragment"
assert_file_absent "$SITES_D/bad.caddy"      "malformed-subdomain fragment"
assert_file_absent "$SITES_D/plain.caddy"    "no-proxy-block fragment"
assert_file_absent "$SITES_D/ghost-service.caddy" "stale fragment swept"

assert_grep 'http://alpha\.\{\$DREAM_DEVICE_NAME:dream\}\.local' "$SITES_D/alpha.caddy" "alpha hostname template"
assert_grep 'reverse_proxy alpha:8001' "$SITES_D/alpha.caddy" "alpha upstream resolved from default_host:port"
assert_no_grep 'request_body' "$SITES_D/alpha.caddy" "alpha has no body-limit (none configured)"

assert_grep 'request_body' "$SITES_D/beta.caddy" "beta has request_body block"
assert_grep 'max_size 75MB' "$SITES_D/beta.caddy" "beta carries client_max_body value"

echo "[resolver] EXCLUSIVE=true (overlay generated)"
DREAM_PROXY_EXCLUSIVE=true bash "$SCRIPT_DIR/scripts/resolve-proxy-config.sh" --script-dir "$TMPROOT" 2>&1 | grep -v '^WARN' >/dev/null || true
assert_file_exists "$TMPROOT/docker-compose.proxy-exclusive.yml" "exclusive overlay"
assert_grep '^  alpha:' "$TMPROOT/docker-compose.proxy-exclusive.yml" "alpha listed in overlay"
assert_grep '^  beta:'  "$TMPROOT/docker-compose.proxy-exclusive.yml" "beta listed in overlay"
# Compose 2.20+ `!reset null` is what actually replaces the merged ports
# list. A plain `ports: []` would be APPENDED to the base ports list
# (compose merges sequences) — caught by a live `docker compose config`
# test on the dream-2 box; this assertion is the static tripwire.
assert_grep 'ports: !reset null' "$TMPROOT/docker-compose.proxy-exclusive.yml" "ports overridden via !reset"
assert_no_grep '^    ports: \[\]\s*$' "$TMPROOT/docker-compose.proxy-exclusive.yml" "no plain empty-list ports (would silently merge, not override)"
assert_no_grep '^  disabled:' "$TMPROOT/docker-compose.proxy-exclusive.yml" "disabled service NOT in overlay"

echo "[resolver] EXCLUSIVE unset (overlay removed)"
unset DREAM_PROXY_EXCLUSIVE
bash "$SCRIPT_DIR/scripts/resolve-proxy-config.sh" --script-dir "$TMPROOT" 2>&1 | grep -v '^WARN' >/dev/null || true
assert_file_absent "$TMPROOT/docker-compose.proxy-exclusive.yml" "exclusive overlay cleaned up"

# ============================================================================
# Profile + auth-policy assertions
# ============================================================================
# Build a second fixture tree so profile-aware filtering is exercised in
# isolation from the EXCLUSIVE overlay tests above.

POLICY_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT" "$POLICY_ROOT"' EXIT
POLICY_EXT_DIR="$POLICY_ROOT/extensions/services"
mkdir -p "$POLICY_EXT_DIR"
POLICY_SITES_D="$POLICY_ROOT/data/dream-proxy/sites.d"
mkdir -p "$POLICY_SITES_D"

# user/service — should appear under every profile
write_manifest "$POLICY_EXT_DIR/userui" "userui" "9001" "proxy:
  subdomain: userui
  exposure: user
  auth: service"
touch "$POLICY_EXT_DIR/userui/compose.yaml"

# developer-api/none — should appear under developer + all, not under user
write_manifest "$POLICY_EXT_DIR/devapi" "devapi" "9002" "proxy:
  subdomain: devapi
  exposure: developer-api
  auth: none"
touch "$POLICY_EXT_DIR/devapi/compose.yaml"

# internal/service — should appear only under all
write_manifest "$POLICY_EXT_DIR/internal" "internal" "9003" "proxy:
  subdomain: internal
  exposure: internal
  auth: service"
touch "$POLICY_EXT_DIR/internal/compose.yaml"

# user/dream-session — used to exercise forward_auth emission
write_manifest "$POLICY_EXT_DIR/gated" "gated" "9004" "proxy:
  subdomain: gated
  exposure: user
  auth: dream-session"
touch "$POLICY_EXT_DIR/gated/compose.yaml"

# Reserved subdomain — must still be rejected regardless of profile
write_manifest "$POLICY_EXT_DIR/reserved2" "reserved2" "9005" "proxy:
  subdomain: chat
  exposure: developer-api
  auth: none"
touch "$POLICY_EXT_DIR/reserved2/compose.yaml"

# Duplicate subdomain check — claim `userui` again, should be skipped
write_manifest "$POLICY_EXT_DIR/zdup" "zdup" "9006" "proxy:
  subdomain: userui
  exposure: user
  auth: service"
touch "$POLICY_EXT_DIR/zdup/compose.yaml"

echo "[policy] default profile (user) emits only exposure=user"
unset DREAM_PROXY_EXCLUSIVE DREAM_PROXY_PROFILE DREAM_PROXY_ALLOW_UNAUTHENTICATED_USER
rm -f "$POLICY_SITES_D"/*.caddy
bash "$SCRIPT_DIR/scripts/resolve-proxy-config.sh" --script-dir "$POLICY_ROOT" 2>/dev/null || true
assert_file_exists "$POLICY_SITES_D/userui.caddy"  "userui fragment under user profile"
assert_file_absent "$POLICY_SITES_D/devapi.caddy"  "devapi fragment under user profile"
assert_file_absent "$POLICY_SITES_D/internal.caddy" "internal fragment under user profile"
assert_file_absent "$POLICY_SITES_D/reserved2.caddy" "reserved-subdomain fragment under user profile"
assert_grep '^# Profile: user' "$POLICY_SITES_D/userui.caddy" "userui fragment header records profile"

echo "[policy] developer profile adds developer-api routes"
rm -f "$POLICY_SITES_D"/*.caddy
DREAM_PROXY_PROFILE=developer bash "$SCRIPT_DIR/scripts/resolve-proxy-config.sh" --script-dir "$POLICY_ROOT" 2>/dev/null || true
assert_file_exists "$POLICY_SITES_D/userui.caddy"  "userui fragment under developer profile"
assert_file_exists "$POLICY_SITES_D/devapi.caddy"  "devapi fragment under developer profile"
assert_file_absent "$POLICY_SITES_D/internal.caddy" "internal fragment still absent under developer profile"

echo "[policy] all profile adds internal routes"
rm -f "$POLICY_SITES_D"/*.caddy
DREAM_PROXY_PROFILE=all bash "$SCRIPT_DIR/scripts/resolve-proxy-config.sh" --script-dir "$POLICY_ROOT" 2>/dev/null || true
assert_file_exists "$POLICY_SITES_D/internal.caddy" "internal fragment under all profile"

echo "[policy] exposure=user + auth=none is rejected by default"
rm -f "$POLICY_SITES_D"/*.caddy
# Drop a fixture that should trip the safety check.
write_manifest "$POLICY_EXT_DIR/badauth" "badauth" "9007" "proxy:
  subdomain: badauth
  exposure: user
  auth: none"
touch "$POLICY_EXT_DIR/badauth/compose.yaml"
if bash "$SCRIPT_DIR/scripts/resolve-proxy-config.sh" --script-dir "$POLICY_ROOT" >/dev/null 2>&1; then
    fail "resolver should exit non-zero when exposure=user + auth=none without override"
else
    pass "resolver refuses exposure=user + auth=none without override"
fi
DREAM_PROXY_ALLOW_UNAUTHENTICATED_USER=true bash "$SCRIPT_DIR/scripts/resolve-proxy-config.sh" --script-dir "$POLICY_ROOT" 2>/dev/null || true
assert_file_exists "$POLICY_SITES_D/badauth.caddy" "badauth emitted once override is set"
# Remove the badauth fixture so the next assertions are clean.
rm -rf "$POLICY_EXT_DIR/badauth"

echo "[policy] auth=dream-session emits forward_auth pattern"
rm -f "$POLICY_SITES_D"/*.caddy
bash "$SCRIPT_DIR/scripts/resolve-proxy-config.sh" --script-dir "$POLICY_ROOT" 2>/dev/null || true
assert_file_exists "$POLICY_SITES_D/gated.caddy" "gated (auth=dream-session) fragment emitted"
assert_grep 'forward_auth' "$POLICY_SITES_D/gated.caddy" "gated fragment contains forward_auth"
assert_grep '/api/auth/verify-session' "$POLICY_SITES_D/gated.caddy" "gated fragment hits verify-session"
assert_grep '@health path /health /healthz' "$POLICY_SITES_D/gated.caddy" "gated fragment keeps /health public"
assert_grep 'header_up -Sec-Websocket-Key' "$POLICY_SITES_D/gated.caddy" "gated fragment strips WS upgrade headers from auth sub-request"
assert_grep 'redir \* /auth/required 303' "$POLICY_SITES_D/gated.caddy" "gated fragment bounces 401/403 to /auth/required"

echo "[policy] duplicate / reserved subdomains rejected under any profile"
rm -f "$POLICY_SITES_D"/*.caddy
# `zdup` claims the same subdomain as `userui`; resolver should keep the
# first and warn on the second regardless of profile.
DREAM_PROXY_PROFILE=all bash "$SCRIPT_DIR/scripts/resolve-proxy-config.sh" --script-dir "$POLICY_ROOT" 2>/dev/null || true
assert_file_exists "$POLICY_SITES_D/userui.caddy" "first claimant kept on duplicate subdomain"
assert_file_absent "$POLICY_SITES_D/zdup.caddy"    "duplicate claimant skipped"
assert_file_absent "$POLICY_SITES_D/reserved2.caddy" "reserved subdomain skipped even under profile=all"

echo "[policy] EXCLUSIVE=true under user profile only strips emitted routes"
rm -f "$POLICY_SITES_D"/*.caddy
DREAM_PROXY_EXCLUSIVE=true bash "$SCRIPT_DIR/scripts/resolve-proxy-config.sh" --script-dir "$POLICY_ROOT" 2>/dev/null || true
assert_grep '^  userui:' "$POLICY_ROOT/docker-compose.proxy-exclusive.yml" "userui present in proxy-exclusive overlay under user profile"
assert_no_grep '^  devapi:' "$POLICY_ROOT/docker-compose.proxy-exclusive.yml" "devapi NOT in proxy-exclusive overlay under user profile"
assert_no_grep '^  internal:' "$POLICY_ROOT/docker-compose.proxy-exclusive.yml" "internal NOT in proxy-exclusive overlay under user profile"

echo ""
echo "=========================================="
echo "Results: $PASS passed, $FAIL failed"
echo "=========================================="
[[ $FAIL -eq 0 ]]
