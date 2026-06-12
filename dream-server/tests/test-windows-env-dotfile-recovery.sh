#!/usr/bin/env bash
# ============================================================================
# Dream Server Windows malformed dotfile recovery tests
# ============================================================================
# Static regression for partial Windows installs that leave install-root
# dotfiles as directories. The installer must not try to read .env as a file
# during phase 2, and phase 6/env generation must repair the owned paths.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PHASE02="$ROOT_DIR/installers/windows/phases/02-detection.ps1"
PHASE05="$ROOT_DIR/installers/windows/phases/05-docker.ps1"
PHASE06="$ROOT_DIR/installers/windows/phases/06-directories.ps1"
ENVGEN="$ROOT_DIR/installers/windows/lib/env-generator.ps1"
LLM_ENDPOINT="$ROOT_DIR/installers/windows/lib/llm-endpoint.ps1"

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
echo "=== Windows malformed dotfile recovery tests ==="
echo ""

[[ -f "$PHASE02" ]] && pass "Windows detection phase exists" || fail "Windows detection phase missing"
[[ -f "$PHASE05" ]] && pass "Windows docker phase exists" || fail "Windows docker phase missing"
[[ -f "$PHASE06" ]] && pass "Windows directories phase exists" || fail "Windows directories phase missing"
[[ -f "$ENVGEN" ]] && pass "Windows env generator exists" || fail "Windows env generator missing"
[[ -f "$LLM_ENDPOINT" ]] && pass "Windows LLM endpoint helper exists" || fail "Windows LLM endpoint helper missing"

check 'Test-Path -LiteralPath $existingEnvPath -PathType Leaf' "$PHASE02" "phase 2 only reads .env when it is a file"
check 'Test-Path -LiteralPath $existingEnvPath -PathType Container' "$PHASE02" "phase 2 detects malformed .env directory"
check 'Ignoring malformed .env directory from a previous partial install.' "$PHASE02" "phase 2 warns and defaults profile for malformed .env"

check 'docker rm -f @_dreamContainerNames' "$PHASE05" "phase 5 removes stale Dream containers after Docker is available"
check 'file bind mounts' "$PHASE05" "phase 5 documents stale bind-mount directory hazard"

check '$_expectedRegularFiles = @(' "$PHASE06" "phase 6 uses explicit owned regular-file cleanup list"
check 'extensions\services\hermes\cli-config.yaml.template' "$PHASE06" "phase 6 repairs malformed Hermes config template directory"
check 'extensions\services\hermes\SOUL.md.template' "$PHASE06" "phase 6 repairs malformed Hermes SOUL template directory"
check 'data\persona\SOUL.md' "$PHASE06" "phase 6 repairs malformed generated persona file directory"
check 'Test-Path -LiteralPath $_expectedFilePath -PathType Container' "$PHASE06" "phase 6 detects dotfile directories"
check 'Remove-Item -LiteralPath $_expectedFilePath -Recurse -Force' "$PHASE06" "phase 6 removes malformed regular-file directories before copy"

check 'Test-Path -LiteralPath $envPath -PathType Container' "$ENVGEN" "env generator detects malformed .env directory"
check 'Remove-Item -LiteralPath $envPath -Recurse -Force' "$ENVGEN" "env generator removes malformed .env directory before writing"
check 'Write-Utf8NoBom -Path $envPath -Content $envContent' "$ENVGEN" "env generator writes .env after recovery"

check 'Test-Path -LiteralPath $Path -PathType Leaf' "$LLM_ENDPOINT" "shared env parser only reads .env when it is a file"
check 'Get-Content -LiteralPath $Path -ErrorAction Stop' "$LLM_ENDPOINT" "shared env parser reads .env defensively"
check 'return @{}' "$LLM_ENDPOINT" "shared env parser fails closed for unreadable .env paths"

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
