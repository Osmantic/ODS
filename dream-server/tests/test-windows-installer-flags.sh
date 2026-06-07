#!/usr/bin/env bash
# ============================================================================
# Dream Server Windows installer flag parity tests
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
ROOT_INSTALLER="$REPO_ROOT/install.ps1"
WINDOWS_INSTALLER="$ROOT_DIR/installers/windows/install-windows.ps1"
WINDOWS_QUICKSTART="$ROOT_DIR/docs/WINDOWS-QUICKSTART.md"
WINDOWS_WALKTHROUGH="$ROOT_DIR/docs/WINDOWS-INSTALL-WALKTHROUGH.md"

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
echo "=== Windows installer flag parity tests ==="
echo ""

[[ -f "$ROOT_INSTALLER" ]] && pass "root Windows installer wrapper exists" || fail "root Windows installer wrapper missing"
[[ -f "$WINDOWS_INSTALLER" ]] && pass "Windows installer orchestrator exists" || fail "Windows installer orchestrator missing"

for flag in Hermes NoHermes Langfuse NoLangfuse NoBootstrap; do
    check "[switch]\$$flag" "$ROOT_INSTALLER" "root wrapper forwards -$flag"
done

check "[switch]\$NoBootstrap" "$WINDOWS_INSTALLER" "Windows installer exposes -NoBootstrap"
check '$noBootstrapFlag = $NoBootstrap.IsPresent' "$WINDOWS_INSTALLER" "Windows installer captures -NoBootstrap"
check '-NoBootstrap $noBootstrapFlag' "$WINDOWS_INSTALLER" "Windows bootstrap decision receives -NoBootstrap"
check '[string]$InstallDir = ""' "$ROOT_INSTALLER" "root wrapper exposes -InstallDir"
check '[string]$InstallDir = ""' "$WINDOWS_INSTALLER" "Windows installer exposes -InstallDir"
check '$env:DREAM_HOME = [System.IO.Path]::GetFullPath($InstallDir)' "$WINDOWS_INSTALLER" "Windows installer maps -InstallDir to DREAM_HOME before constants"
check 'Write-InfoBox "Install target:" "$installDir"' "$ROOT_DIR/installers/windows/phases/01-preflight.ps1" "Windows preflight prints install target"
check 'Install target checked: $installDir' "$ROOT_DIR/installers/windows/phases/01-preflight.ps1" "Windows initial disk gate prints install target"
check 'Install target checked: $installDir' "$ROOT_DIR/installers/windows/phases/02-detection.ps1" "Windows tier disk gate prints install target"
check '.\install.ps1 -InstallDir $_installDirHint' "$ROOT_DIR/installers/windows/phases/01-preflight.ps1" "Windows initial disk gate prints install-dir rerun hint"
check '.\install.ps1 -InstallDir $_installDirHint' "$ROOT_DIR/installers/windows/phases/02-detection.ps1" "Windows tier disk gate prints install-dir rerun hint"
check '.\install.ps1 -InstallDir $_installDirHint' "$ROOT_DIR/installers/windows/phases/04-requirements.ps1" "Windows requirements disk gate prints install-dir rerun hint"
check '$_installDirHint = "<path-with-enough-space>\dream-server"' "$ROOT_DIR/installers/windows/phases/04-requirements.ps1" "Windows disk warning uses a generic install-dir hint"
check 'if ($continueChoice -notmatch "^[yY]")' "$ROOT_DIR/installers/windows/phases/04-requirements.ps1" "Windows requirements default prompt rejects Enter"
check 'exit 1' "$ROOT_DIR/installers/windows/phases/04-requirements.ps1" "Windows requirements prompt aborts on default No"

check '| `-NoHermes` | Disable Hermes Agent |' "$WINDOWS_QUICKSTART" "Windows quickstart documents -NoHermes"
check '| `-NoBootstrap` | Wait for the full model before launching |' "$WINDOWS_QUICKSTART" "Windows quickstart documents -NoBootstrap"
check '| `-InstallDir <path>` | Install runtime files on a specific drive/path |' "$WINDOWS_QUICKSTART" "Windows quickstart documents -InstallDir"
check '$installDir = "D:\Apps\dream-server"' "$WINDOWS_QUICKSTART" "Windows quickstart uses installDir variable for custom path"
check 'cd $installDir' "$WINDOWS_QUICKSTART" "Windows quickstart uses installDir for management commands"
check '$installDir = "D:\Apps\dream-server"' "$WINDOWS_WALKTHROUGH" "Windows walkthrough uses installDir variable for custom path"
check 'cd $installDir' "$WINDOWS_WALKTHROUGH" "Windows walkthrough uses installDir for management commands"
check 'Models | `$installDir\data\models\`' "$WINDOWS_QUICKSTART" "Windows quickstart files table uses installDir for models"

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
