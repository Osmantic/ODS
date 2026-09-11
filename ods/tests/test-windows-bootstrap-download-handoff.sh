#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UI="$ROOT/installers/windows/lib/ui.ps1"
INSTALLER="$ROOT/installers/windows/install-windows.ps1"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

grep -Fq 'function Test-ODSBootstrapUpgradeActive' "$UI" \
    || fail "Windows download helper must detect a live bootstrap owner"
grep -Fq 'Get-ScheduledTask -TaskName "ODSModelUpgrade"' "$UI" \
    || fail "normal scheduled bootstrap downloads must be detected"
grep -Fq '$arguments.Contains($wrapperNeedle)' "$UI" \
    || fail "a running scheduled task must be scoped to this install"
grep -Fq '$wrapperContent.Contains($modelNeedle)' "$UI" \
    || fail "a running scheduled task must own the requested model"
grep -Fq 'Get-CimInstance Win32_Process' "$UI" \
    || fail "direct-launch bootstrap fallback must be detected"
grep -Fq 'function Wait-ODSBootstrapDownloadHandoff' "$UI" \
    || fail "the synchronous installer must wait for an active owner"
grep -Fq 'waiting for a safe handoff instead of opening the same .part file twice' "$UI" \
    || fail "the user-facing handoff must explain why reinstall is waiting"
grep -Fq 'ODS_BOOTSTRAP_HANDOFF_WAIT_SECONDS' "$INSTALLER" \
    || fail "handoff waiting must be bounded and configurable"
grep -Fq 'Refusing to race the active bootstrap downloader' "$INSTALLER" \
    || fail "timeout must fail closed instead of racing the shared partial"
grep -Fq '$normalized.Contains($bashInstallNeedle)' "$UI" \
    || fail "direct Git Bash launch detection must normalize Windows drive paths"

python3 - "$INSTALLER" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
block = text[text.index("# ── Download GGUF model") :]
integrity = block.index("Test-ModelIntegrity")
handoff = block.index("Wait-ODSBootstrapDownloadHandoff")
download = block.index("Invoke-DownloadWithRetry", handoff)
if not integrity < handoff < download:
    raise SystemExit("integrity validation must precede handoff, which must precede direct download")
PY

if command -v pwsh >/dev/null 2>&1; then
    pwsh -NoProfile -File "$ROOT/tests/test-windows-bootstrap-download-handoff.ps1"
elif command -v powershell.exe >/dev/null 2>&1 && command -v cygpath >/dev/null 2>&1; then
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -aw "$ROOT/tests/test-windows-bootstrap-download-handoff.ps1")"
else
    echo "SKIP: PowerShell runtime unavailable"
fi

echo "PASS: Windows reinstall serializes with an active bootstrap download"
