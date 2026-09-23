#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
echo "scripts/install.sh is retained for compatibility; use ./pixel plan and ./pixel apply --confirm." >&2
bash "$ROOT/scripts/plan.sh"
if [[ ${APPLY_CONFIG:-0} == 1 ]]; then exec bash "$ROOT/scripts/apply.sh" --confirm; fi
