#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[smoke] macOS dispatch and support messaging"
test -f installers/macos.sh
grep -q "macos)" installers/dispatch.sh
grep -q "macOS" docs/SUPPORT-MATRIX.md

echo "[smoke] PASS macos-dispatch"
