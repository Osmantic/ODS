#!/usr/bin/env bash
# ods-macos.sh resolves the stack again when the owner adds
# a permitted docker-compose.override.yml after install:
# the installer's .compose-flags predates it, so start/restart ignored it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="${ODS_MACOS_CLI_UNDER_TEST:-$ROOT/installers/macos/ods-macos.sh}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
FAILURES=0
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; FAILURES=$((FAILURES + 1)); }

definition="$(awk '$0 == "_get_base_compose_flags() {" { copy=1 } copy { print } copy && /^}$/ { exit }' "$CLI")"
[[ -n "$definition" ]] || { echo "[FAIL] _get_base_compose_flags not found in $CLI" >&2; exit 1; }
eval "$definition"
ensure_hermes_dashboard_session_token() { :; }
macos_model_store_compose_flags() { printf '%s\n' "$1"; }
read_env_value() { :; }

INSTALL_DIR="$tmp/install"
mkdir -p "$INSTALL_DIR/scripts"
printf '#!/usr/bin/env bash\necho RESOLVED\n' > "$INSTALL_DIR/scripts/resolve-compose-stack.sh"
chmod +x "$INSTALL_DIR/scripts/resolve-compose-stack.sh"
cache="-f docker-compose.base.yml -f installers/macos/docker-compose.macos.yml"
printf '%s\n' "$cache" > "$INSTALL_DIR/.compose-flags"

[[ "$(_get_base_compose_flags)" == "$cache" ]] \
    && pass "without an override the saved flags are used" \
    || fail "saved flags were not used without an override"

printf 'services:\n  custom-service:\n    image: python:3.12-slim\n' > "$INSTALL_DIR/docker-compose.override.yml"
[[ "$(_get_base_compose_flags)" == "RESOLVED" ]] \
    && pass "an override added after install resolves the stack again" \
    || fail "an override added after install was ignored: $(_get_base_compose_flags)"

printf '%s\n' "$cache -f docker-compose.override.yml" > "$INSTALL_DIR/.compose-flags"
[[ "$(_get_base_compose_flags)" == "$cache -f docker-compose.override.yml" ]] \
    && pass "a cache that already lists the override is kept" \
    || fail "a cache listing the override was not kept"

# Exercise the production resolver through the cache-miss path as well. A
# stub alone would incorrectly suggest that protected core overrides work.
INSTALL_DIR="$tmp/real-install"
mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/installers/macos" "$INSTALL_DIR/config"
printf 'services: {}\n' > "$INSTALL_DIR/docker-compose.base.yml"
printf 'services: {}\n' > "$INSTALL_DIR/installers/macos/docker-compose.macos.yml"
cp "$ROOT/config/core-service-ids.json" "$INSTALL_DIR/config/"
printf '#!/usr/bin/env bash\nexec bash %q "$@"\n' "$ROOT/scripts/resolve-compose-stack.sh" \
    > "$INSTALL_DIR/scripts/resolve-compose-stack.sh"
chmod +x "$INSTALL_DIR/scripts/resolve-compose-stack.sh"
printf '%s\n' "$cache" > "$INSTALL_DIR/.compose-flags"
cat > "$INSTALL_DIR/docker-compose.override.yml" <<'YAML'
services:
  custom-service:
    image: python:3.12-slim
    user: "1000:1000"
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    command: ["python", "-c", "import time; time.sleep(60)"]
YAML
resolved="$(_get_base_compose_flags)"
[[ "$resolved" == *"-f docker-compose.override.yml"* ]] \
    && pass "the real resolver includes a permitted override added after install" \
    || fail "the real resolver did not include the permitted override: $resolved"
[[ "$(cat "$INSTALL_DIR/.compose-flags")" == "$cache" ]] \
    || fail "resolving the override changed the saved flags"

printf 'services:\n  n8n:\n    profiles: [disabled]\n' > "$INSTALL_DIR/docker-compose.override.yml"
resolved="$(_get_base_compose_flags 2>"$tmp/core-refusal.log")"
[[ "$resolved" != *"docker-compose.override.yml"* ]] \
    && pass "the real resolver still rejects protected core overrides" \
    || fail "protected core override was included"
grep -q "collides with a built-in core service name" "$tmp/core-refusal.log" \
    || fail "protected core override did not explain its refusal"

[[ "$FAILURES" == 0 ]] || exit 1
echo "All macOS Compose override cache checks passed"
