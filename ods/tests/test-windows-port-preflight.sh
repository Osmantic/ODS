#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v powershell.exe >/dev/null 2>&1; then
    PS_BIN="powershell.exe"
elif command -v pwsh >/dev/null 2>&1; then
    PS_BIN="pwsh"
else
    echo "[SKIP] PowerShell unavailable"
    exit 0
fi

if command -v cygpath >/dev/null 2>&1; then
    TEST_PATH="$(cygpath -w "$ROOT_DIR/tests/test-windows-port-preflight.ps1")"
elif [[ "$PS_BIN" == "powershell.exe" ]] && command -v wslpath >/dev/null 2>&1; then
    TEST_PATH="$(wslpath -w "$ROOT_DIR/tests/test-windows-port-preflight.ps1")"
else
    TEST_PATH="$ROOT_DIR/tests/test-windows-port-preflight.ps1"
fi

"$PS_BIN" -NoProfile -ExecutionPolicy Bypass -File "$TEST_PATH"
