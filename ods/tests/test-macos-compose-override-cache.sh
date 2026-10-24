#!/usr/bin/env bash
# ods-macos.sh resolves the stack again when the owner adds the
# docker-compose.override.yml that docs/PROFILES.md describes after install:
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

printf 'services:\n  n8n:\n    profiles: [disabled]\n' > "$INSTALL_DIR/docker-compose.override.yml"
[[ "$(_get_base_compose_flags)" == "RESOLVED" ]] \
    && pass "an override added after install resolves the stack again" \
    || fail "an override added after install was ignored: $(_get_base_compose_flags)"

printf '%s\n' "$cache -f docker-compose.override.yml" > "$INSTALL_DIR/.compose-flags"
[[ "$(_get_base_compose_flags)" == "$cache -f docker-compose.override.yml" ]] \
    && pass "a cache that already lists the override is kept" \
    || fail "a cache listing the override was not kept"

[[ "$FAILURES" == 0 ]] || exit 1
echo "All macOS Compose override cache checks passed"
