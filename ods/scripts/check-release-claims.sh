#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${ROOT_DIR}/manifest.json"
MATRIX="${ROOT_DIR}/docs/SUPPORT-MATRIX.md"
TRUTH="${ROOT_DIR}/docs/PLATFORM-TRUTH-TABLE.md"
JSON_OUTPUT=false
[[ "${1:-}" == "--json" ]] && JSON_OUTPUT=true
CHECKS_JSON='[]'
linux_supported=null
wsl_supported=null
macos_supported=null
windows_native_supported=null

record_check() {
    local status="$1" name="$2"
    command -v jq >/dev/null 2>&1 || return 0
    CHECKS_JSON=$(jq -c --arg status "$status" --arg name "$name" \
        '. + [{name: $name, status: $status}]' <<< "$CHECKS_JSON")
}

emit_json() {
    local ok="$1"
    jq -cn \
        --argjson ok "$ok" \
        --argjson checks "$CHECKS_JSON" \
        --argjson linux "$linux_supported" \
        --argjson windows_wsl2 "$wsl_supported" \
        --argjson macos "$macos_supported" \
        --argjson windows_native "$windows_native_supported" \
        '{ok: $ok, support: {linux: $linux, windows_wsl2: $windows_wsl2, macos: $macos, windows_native: $windows_native}, checks: $checks}'
}

fail() {
    record_check "fail" "$1"
    if $JSON_OUTPUT && command -v jq >/dev/null 2>&1; then emit_json false; else echo "[FAIL] $1"; fi
    exit 1
}
pass() {
    record_check "pass" "$1"
    $JSON_OUTPUT || echo "[PASS] $1"
}
checked() { record_check "pass" "$1"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"
test -f "$MANIFEST" || fail "manifest.json missing"
test -f "$MATRIX" || fail "docs/SUPPORT-MATRIX.md missing"
test -f "$TRUTH" || fail "docs/PLATFORM-TRUTH-TABLE.md missing"

# Manifest support expectations
linux_supported="$(jq -r '.compatibility.os.linux.supported' "$MANIFEST")"
wsl_supported="$(jq -r '.compatibility.os.windows_wsl2.supported' "$MANIFEST")"
macos_supported="$(jq -r '.compatibility.os.macos.supported' "$MANIFEST")"
windows_native_supported="$(jq -r '.compatibility.os.windows_native.supported' "$MANIFEST")"

[[ "$linux_supported" == "true" ]] || fail "manifest must mark linux supported"
checked "manifest marks Linux supported"
[[ "$wsl_supported" == "true" ]] || fail "manifest must mark windows_wsl2 supported"
checked "manifest marks Windows WSL2 supported"
[[ "$macos_supported" == "true" ]] || fail "manifest must mark macos supported (Tier B)"
checked "manifest marks macOS supported"
[[ "$windows_native_supported" == "false" ]] || fail "manifest must mark windows_native unsupported"
checked "manifest marks Windows native unsupported"

# Support matrix wording expectations
grep -q "Windows (Docker Desktop + WSL2).*Supported\|Windows (Docker Desktop + WSL2).*Tier B" "$MATRIX" || fail "support matrix missing Windows Tier B claim"
checked "support matrix includes Windows Tier B"
grep -q "macOS (Apple Silicon).*Supported\|macOS (Apple Silicon).*Tier B" "$MATRIX" || fail "support matrix missing macOS Tier B claim"
checked "support matrix includes macOS Tier B"
grep -q "install\.ps1" "$MATRIX" || fail "support matrix missing Windows installer reference"
checked "support matrix includes Windows installer"

# Truth table consistency
grep -q "Windows (Docker Desktop + WSL2).*Tier B" "$TRUTH" || fail "truth table missing Windows Tier B"
checked "truth table includes Windows Tier B"
grep -q "macOS Apple Silicon.*Tier B" "$TRUTH" || fail "truth table missing macOS Tier B"
checked "truth table includes macOS Tier B"
grep -q "Not safe to claim now" "$TRUTH" || fail "truth table missing launch guardrails section"
checked "truth table includes launch guardrails"

pass "release claim gates"
if $JSON_OUTPUT; then
    emit_json true
fi
