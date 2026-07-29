#!/usr/bin/env bash
# ============================================================================
# ODS Windows installer phase-abort sentinel tests
#
# Verifies the ODS_INSTALL_ABORTED contract for dot-sourced phases:
#   1. Phase-local broad catches must rethrow the sentinel, not swallow it.
#   2. The orchestrator stops (exit 1) before running the next phase when a
#      phase throws the sentinel from inside a local try/catch.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PREFLIGHT="$ROOT_DIR/installers/windows/phases/01-preflight.ps1"
ORCHESTRATOR="$ROOT_DIR/installers/windows/install-windows.ps1"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

check() {
    local pattern="$1" file="$2" label="$3"
    if grep -Fq -- "$pattern" "$file"; then
        pass "$label"
    else
        fail "$label"
    fi
}

echo ""
echo "=== Windows installer phase-abort sentinel tests ==="
echo ""

# ── Static contract checks ───────────────────────────────────────────────────
check 'if ($_.FullyQualifiedErrorId -eq "ODS_INSTALL_ABORTED") { throw }' "$PREFLIGHT" \
    "preflight Windows-build catch rethrows abort sentinel"
check 'if ($_.FullyQualifiedErrorId -eq "ODS_INSTALL_ABORTED") { exit 1 }' "$ORCHESTRATOR" \
    "orchestrator catches sentinel and exits 1"

# ── Behavioral check: sentinel from a local try must stop the orchestrator ──
PS_BIN="powershell.exe"
if ! command -v "$PS_BIN" >/dev/null; then
    PS_BIN="pwsh"
fi

if command -v "$PS_BIN" >/dev/null; then
    TMPDIR_TEST="$(mktemp -d)"
    trap 'rm -rf "$TMPDIR_TEST"' EXIT

    cat > "$TMPDIR_TEST/phase-fatal.ps1" <<'EOF'
# Mirrors the 01-preflight.ps1 shape: fatal throw inside a broad local try.
try {
    throw "ODS_INSTALL_ABORTED"
} catch {
    if ($_.FullyQualifiedErrorId -eq "ODS_INSTALL_ABORTED") { throw }
    Write-Host "probe failed -- continuing"
}
EOF

    cat > "$TMPDIR_TEST/phase-next.ps1" <<'EOF'
Set-Content -Path (Join-Path $PSScriptRoot "next-phase-ran.marker") -Value "ran"
EOF

    cat > "$TMPDIR_TEST/orchestrator.ps1" <<'EOF'
# Mirrors the install-windows.ps1 phase loop.
$ErrorActionPreference = "Stop"
try {
    . (Join-Path $PSScriptRoot "phase-fatal.ps1")
    . (Join-Path $PSScriptRoot "phase-next.ps1")
} catch {
    if ($_.FullyQualifiedErrorId -eq "ODS_INSTALL_ABORTED") { exit 1 }
    throw
}
exit 0
EOF

    set +e
    "$PS_BIN" -NoProfile -ExecutionPolicy Bypass -File "$TMPDIR_TEST/orchestrator.ps1"
    rc=$?
    set -e

    if [[ $rc -eq 1 ]]; then
        pass "orchestrator exits 1 on sentinel thrown from local try"
    else
        fail "orchestrator exits 1 on sentinel thrown from local try (got exit $rc)"
    fi

    if [[ ! -f "$TMPDIR_TEST/next-phase-ran.marker" ]]; then
        pass "next phase does not run after sentinel"
    else
        fail "next phase does not run after sentinel"
    fi
else
    echo "  SKIP behavioral checks (no PowerShell available)"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
