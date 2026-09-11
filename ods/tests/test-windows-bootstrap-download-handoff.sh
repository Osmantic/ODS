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

python3 - "$INSTALLER" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
handoff = text.index("Wait-ODSBootstrapDownloadHandoff")
download = text.index("Invoke-DownloadWithRetry", handoff)
if handoff >= download:
    raise SystemExit("handoff must run before the direct downloader")
PY

echo "PASS: Windows reinstall serializes with an active bootstrap download"
