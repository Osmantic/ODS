#!/usr/bin/env bash
# Contract: every GitHub Actions workflow must declare an explicit `permissions:`
# block. Without one, GITHUB_TOKEN inherits the repository default (commonly
# read-write), giving every CI job far more access than it needs.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WF_DIR="$ROOT_DIR/.github/workflows"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

missing=0
count=0
for wf in "$WF_DIR"/*.yml "$WF_DIR"/*.yaml; do
    [[ -f "$wf" ]] || continue
    count=$((count + 1))
    if ! grep -qE '^permissions:' "$wf"; then
        echo "  missing top-level permissions: $(basename "$wf")" >&2
        missing=$((missing + 1))
    fi
done

(( count > 0 )) || fail "no workflow files found under $WF_DIR"
(( missing == 0 )) || fail "$missing workflow(s) lack an explicit top-level permissions block"
pass "all $count workflows declare explicit GITHUB_TOKEN permissions"
