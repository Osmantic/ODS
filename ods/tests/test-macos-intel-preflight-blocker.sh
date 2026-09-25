#!/usr/bin/env bash
# installers/macos.sh must report an Intel Mac as a preflight blocker.
#
# The macOS installer requires Apple Silicon (install-macos.sh exits on
# x86_64), but the preflight/doctor wrapper used to warn about the
# architecture and then hand the engine a hard-coded Apple Silicon profile,
# so an Intel Mac got a report with no blocker for the one thing that will
# stop the install. The wrapper now passes the real host architecture to the
# engine on Darwin only; the Linux CI simulation passes an empty value and
# must keep getting no architecture blocker.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }
has() { printf '%s\n' "$1" | grep -q -- "$2"; }

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# Fake uname so the wrapper sees the host we choose.
mkdir -p "$TMP_ROOT/bin"
cat > "$TMP_ROOT/bin/uname" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
    -m) printf '%s\n' "${FAKE_UNAME_M:-x86_64}" ;;
    *) printf '%s\n' "${FAKE_UNAME_S:-Linux}" ;;
esac
EOF
chmod +x "$TMP_ROOT/bin/uname"

make_tree() {
    # $1 = tree dir, $2 = "stub" | "real" preflight engine
    local tree="$1" engine="$2"
    mkdir -p "$tree/installers" "$tree/scripts" "$tree/lib"
    cp "$ROOT_DIR/installers/macos.sh" "$tree/installers/macos.sh"
    cp "$ROOT_DIR/lib/safe-env.sh" "$tree/lib/safe-env.sh"
    cp "$ROOT_DIR/lib/python-cmd.sh" "$tree/lib/python-cmd.sh"
    # Quiet doctor stub so only the preflight step is under test.
    printf '#!/usr/bin/env bash\nprintf "{}\\n" > "$1"\n' > "$tree/scripts/ods-doctor.sh"
    chmod +x "$tree/scripts/ods-doctor.sh"
    if [[ "$engine" == "real" ]]; then
        cp "$ROOT_DIR/scripts/preflight-engine.sh" "$tree/scripts/preflight-engine.sh"
        # The engine only checks that the overlays exist.
        : > "$tree/docker-compose.base.yml"
        : > "$tree/docker-compose.amd.yml"
    else
        cat > "$tree/scripts/preflight-engine.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
report=""
printf '%s\n' "$@" > "${ENGINE_ARGV_LOG:?}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --report) report="$2"; shift 2 ;;
        *) shift ;;
    esac
done
printf '{"checks": []}\n' > "$report"
printf 'PREFLIGHT_BLOCKERS=0\nPREFLIGHT_WARNINGS=0\n'
EOF
        chmod +x "$tree/scripts/preflight-engine.sh"
    fi
}

WRAPPER_RC=0
WRAPPER_OUT=""
run_wrapper() {
    # $1 = tree, $2 = uname -s, $3 = uname -m. Runs in this shell (no command
    # substitution) so the exit code survives; output lands in WRAPPER_OUT.
    local tree="$1"
    WRAPPER_RC=0
    PATH="$TMP_ROOT/bin:$PATH" FAKE_UNAME_S="$2" FAKE_UNAME_M="$3" \
        ENGINE_ARGV_LOG="$tree/engine-argv.log" \
        bash "$tree/installers/macos.sh" --no-delegate \
            --report "$tree/preflight.json" --doctor-report "$tree/doctor.json" \
            > "$tree/out.log" 2>&1 \
        || WRAPPER_RC=$?
    WRAPPER_OUT="$(cat "$tree/out.log")"
}

host_arch_passed() {
    # Value following --host-arch in the recorded engine argv (empty if absent).
    awk 'found { print; exit } $0 == "--host-arch" { found = 1 }' "$1/engine-argv.log"
}

# --- Wrapper gating: what does the engine receive? ---------------------------

STUB="$TMP_ROOT/stub"; make_tree "$STUB" stub

run_wrapper "$STUB" Darwin x86_64
if [[ "$(host_arch_passed "$STUB")" == "x86_64" ]]; then
    pass "Darwin/x86_64: wrapper passes --host-arch x86_64 to the engine"
else
    fail "Darwin/x86_64: wrapper must pass --host-arch x86_64 (got '$(host_arch_passed "$STUB")')"
fi
if has "$WRAPPER_OUT" 'Intel Mac detected'; then
    pass "Darwin/x86_64: wrapper names the Intel Mac in its output"
else
    fail "Darwin/x86_64: wrapper should say an Intel Mac was detected"
fi

run_wrapper "$STUB" Darwin arm64
if [[ "$(host_arch_passed "$STUB")" == "arm64" ]]; then
    pass "Darwin/arm64: wrapper passes --host-arch arm64 to the engine"
else
    fail "Darwin/arm64: wrapper must pass --host-arch arm64 (got '$(host_arch_passed "$STUB")')"
fi
if has "$WRAPPER_OUT" 'Apple Silicon detected'; then
    pass "Darwin/arm64: wrapper reports Apple Silicon"
else
    fail "Darwin/arm64: wrapper should report Apple Silicon"
fi

run_wrapper "$STUB" Linux x86_64
if grep -q -- '--host-arch' "$STUB/engine-argv.log" && [[ -z "$(host_arch_passed "$STUB")" ]]; then
    pass "Linux/x86_64 (CI simulation): wrapper passes an empty --host-arch"
else
    fail "Linux/x86_64 (CI simulation): wrapper must pass an empty --host-arch (got '$(host_arch_passed "$STUB")')"
fi
if has "$WRAPPER_OUT" 'Intel Mac detected'; then
    fail "Linux/x86_64: wrapper must not call a Linux host an Intel Mac"
else
    pass "Linux/x86_64: wrapper does not call a Linux host an Intel Mac"
fi

# --- End to end with the real engine ----------------------------------------

REAL="$TMP_ROOT/real"; make_tree "$REAL" real

run_wrapper "$REAL" Darwin x86_64
if has "$WRAPPER_OUT" 'BLOCKER: Intel Mac (x86_64) is not supported'; then
    pass "real engine, Darwin/x86_64: report carries the Intel Mac blocker"
else
    fail "real engine, Darwin/x86_64: report must carry the Intel Mac blocker"
fi
if [[ "$WRAPPER_RC" -eq 2 ]]; then
    pass "real engine, Darwin/x86_64: wrapper exits 2 on the blocker"
else
    fail "real engine, Darwin/x86_64: wrapper should exit 2 (got $WRAPPER_RC)"
fi

# Disk/RAM on the host running this test may add their own blockers, so only
# assert the architecture outcome for the remaining cases.
run_wrapper "$REAL" Darwin arm64
if has "$WRAPPER_OUT" 'Intel Mac'; then
    fail "real engine, Darwin/arm64: no Intel Mac blocker expected"
else
    pass "real engine, Darwin/arm64: no Intel Mac blocker"
fi

run_wrapper "$REAL" Linux x86_64
if has "$WRAPPER_OUT" 'Intel Mac'; then
    fail "real engine, Linux/x86_64 (CI simulation): no Intel Mac blocker expected"
else
    pass "real engine, Linux/x86_64 (CI simulation): no Intel Mac blocker"
fi

echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
