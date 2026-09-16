#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT_DIR/ods/ods-update.sh"

grep -q 'update_branch=$(git branch --show-current' "$SCRIPT" \
    || { echo "FAIL: updater does not resolve the checked-out branch" >&2; exit 1; }
grep -q 'git pull --ff-only origin "$update_branch"' "$SCRIPT" \
    || { echo "FAIL: updater does not pull the resolved branch fast-forward-only" >&2; exit 1; }
grep -q 'Cannot update a detached checkout safely' "$SCRIPT" \
    || { echo "FAIL: updater does not fail closed for detached checkouts" >&2; exit 1; }
if grep -q 'git pull origin main' "$SCRIPT" || grep -q 'git pull origin master' "$SCRIPT"; then
    echo "FAIL: updater still contains hard-coded main/master pulls" >&2
    exit 1
fi

echo "PASS: ods-update follows the checked-out branch and rejects detached updates"
