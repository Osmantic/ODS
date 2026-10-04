#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$ROOT_DIR/src-tauri/src/installer.rs"

grep -q 'Unable to persist installer progress' "$INSTALLER"
grep -q 'update_progress(&state' "$INSTALLER"
grep -q 's.save().map_err' "$INSTALLER"
grep -q 'update_progress(&state, "Downloading ODS", 5)?' "$INSTALLER"

echo "[PASS] installer progress persistence failures are propagated"
