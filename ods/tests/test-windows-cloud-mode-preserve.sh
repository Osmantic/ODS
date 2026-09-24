#!/usr/bin/env bash
# Tests for the Windows installer cloud-mode preservation contract.
#
# `install-windows.ps1 -Cloud` writes ODS_MODE=cloud into .env, but the
# installer derives $cloudMode solely from the -Cloud switch. A later flagless
# rerun therefore lands in local mode: phase 02 re-runs GPU detection, phase 06
# rewrites .env as ODS_MODE=local, LiteLLM keys and native-inference settings
# are regenerated for a stack that has no local model, and the Cloud install is
# silently converted into a broken "local" one.
#
# Linux and macOS preserve the persisted mode across flagless reruns; the
# Windows installer must do the same: when -Cloud is not passed, a persisted
# ODS_MODE=cloud in .env must restore $cloudMode before any phase runs.
#
# These are hermetic static contracts (no PowerShell, no Docker required),
# matching the convention in test-windows-cli-enable-disable.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALLER="$ROOT_DIR/installers/windows/install-windows.ps1"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}OK${NC} $1"; }
fail() { echo -e "${RED}FAIL${NC} $1"; exit 1; }
info() { echo -e "${BLUE}i${NC} $1"; }

info "Static: installer file exists"
[[ -s "$INSTALLER" ]] || fail "install-windows.ps1 missing"
pass "installer file exists"

# Extract the orchestrator prologue: from the $cloudMode assignment through the
# first phase source line. The cloud-mode recovery must live inside this span,
# before any phase consumes $cloudMode.
prologue() {
    awk '/cloudMode[[:space:]]*=[[:space:]]*[$]?Cloud\.IsPresent/{p=1} p{print} p&&/01-preflight/{exit}' \
        "$INSTALLER" | tr -d '\r'
}

info "Static: prologue reads the persisted ODS_MODE from .env"
prologue | grep -q 'Get-WindowsODSEnvMap' \
    || fail "orchestrator prologue never reads the persisted .env (Get-WindowsODSEnvMap)"
prologue | grep -q '"ODS_MODE"' \
    || fail "orchestrator prologue never consults the persisted ODS_MODE value"
pass "prologue consults persisted ODS_MODE"

info "Static: flagless rerun restores cloud mode from .env"
prologue | grep -Eq 'cloudMode[[:space:]]*=[[:space:]]*\$true' \
    || fail "prologue never restores cloudMode from the persisted mode"
pass "cloudMode is restored from persisted ODS_MODE=cloud"

info "Static: recovery is gated on -Cloud not being passed"
# The flag itself must still win: the restore may only run when $cloudMode is
# currently false (i.e. -Cloud absent). Require an explicit guard.
prologue | grep -Eq 'if[[:space:]]*\(-not \$cloudMode\)' \
    || fail 'cloud-mode restore is not guarded by "-not $cloudMode"'
pass "restore preserves explicit -Cloud"

info "Static: recovery runs before phase 01 is sourced"
prelude_end="$(grep -n 'Join-Path.*01-preflight' "$INSTALLER" | head -1 | cut -d: -f1)"
[[ -n "$prelude_end" ]] || fail "could not locate phase 01 source line"
# Every ODS_MODE consult in the prologue must sit before phase 01.
awk -v end="$prelude_end" 'NR<end && /"ODS_MODE"/{found=1} END{exit !found}' "$INSTALLER" \
    || fail "ODS_MODE is only consulted after phase dispatch begins"
pass "recovery precedes phase dispatch"

echo ""
echo -e "${GREEN}All Windows cloud-mode preservation contracts passed${NC}"
