#!/usr/bin/env bash
# Regression: `ods enable` must repair a stale compose cache even when the
# extension's compose.yaml is already active.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

make_install() {
    local install_dir="$1"
    mkdir -p \
        "$install_dir/lib" \
        "$install_dir/scripts" \
        "$install_dir/extensions/services/ods-proxy"

    cp "$ROOT_DIR/ods-cli" "$install_dir/ods-cli"
    cp "$ROOT_DIR/lib/service-registry.sh" "$install_dir/lib/"
    cp "$ROOT_DIR/lib/python-cmd.sh" "$install_dir/lib/"
    cat > "$install_dir/scripts/resolve-compose-stack.sh" <<'RESOLVER'
#!/usr/bin/env bash
set -euo pipefail
script_dir=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --script-dir) script_dir="$2"; shift 2 ;;
        *) shift ;;
    esac
done
flags="-f docker-compose.base.yml"
if [[ -f "$script_dir/extensions/services/ods-proxy/compose.yaml" ]]; then
    flags="$flags -f extensions/services/ods-proxy/compose.yaml"
fi
printf '%s\n' "$flags"
RESOLVER
    chmod +x "$install_dir/scripts/resolve-compose-stack.sh"
    cp "$ROOT_DIR/extensions/services/ods-proxy/compose.yaml" \
        "$install_dir/extensions/services/ods-proxy/"

    # Keep the fixture focused on compose reconciliation. Core dependency
    # availability is covered by the registry/enable dependency tests.
    sed 's/depends_on: \[dashboard, dashboard-api, open-webui\]/depends_on: []/' \
        "$ROOT_DIR/extensions/services/ods-proxy/manifest.yaml" \
        > "$install_dir/extensions/services/ods-proxy/manifest.yaml"

    printf 'services: {}\n' > "$install_dir/docker-compose.base.yml"
    printf '%s\n' \
        'GPU_BACKEND=cpu' \
        'GPU_COUNT=1' \
        'ODS_MODE=local' \
        'TIER=1' \
        > "$install_dir/.env"
}

for cache_state in stale empty missing; do
    install_dir="$TMP_DIR/$cache_state"
    make_install "$install_dir"
    case "$cache_state" in
        stale) printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags" ;;
        empty) : > "$install_dir/.compose-flags" ;;
        missing) ;;
    esac

    ODS_HOME="$install_dir" bash "$install_dir/ods-cli" enable ods-proxy >/dev/null
    first="$(cat "$install_dir/.compose-flags")"
    [[ "$first" == *"-f docker-compose.base.yml"* ]] \
        || fail "$cache_state cache recovery dropped the base compose file"
    [[ "$first" == *"-f extensions/services/ods-proxy/compose.yaml"* ]] \
        || fail "$cache_state cache recovery omitted ods-proxy"

    ODS_HOME="$install_dir" bash "$install_dir/ods-cli" enable ods-proxy >/dev/null
    second="$(cat "$install_dir/.compose-flags")"
    [[ "$second" == "$first" ]] \
        || fail "$cache_state cache reconciliation was not idempotent"
    [[ "$(grep -o 'extensions/services/ods-proxy/compose.yaml' \
        "$install_dir/.compose-flags" | wc -l)" -eq 1 ]] \
        || fail "$cache_state cache reconciliation duplicated ods-proxy"
done

pass "Linux enable reconciles stale, empty, and missing compose caches"

dual_dir="$TMP_DIR/dual-marker"
make_install "$dual_dir"
cp "$dual_dir/extensions/services/ods-proxy/compose.yaml" \
    "$dual_dir/extensions/services/ods-proxy/compose.yaml.disabled"
printf '%s\n' '-f docker-compose.base.yml' > "$dual_dir/.compose-flags"

ODS_HOME="$dual_dir" bash "$dual_dir/ods-cli" enable ods-proxy >/dev/null
[[ -f "$dual_dir/extensions/services/ods-proxy/compose.yaml" ]] \
    || fail "dual-marker repair removed the active compose fragment"
[[ ! -e "$dual_dir/extensions/services/ods-proxy/compose.yaml.disabled" ]] \
    || fail "dual-marker repair left the stale disabled marker"
grep -q -- '-f extensions/services/ods-proxy/compose.yaml' \
    "$dual_dir/.compose-flags" \
    || fail "dual-marker repair did not add ods-proxy to the active stack"

pass "Linux enable normalizes a dual compose-marker state"
