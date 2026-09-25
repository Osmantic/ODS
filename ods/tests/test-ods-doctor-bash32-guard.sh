#!/usr/bin/env bash
# Regression coverage for scripts/ods-doctor.sh under stock macOS Bash 3.2.
#
# ods-doctor.sh sources lib/service-registry.sh (Bash 4 associative arrays).
# Callers launch it by its shebang (ods-cli `ods doctor`, installers/macos.sh,
# the host agent, the documented direct invocation), so on macOS it can run
# under /bin/bash 3.2 and used to die with "service-registry.sh requires
# Bash 4.0+" before generating a report. It must now re-exec under a modern
# Bash instead.
#
# Run from repo root:  bash ods/tests/test-ods-doctor-bash32-guard.sh
# Or from ods:         bash tests/test-ods-doctor-bash32-guard.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCTOR="$ROOT_DIR/scripts/ods-doctor.sh"
SR_ERR="service-registry.sh requires Bash 4.0+"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

[[ -f "$DOCTOR" ]] || { echo "ods-doctor.sh not found at $DOCTOR" >&2; exit 1; }

# 1. Static: the Bash-4 re-exec guard must appear before lib/service-registry.sh
# is sourced, or the source runs under the wrong shell first.
guard_line="$(grep -nE 'BASH_VERSINFO\[0\]:-0\}" -lt 4' "$DOCTOR" | head -n1 | cut -d: -f1)"
source_line="$(grep -nE '\. "\$ROOT_DIR/lib/service-registry.sh"' "$DOCTOR" | head -n1 | cut -d: -f1)"
if [[ -n "$guard_line" && -n "$source_line" && "$guard_line" -lt "$source_line" ]]; then
    pass "re-exec guard precedes the service-registry.sh source"
else
    fail "re-exec guard must precede the service-registry.sh source (guard=$guard_line source=$source_line)"
fi

# 2. --help must work under any Bash (it precedes the guard and needs no Bash 4).
if /bin/bash "$DOCTOR" --help >/dev/null 2>&1; then
    pass "--help works under stock /bin/bash"
else
    fail "--help failed under stock /bin/bash"
fi

tmp_report="$(mktemp)"
trap 'rm -f "$tmp_report"' EXIT

# 3. Launched under stock /bin/bash, a real run must never leak the raw
# service-registry.sh Bash-version error: on macOS the guard re-execs into a
# modern Bash (Homebrew paths are hard-coded candidates), and on Linux
# /bin/bash is already 4+. The report itself may still fail later on this
# host (no Docker), which is fine — we only assert the version gate is gone.
out="$(/bin/bash "$DOCTOR" "$tmp_report" 2>&1 || true)"
if [[ "$out" == *"$SR_ERR"* ]]; then
    fail "stock /bin/bash run leaked the service-registry Bash-version error"
else
    pass "stock /bin/bash run clears the service-registry version gate"
fi

# 4. Same when a caller passes an explicit modern Bash via \$BASH (how ods-cli
# invokes its Bash-4 helpers): the re-exec still clears the version gate.
modern_bash="$(command -v bash 2>/dev/null || true)"
if [[ -n "$modern_bash" ]] && "$modern_bash" -c '[ "${BASH_VERSINFO[0]:-0}" -ge 4 ]' 2>/dev/null; then
    out="$(BASH="$modern_bash" /bin/bash "$DOCTOR" "$tmp_report" 2>&1 || true)"
    if [[ "$out" == *"$SR_ERR"* ]]; then
        fail "re-exec via \$BASH did not happen; service-registry version error leaked"
    else
        pass "re-exec via \$BASH clears the service-registry version gate"
    fi
else
    pass "no modern Bash on PATH here; \$BASH re-exec case skipped"
fi

# 5. Static: the guard keeps an actionable fallback message for hosts with no
# modern Bash at all (cannot be exercised where Homebrew Bash exists).
if grep -q "requires Bash 4+" "$DOCTOR"; then
    pass "guard retains an actionable no-modern-Bash message"
else
    fail "guard should print an actionable message when no modern Bash is found"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
