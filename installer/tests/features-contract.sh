#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
features="$root/src/pages/Features.tsx"
commands="$root/src-tauri/src/commands.rs"

if grep -q 'id: "chat"' "$features"; then
  echo "chat must not be submitted as an installer feature"
  exit 1
fi
if grep -q 'features.*chat' "$features"; then
  echo "chat must not be included in the selected feature payload"
  exit 1
fi
grep -q 'ALLOWED_FEATURES' "$commands"
echo "installer feature contract passed"
