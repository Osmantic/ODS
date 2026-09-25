#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ $# == 0 ]] || pixel_die "Usage: ./pixel frontier-usage"
pixel_load_env
[[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 ]] || pixel_die "Frontier limb is disabled"
usage_path="$PIXEL_FRONTIER_BROKER_STATE_DIR/metrics/usage.json"
sudo test -f "$usage_path" || pixel_die "Frontier usage summary is unavailable; check the broker service"
sudo jq -S . "$usage_path"
