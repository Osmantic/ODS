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
    env "$@" "$BASH" -c '. "$1"; ods_detect_python_cmd' bash "$RESOLVER"
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
mkdir -p "$tmpdir/NoPython"
if resolved="$(resolve "PATH=$WINAPPS:$PATH" "LOCALAPPDATA=$tmpdir/NoPython" ODS_PYTHON_CMD= 2>"$tmpdir/error")"; then
    fail "resolver succeeded with no interpreter available"
fi
[[ -z "$resolved" ]] || fail "resolver returned '$resolved' with no interpreter available"
grep -q 'Neither python3 nor python' "$tmpdir/error" || fail "missing interpreter diagnostic was not emitted"
pass "resolver still reports failure when nothing is installed"

echo "Test 4: ODS_PYTHON_CMD override is not bypassed by the fallback"
resolved="$(resolve "PATH=$WINAPPS:$PATH" "LOCALAPPDATA=$tmpdir/Local" "ODS_PYTHON_CMD=$REALBIN/python3")"
[[ "$resolved" == "$REALBIN/python3" ]] \
    || fail "resolver returned '$resolved' instead of the ODS_PYTHON_CMD override"
pass "ODS_PYTHON_CMD override keeps precedence"

echo "Test 5: Python Install Manager runtime with spaces in LOCALAPPDATA"
MANAGER_LOCAL="$tmpdir/Local with spaces"
MANAGER_PYTHON="$MANAGER_LOCAL/Python/pythoncore-3.14-64/python.exe"
write_working_python "$MANAGER_PYTHON"
resolved="$(resolve "PATH=$WINAPPS:$PATH" "LOCALAPPDATA=$MANAGER_LOCAL" ODS_PYTHON_CMD=)"
[[ "$resolved" == "$MANAGER_PYTHON" ]] || fail "manager runtime was not discovered: '$resolved'"
pass "generic resolver discovers the installed manager runtime"

resolved="$(env "PATH=$WINAPPS:$PATH" "LOCALAPPDATA=$MANAGER_LOCAL" ODS_PYTHON_CMD= "$BASH" -c '. "$1"; ods_detect_python_cmd_with_module json' bash "$RESOLVER")"
[[ "$resolved" == "$MANAGER_PYTHON" ]] || fail "module resolver missed the manager runtime: '$resolved'"
pass "module-aware resolver discovers the same manager runtime"

echo "Test 6: unusable manager runtime does not hide a later working runtime"
BAD_PYTHON="$MANAGER_LOCAL/Python/pythoncore-3.13-64/python.exe"
write_working_python "$BAD_PYTHON"
printf '#!/usr/bin/env bash\nexit 1\n' > "$BAD_PYTHON"
resolved="$(resolve "PATH=$WINAPPS:$PATH" "LOCALAPPDATA=$MANAGER_LOCAL" ODS_PYTHON_CMD=)"
[[ "$resolved" == "$MANAGER_PYTHON" ]] || fail "unusable runtime prevented discovery: '$resolved'"
pass "only runnable manager runtimes are returned"

echo "Test 7: backend contract public entry point executes the manager interpreter"
# Resolve the test interpreter before masking PATH. The fixture wrapper then
# executes real Python, so this verifies JSON output rather than just a probe.
test_python="${TEST_PYTHON_CMD:-python3}"
real_python="$("$test_python" -c 'import sys; print(sys.executable)')"
. "$RESOLVER"
real_python="$(_ods_windows_path_to_unix "${real_python//$'\r'/}")"
printf '#!/usr/bin/env bash\nexec %q "$@"\n' "$real_python" > "$MANAGER_PYTHON"
output="$(env "PATH=$WINAPPS:$PATH" "LOCALAPPDATA=$MANAGER_LOCAL" ODS_PYTHON_CMD= "$BASH" scripts/load-backend-contract.sh --backend amd --env)"
[[ "$output" == *'BACKEND_CONTRACT_ID="amd"'* && "$output" == *'BACKEND_LLM_ENGINE="lemonade"'* ]] \
    || fail "backend contract could not be rendered through the manager runtime"
pass "backend contract renderer produces the shipped AMD contract"

echo ""
echo "All python-cmd resolver tests passed."
