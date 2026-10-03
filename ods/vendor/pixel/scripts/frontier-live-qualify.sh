#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
pixel_load_env
pixel_require_linux
[[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 ]] || pixel_die "Frontier limb is disabled"
for command in python3 sudo; do pixel_require_command "$command"; done
exec python3 "$ROOT/scripts/frontier-live-qualify.py" "$@"
