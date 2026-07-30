#!/usr/bin/env bash
# Test lib/python-cmd.sh: ods_detect_python_cmd interpreter discovery.
#
# The resolver deliberately rejects the Windows App Execution Aliases
# (%LOCALAPPDATA%\Microsoft\WindowsApps\python*.exe) because they are Store
# stubs, not interpreters. Those aliases ship enabled by default, so on Git
# Bash they usually shadow BOTH `python3` and `python` on PATH. The resolver
# must still find the real interpreter under LOCALAPPDATA instead of reporting
# that no Python exists.
#
# Run from repo root:  bash ods/tests/test-python-cmd-resolver.sh
# Or from ods: bash tests/test-python-cmd-resolver.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

RESOLVER="$ROOT_DIR/lib/python-cmd.sh"
[[ -f "$RESOLVER" ]] || fail "lib/python-cmd.sh not found"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# A stub that behaves like a working interpreter for the resolver's probe
# ("python -c 'import sys; sys.exit(0)'").
write_working_python() {
    local target="$1"
    mkdir -p "$(dirname "$target")"
    cat > "$target" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$target"
}

WINAPPS="$tmpdir/Local/Microsoft/WindowsApps"
write_working_python "$WINAPPS/python"
write_working_python "$WINAPPS/python3"

REAL_PYTHON="$tmpdir/Local/Programs/Python/Python313/python.exe"
write_working_python "$REAL_PYTHON"

# The resolver caches its answer per shell, so every case runs in a fresh bash.
resolve() {
    env "$@" bash -c '. "$1"; ods_detect_python_cmd' bash "$RESOLVER" 2>/dev/null || true
}

echo "Test 1: both PATH names are WindowsApps aliases, real interpreter under LOCALAPPDATA"
resolved="$(resolve "PATH=$WINAPPS:$PATH" "LOCALAPPDATA=$tmpdir/Local" ODS_PYTHON_CMD=)"
[[ "$resolved" == "$REAL_PYTHON" ]] \
    || fail "resolver returned '$resolved' instead of the LOCALAPPDATA interpreter '$REAL_PYTHON'"
pass "resolver falls back to the LOCALAPPDATA interpreter"

echo "Test 2: a usable PATH interpreter still wins over the LOCALAPPDATA fallback"
REALBIN="$tmpdir/realbin"
write_working_python "$REALBIN/python3"
resolved="$(resolve "PATH=$REALBIN:$WINAPPS:$PATH" "LOCALAPPDATA=$tmpdir/Local" ODS_PYTHON_CMD=)"
[[ "$resolved" == "python3" ]] \
    || fail "resolver returned '$resolved' instead of the PATH python3"
pass "PATH python3 keeps precedence over the LOCALAPPDATA fallback"

echo "Test 3: no interpreter anywhere still fails loudly"
empty_bin="$tmpdir/empty"
mkdir -p "$empty_bin" "$tmpdir/NoPython"
resolved="$(resolve "PATH=$WINAPPS:$empty_bin" "LOCALAPPDATA=$tmpdir/NoPython" ODS_PYTHON_CMD=)"
[[ -z "$resolved" ]] || fail "resolver returned '$resolved' with no interpreter available"
pass "resolver still reports failure when nothing is installed"

echo "Test 4: ODS_PYTHON_CMD override is not bypassed by the fallback"
resolved="$(resolve "PATH=$WINAPPS:$PATH" "LOCALAPPDATA=$tmpdir/Local" "ODS_PYTHON_CMD=$REALBIN/python3")"
[[ "$resolved" == "$REALBIN/python3" ]] \
    || fail "resolver returned '$resolved' instead of the ODS_PYTHON_CMD override"
pass "ODS_PYTHON_CMD override keeps precedence"

echo ""
echo "All python-cmd resolver tests passed."
