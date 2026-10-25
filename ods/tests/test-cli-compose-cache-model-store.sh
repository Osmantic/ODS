#!/usr/bin/env bash
# The CLI's saved Compose flags must be resolved again once the owner registers
# a model store or selects a model in one. A cache written by the installer
# before that mounted the default model directory, so `ods restart` left
# llama-server without its model and LiteLLM and the Dashboard unstarted.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="${ODS_CLI_UNDER_TEST:-$ROOT_DIR/ods-cli}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
FAILURES=0
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; FAILURES=$((FAILURES + 1)); }

extract() { awk -v name="$2" '$0 == name "() {" { copy=1 } copy { print } copy && /^}$/ { exit }' "$1"; }
definition="$(extract "$CLI" get_compose_flags)"
[[ -n "$definition" ]] || { echo "[FAIL] get_compose_flags not found in $CLI" >&2; exit 1; }
eval "$definition"
_ensure_hermes_dashboard_session_token() { :; }
warn() { echo "warn: $*" >&2; }

INSTALL_DIR="$tmp/install"
mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/data"
touch "$INSTALL_DIR/docker-compose.base.yml" "$INSTALL_DIR/docker-compose.cpu.yml"
printf '#!/usr/bin/env bash\necho RESOLVED\n' > "$INSTALL_DIR/scripts/resolve-compose-stack.sh"
chmod +x "$INSTALL_DIR/scripts/resolve-compose-stack.sh"
base_cache="-f docker-compose.base.yml -f docker-compose.cpu.yml"
stores="-f .model-stores.compose.json"
active="-f data/.active-model-store.compose.json"

# check NAME EXPECTED CACHE [ACTIVE_STORE]
check() {
    local name="$1" expected="$2" cache="$3" store="${4:-}" got
    printf '%s\n' "$cache" > "$INSTALL_DIR/.compose-flags"
    if [[ -n "$store" ]]; then export ODS_ACTIVE_MODEL_STORE="$store"; else unset ODS_ACTIVE_MODEL_STORE; fi
    got="$(get_compose_flags)"
    [[ "$got" == "$expected" ]] && pass "$name" || fail "$name: got '$got', expected '$expected'"
}

check "no model stores: the saved flags are used" "$base_cache" "$base_cache"

touch "$INSTALL_DIR/.model-stores.compose.json"
check "a store registered after install resolves the stack again" "RESOLVED" "$base_cache"
check "a cache that already mounts the stores is kept" "$base_cache $stores" "$base_cache $stores" default

touch "$INSTALL_DIR/data/.active-model-store.compose.json"
check "a model selected in a store resolves a cache without its mount" "RESOLVED" "$base_cache $stores" models-ssd
check "a cache with the active store's mount is kept" "$base_cache $stores $active" "$base_cache $stores $active" models-ssd
check "back on the default store, a cache with the store mount resolves again" "RESOLVED" "$base_cache $stores $active" default

# An owner override (docs/PROFILES.md) created after install is picked up too.
rm -f "$INSTALL_DIR/.model-stores.compose.json" "$INSTALL_DIR/data/.active-model-store.compose.json"
printf 'services:\n  n8n:\n    profiles: [disabled]\n' > "$INSTALL_DIR/docker-compose.override.yml"
check "an owner override added after install resolves the stack again" "RESOLVED" "$base_cache"
check "a cache that already includes the override is kept" \
    "$base_cache -f docker-compose.override.yml" "$base_cache -f docker-compose.override.yml"

[[ "$FAILURES" == 0 ]] || exit 1
echo "All Compose cache model-store checks passed"
