#!/usr/bin/env bash
# ============================================================================
# Contract: dream-proxy routing schema (manifest <-> resolver <-> Caddyfile)
# ============================================================================
# Asserts repo-wide invariants for the per-extension proxy schema:
#   - every shipped manifest passes the audit-script validation (catches
#     malformed / reserved / colliding proxy.subdomain values at PR time)
#   - the resolver, run against the real extensions/services tree, emits a
#     fragment for every service that has a proxy block AND a non-disabled
#     compose file — and only those services
#   - the Caddyfile master imports /etc/caddy/sites.d/*.caddy (otherwise the
#     fragments are inert)
#   - the dream-proxy compose bind-mounts ./data/dream-proxy/sites.d into
#     the container (otherwise the imports resolve to nothing at runtime)
#
# Run: bash tests/contracts/test-proxy-routing.sh
# ============================================================================

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PASS=0
FAIL=0

pass() { echo "  ok: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

cd "$ROOT_DIR"

echo "[contract] audit-extensions.py accepts every shipped manifest"
audit_output="$(python3 scripts/audit-extensions.py --strict 2>&1)"
audit_exit=$?
if [[ $audit_exit -eq 0 ]]; then
    pass "audit passes strict mode"
else
    fail "audit failed in strict mode (output below)"
    echo "$audit_output"
fi

echo "[contract] resolver emits a fragment per (proxy + enabled-compose) manifest"
TMP_INSTALL="$(mktemp -d)"
trap 'rm -rf "$TMP_INSTALL"' EXIT
# Mirror the real extensions/services tree into a writable temp dir so the
# resolver's idempotent reconcile can run without touching the repo.
mkdir -p "$TMP_INSTALL/extensions/services" "$TMP_INSTALL/data/dream-proxy/sites.d"
cp -r "$ROOT_DIR/extensions/services/." "$TMP_INSTALL/extensions/services/"

unset DREAM_PROXY_EXCLUSIVE
bash scripts/resolve-proxy-config.sh --script-dir "$TMP_INSTALL" >/dev/null 2>&1

# Build the expected service-id set: every manifest with a proxy block AND
# either a present compose.yaml or category: core (mirrors resolver gating).
expected_ids=$(python3 - <<'PY'
import pathlib, yaml
root = pathlib.Path("extensions/services")
ids = []
for service_dir in sorted(root.iterdir()):
    if not service_dir.is_dir():
        continue
    mp = service_dir / "manifest.yaml"
    if not mp.exists():
        continue
    m = yaml.safe_load(mp.read_text())
    if not isinstance(m, dict) or not isinstance(m.get("proxy"), dict):
        continue
    sub = (m["proxy"].get("subdomain") or "").strip().lower()
    if not sub:
        continue
    svc = m.get("service") or {}
    sid = svc.get("id") or service_dir.name
    compose_rel = svc.get("compose_file") or ""
    category = svc.get("category") or "optional"
    if compose_rel:
        if (service_dir / (compose_rel + ".disabled")).exists():
            continue
        if not (service_dir / compose_rel).exists() and category != "core":
            continue
    elif category != "core":
        continue
    ids.append(sid)
print("\n".join(sorted(ids)))
PY
)

generated_ids=$(find "$TMP_INSTALL/data/dream-proxy/sites.d" -name "*.caddy" -printf '%f\n' \
    | sed 's/\.caddy$//' | sort)

if [[ "$expected_ids" == "$generated_ids" ]]; then
    pass "fragment set matches manifest predicate ($(echo "$expected_ids" | wc -l) services)"
else
    fail "fragment set drift"
    diff <(echo "$expected_ids") <(echo "$generated_ids") || true
fi

echo "[contract] Caddyfile imports the generated fragments directory"
if grep -qE '^[[:space:]]*import[[:space:]]+/etc/caddy/sites\.d/\*\.caddy' \
    extensions/services/dream-proxy/Caddyfile; then
    pass "Caddyfile imports /etc/caddy/sites.d/*.caddy"
else
    fail "Caddyfile is missing the import line — fragments would be inert"
fi

echo "[contract] dream-proxy compose bind-mounts sites.d into the container"
if grep -qE 'data/dream-proxy/sites\.d:/etc/caddy/sites\.d' \
    extensions/services/dream-proxy/compose.yaml; then
    pass "compose mounts data/dream-proxy/sites.d -> /etc/caddy/sites.d"
else
    fail "dream-proxy compose missing sites.d bind mount"
fi

echo "[contract] every generated fragment is well-formed Caddy syntax"
bad=0
for frag in "$TMP_INSTALL/data/dream-proxy/sites.d"/*.caddy; do
    # Light structural check: must declare a single http://...{ block with
    # exactly one reverse_proxy inside. Avoids needing the caddy binary at CI.
    if ! grep -qE '^http://[a-z0-9-]+\.\{\$DREAM_DEVICE_NAME:dream\}\.local \{$' "$frag"; then
        fail "fragment $(basename "$frag") missing hostname header"
        bad=$((bad+1))
    fi
    # `grep -E` does not expand `\t`; match a real tab via a literal.
    if [[ "$(grep -cE "^"$'\t'"reverse_proxy " "$frag")" != "1" ]]; then
        fail "fragment $(basename "$frag") must have exactly one reverse_proxy directive"
        bad=$((bad+1))
    fi
done
[[ $bad -eq 0 ]] && pass "all fragments well-formed"

echo ""
echo "=========================================="
echo "Results: $PASS passed, $FAIL failed"
echo "=========================================="
[[ $FAIL -eq 0 ]]
