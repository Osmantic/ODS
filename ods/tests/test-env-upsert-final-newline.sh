#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for file in ods-cli scripts/mode-switch.sh installers/macos/ods-macos.sh installers/macos/lib/env-generator.sh; do
    bash -n "$ROOT_DIR/$file"
done

grep -q 'tail -c 1' "$ROOT_DIR/ods-cli"
grep -q 'tail -c 1' "$ROOT_DIR/scripts/mode-switch.sh"
grep -q 'tail -c 1' "$ROOT_DIR/installers/macos/ods-macos.sh"
grep -q 'tail -c 1' "$ROOT_DIR/installers/macos/lib/env-generator.sh"
grep -q 'read -r line ||' "$ROOT_DIR/installers/macos/ods-macos.sh"

echo "[PASS] env upserts and macOS reads preserve final non-newline entries"
