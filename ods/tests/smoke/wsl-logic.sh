#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[smoke] WSL dispatch logic"
grep -q "linux|wsl" installers/dispatch.sh
grep -q "Windows (Docker Desktop + WSL2)" docs/SUPPORT-MATRIX.md
grep -q 'install\.ps1' docs/SUPPORT-MATRIX.md

echo "[smoke] PASS wsl-logic"
