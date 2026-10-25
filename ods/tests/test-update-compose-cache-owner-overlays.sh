#!/usr/bin/env bash
# ods-update.sh must resolve the stack again, like ods-cli, when the owner adds
# a model store, selects a model in one, or adds docker-compose.override.yml
# after install. The installer's .compose-flags predates them.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
UPDATE="${ODS_UPDATE_UNDER_TEST:-$ROOT/ods-update.sh}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
sed '$d' "$UPDATE" > "$TMP/functions.sh"
# shellcheck source=/dev/null
source "$TMP/functions.sh"
log_warn() { :; }
FAILURES=0
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; FAILURES=$((FAILURES + 1)); }

INSTALL_DIR="$TMP/install"
mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/data"
touch "$INSTALL_DIR/docker-compose.base.yml" "$INSTALL_DIR/docker-compose.cpu.yml"
printf '#!/usr/bin/env bash\necho "-f docker-compose.base.yml"\n' > "$INSTALL_DIR/scripts/resolve-compose-stack.sh"
chmod +x "$INSTALL_DIR/scripts/resolve-compose-stack.sh"
base="-f docker-compose.base.yml -f docker-compose.cpu.yml"

# check NAME EXPECTED CACHE ACTIVE_STORE
check() {
    local got
    printf '%s\n' "$3" > "$INSTALL_DIR/.compose-flags"
    printf 'ODS_ACTIVE_MODEL_STORE=%s\n' "$4" > "$INSTALL_DIR/.env"
    got="$(resolve_compose_flags)"
    [[ "$got" == "$2" ]] && pass "$1" || fail "$1: got '$got', expected '$2'"
}

check "no owner overlays: the saved flags are used" "$base" "$base" default
touch "$INSTALL_DIR/.model-stores.compose.json" "$INSTALL_DIR/data/.active-model-store.compose.json"
check "a store registered after install resolves again" "-f docker-compose.base.yml" "$base" default
check "a model selected in a store resolves a cache without its mount" "-f docker-compose.base.yml" \
    "$base -f .model-stores.compose.json" models-ssd
check "a cache with the active store's mount is kept" \
    "$base -f .model-stores.compose.json -f data/.active-model-store.compose.json" \
    "$base -f .model-stores.compose.json -f data/.active-model-store.compose.json" models-ssd
check "back on the default store, a cache with the store mount resolves again" "-f docker-compose.base.yml" \
    "$base -f .model-stores.compose.json -f data/.active-model-store.compose.json" default
rm -f "$INSTALL_DIR/.model-stores.compose.json" "$INSTALL_DIR/data/.active-model-store.compose.json"
touch "$INSTALL_DIR/docker-compose.override.yml"
check "an owner override added after install resolves again" "-f docker-compose.base.yml" "$base" default

[[ "$FAILURES" == 0 ]] || exit 1
echo "All ods-update Compose cache checks passed"
