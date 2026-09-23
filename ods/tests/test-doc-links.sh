#!/usr/bin/env bash
# Validate links in all publishable Markdown, using the shared Pixel parser.
# Run from either repository root or ods/. Requires Node.js and Git.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

check_contributing_workdirs() {
  local command workdir
  while IFS= read -r command; do
    command="${command%$'\r'}"
    workdir="${command#cd }"
    workdir="${workdir%% &&*}"
    [[ -d "$ROOT_DIR/$workdir" ]] || {
      echo "[FAIL] CONTRIBUTING.md uses a missing working directory: $workdir"
      return 1
    }
  done < <(grep -E '^cd [^/~$]+' "$ROOT_DIR/CONTRIBUTING.md")
}

node --test "$ROOT_DIR/tests/test-doc-links.test.mjs"
node "$ROOT_DIR/scripts/check-doc-links.mjs"
check_contributing_workdirs
echo "[PASS] Local links and Markdown fragments resolve throughout the public Git file inventory"
