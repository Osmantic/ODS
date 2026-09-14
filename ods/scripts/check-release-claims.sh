#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${ROOT_DIR}/manifest.json"
MATRIX="${ROOT_DIR}/docs/SUPPORT-MATRIX.md"
TRUTH="${ROOT_DIR}/docs/PLATFORM-TRUTH-TABLE.md"

fail() { echo "[FAIL] $1"; exit 1; }
warn() { echo "[WARN] $1"; }
pass() { echo "[PASS] $1"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"
test -f "$MANIFEST" || fail "manifest.json missing"

# Manifest support expectations
linux_supported="$(jq -r '.compatibility.os.linux.supported' "$MANIFEST")"
wsl_supported="$(jq -r '.compatibility.os.windows_wsl2.supported' "$MANIFEST")"
macos_supported="$(jq -r '.compatibility.os.macos.supported' "$MANIFEST")"
windows_native_supported="$(jq -r '.compatibility.os.windows_native.supported' "$MANIFEST")"

[[ "$linux_supported" == "true" ]] || fail "manifest must mark linux supported"
[[ "$wsl_supported" == "true" ]] || fail "manifest must mark windows_wsl2 supported"
[[ "$macos_supported" == "true" ]] || fail "manifest must mark macos supported (Tier B)"
[[ "$windows_native_supported" == "false" ]] || fail "manifest must mark windows_native unsupported"

# Support matrix wording expectations
if [[ -f "$MATRIX" ]]; then
    grep -q "Windows (Docker Desktop + WSL2).*Supported\|Windows (Docker Desktop + WSL2).*Tier B" "$MATRIX" || fail "support matrix missing Windows Tier B claim"
    grep -q "macOS (Apple Silicon).*Supported\|macOS (Apple Silicon).*Tier B" "$MATRIX" || fail "support matrix missing macOS Tier B claim"
    grep -q "install\.ps1" "$MATRIX" || fail "support matrix missing Windows installer reference"
else
    warn "docs/SUPPORT-MATRIX.md missing — skipping matrix checks"
fi

# Truth table consistency
if [[ -f "$TRUTH" ]]; then
    grep -q "Windows (Docker Desktop + WSL2).*Tier B" "$TRUTH" || fail "truth table missing Windows Tier B"
    grep -q "macOS Apple Silicon.*Tier B" "$TRUTH" || fail "truth table missing macOS Tier B"
    grep -q "Not safe to claim now" "$TRUTH" || fail "truth table missing launch guardrails section"
else
    warn "docs/PLATFORM-TRUTH-TABLE.md missing — skipping truth table checks"
fi

pass "release claim gates"
