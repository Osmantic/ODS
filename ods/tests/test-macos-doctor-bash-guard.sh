#!/usr/bin/env bash
# installers/macos.sh must never report a doctor JSON that was not written.
#
# scripts/ods-doctor.sh sources lib/service-registry.sh, which needs Bash 4+.
# On a stock Mac (Bash 3.2, no Homebrew Bash) the doctor exits before writing
# anything; the wrapper used to swallow that and print "[INFO] Doctor report:"
# anyway. These tests pin the contract with a hermetic copy of the wrapper and
# stubbed preflight/doctor scripts. Hosts without a discoverable Bash 4+ verify
# the skip path; hosts with one verify the run-through path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MACOS_SH="$ROOT_DIR/installers/macos.sh"

PASS=0
FAIL=0
SKIP=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }
skip() { echo "[SKIP] $1"; SKIP=$((SKIP + 1)); }

first_line() {
    grep -n -- "$1" "$2" | head -n 1 | cut -d: -f1
}

has() { printf '%s\n' "$1" | grep -q -- "$2"; }

# --- Structural guards ------------------------------------------------------

probe_line="$(first_line 'BASH_VERSINFO\[0\]' "$MACOS_SH")"
doctor_line="$(first_line 'scripts/ods-doctor.sh" "\$DOCTOR_FILE"' "$MACOS_SH")"
if [[ -n "$probe_line" && -n "$doctor_line" && "$probe_line" -lt "$doctor_line" ]]; then
    pass "macos.sh probes for Bash 4+ before running ods-doctor.sh"
else
    fail "macos.sh must probe for Bash 4+ before running ods-doctor.sh"
fi

if grep -q 'ods-doctor.sh" "\$DOCTOR_FILE" >/dev/null 2>&1 || true' "$MACOS_SH"; then
    fail "macos.sh still swallows the ods-doctor.sh exit status with '|| true'"
else
    pass "macos.sh no longer swallows the ods-doctor.sh exit status"
fi

if grep -q -- '-f "\$DOCTOR_FILE"' "$MACOS_SH"; then
    pass "macos.sh checks that the doctor report exists before announcing it"
else
    fail "macos.sh must check -f \"\$DOCTOR_FILE\" before announcing the report"
fi

if grep -q 'brew install bash' "$MACOS_SH"; then
    pass "macos.sh tells the user how to get Bash 4+ when the doctor is skipped"
else
    fail "macos.sh should point at 'brew install bash' when the doctor is skipped"
fi

# --- Behavioural contract: INFO only when the file exists -------------------

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
mkdir -p "$TMP_ROOT/installers" "$TMP_ROOT/scripts" "$TMP_ROOT/lib"
cp "$MACOS_SH" "$TMP_ROOT/installers/macos.sh"
cp "$ROOT_DIR/lib/safe-env.sh" "$TMP_ROOT/lib/safe-env.sh"
cp "$ROOT_DIR/lib/python-cmd.sh" "$TMP_ROOT/lib/python-cmd.sh"

# Stub preflight: no blockers, minimal report, so the wrapper reaches the doctor step.
cat > "$TMP_ROOT/scripts/preflight-engine.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
report=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --report) report="$2"; shift 2 ;;
        *) shift ;;
    esac
done
printf '{"checks": []}\n' > "$report"
printf 'PREFLIGHT_BLOCKERS=0\nPREFLIGHT_WARNINGS=0\n'
EOF
chmod +x "$TMP_ROOT/scripts/preflight-engine.sh"

run_wrapper() {
    # $1 = doctor stub body; prints wrapper stdout+stderr, never fails the caller.
    local report="$TMP_ROOT/preflight.json"
    local doctor="$TMP_ROOT/doctor.json"
    rm -f "$report" "$doctor"
    {
        printf '#!/usr/bin/env bash\nset -euo pipefail\n'
        printf '%s\n' "$1"
    } > "$TMP_ROOT/scripts/ods-doctor.sh"
    chmod +x "$TMP_ROOT/scripts/ods-doctor.sh"
    bash "$TMP_ROOT/installers/macos.sh" --no-delegate \
        --report "$report" --doctor-report "$doctor" 2>&1 || true
}

# Case 1: doctor exits non-zero without writing anything (what Bash 3.2 does today).
out="$(run_wrapper 'echo "ERROR: service-registry.sh requires Bash 4.0+" >&2; exit 1')"

if has "$out" '^\[INFO\] Doctor report:'; then
    fail "wrapper announced a doctor report that was never written"
else
    pass "wrapper does not announce a missing doctor report"
fi

if has "$out" '^\[WARN\] Doctor report skipped'; then
    # No Bash 4+ discoverable on this host: the doctor was (correctly) never run.
    if has "$out" 'requires Bash 4+' && has "$out" 'brew install bash'; then
        pass "wrapper explains the skip and points at 'brew install bash'"
    else
        fail "wrapper must explain the skip and point at 'brew install bash'"
    fi
    skip "run-through cases: no Bash 4+ discoverable on this host"
    skip "run-through cases: doctor stderr surfacing"
    skip "run-through cases: report announced when written"
else
    if has "$out" '^\[WARN\] Doctor report was not written'; then
        pass "wrapper warns when the doctor report was not written"
    else
        fail "wrapper must warn when the doctor report was not written"
    fi
    if has "$out" 'service-registry.sh requires Bash 4.0+'; then
        pass "wrapper surfaces the doctor's own error text"
    else
        fail "wrapper should surface the doctor's stderr on failure"
    fi

    # Case 2: doctor exits zero but writes nothing.
    out="$(run_wrapper 'exit 0')"
    if has "$out" '^\[INFO\] Doctor report:'; then
        fail "wrapper announced a report after a doctor run that wrote nothing"
    else
        pass "wrapper requires the file to exist, not just exit 0"
    fi

    # Case 3: doctor writes the report and exits zero.
    out="$(run_wrapper 'printf "{}\n" > "$1"; exit 0')"
    if has "$out" '^\[INFO\] Doctor report:'; then
        pass "wrapper announces the doctor report when it exists"
    else
        fail "wrapper must announce the doctor report when it was written"
    fi
    if has "$out" '^\[WARN\] Doctor report'; then
        fail "wrapper warned even though the doctor report was written"
    else
        pass "wrapper does not warn on a successful doctor run"
    fi
fi

echo "Result: $PASS passed, $FAIL failed, $SKIP skipped"
[[ "$FAIL" -eq 0 ]]
