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

echo ""
echo "=========================================="
echo "Results: $PASS passed, $FAIL failed"
echo "=========================================="
[[ $FAIL -eq 0 ]]
