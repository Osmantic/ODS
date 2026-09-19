#!/usr/bin/env bash
# Regression (issue #4607): ods-cli previously ran mutating `docker compose`
# commands with no capability gate. On hosts without the Compose v2 plugin —
# or with only the EOL docker-compose v1 binary — every `ods up`-class call
# failed open into cryptic YAML errors after partially starting the stack.
#
# `_require_compose_v2` must fail fast unless `docker compose version`
# reports major >= 2, and `_compose_run_with_summary` must call it.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/ods-cli"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fakebin="$tmp/bin"
mkdir -p "$fakebin"

FUNC_SRC="$(awk '/^_require_compose_v2\(\) \{/,/^\}/' "$CLI")"
[[ -n "$FUNC_SRC" ]] || fail "could not extract _require_compose_v2 from ods-cli"

run_gate() {
    # Runs _require_compose_v2 (twice, to prove single-probe caching) with
    # $fakebin first on PATH. Prints stdout+stderr; returns the rc.
    PATH="$fakebin:$PATH" bash -c '
        set -euo pipefail
        error() { echo -e "ERROR: $1" >&2; exit 1; }
        _compose_v2_checked=""
        eval "$1"
        _require_compose_v2
        _require_compose_v2
    ' _ "$FUNC_SRC"
}

# ── Case 1: compose v2 plugin → gate passes, probed once ──────────────────
cat > "$fakebin/docker" <<'EOF'
#!/usr/bin/env bash
echo "version" >> "${DOCKER_CALL_LOG:?}"
if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
    printf 'v2.29.1\n'
    exit 0
fi
exit 0
EOF
chmod +x "$fakebin/docker"
export DOCKER_CALL_LOG="$tmp/calls.log"

out="$(run_gate 2>&1)"
grep -c version "$DOCKER_CALL_LOG" | grep -qx 1 \
    || fail "gate did not probe exactly once across two calls: $(cat "$DOCKER_CALL_LOG")"
pass "compose v2.29.1 accepted and probed exactly once"

# ── Case 2: no compose plugin → fail fast with actionable message ─────────
cat > "$fakebin/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "compose" ]]; then
    printf "docker: 'compose' is not a docker command.\n" >&2
    exit 1
fi
exit 0
EOF
chmod +x "$fakebin/docker"

if out="$(run_gate 2>&1)"; then
    fail "gate passed without a compose plugin"
fi
grep -q "Compose v2 plugin not found" <<<"$out" \
    || fail "missing-plugin failure lacked actionable message; got: $out"
pass "missing compose plugin fails fast with actionable message"

# ── Case 3: v1-era version string → fail fast ──────────────────────────────
cat > "$fakebin/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
    printf '1.29.2\n'
    exit 0
fi
exit 0
EOF
chmod +x "$fakebin/docker"

if out="$(run_gate 2>&1)"; then
    fail "gate accepted compose major version 1"
fi
grep -q "too old" <<<"$out" \
    || fail "v1 failure lacked actionable message; got: $out"
pass "compose v1.x rejected with actionable message"

# ── Case 4: mutating compose paths invoke the gate ─────────────────────────
awk '/^_compose_run_with_summary\(\) \{/,/^\}/' "$CLI" | grep -q '_require_compose_v2' \
    || fail "_compose_run_with_summary does not invoke _require_compose_v2"
awk '/^_ods_cli_rebuild_images\(\) \{/,/^\}/' "$CLI" | grep -q '_require_compose_v2' \
    || fail "_ods_cli_rebuild_images does not invoke _require_compose_v2"
awk '/^_ods_cli_pull_external_images\(\) \{/,/^\}/' "$CLI" | grep -q '_require_compose_v2' \
    || fail "_ods_cli_pull_external_images does not invoke _require_compose_v2"
pass "compose run/build/pull paths invoke the version gate"

echo "All compose version-gate checks passed."
