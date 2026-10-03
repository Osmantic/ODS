#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ $# == 1 ]] || pixel_die "Usage: ./pixel frontier-show JOB_ID"
job_id=$1
[[ "$job_id" =~ ^frontier-[0-9]{13}-[a-f0-9]{12}$ ]] || pixel_die "Unsafe Frontier job ID"
pixel_load_env
[[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 ]] || pixel_die "Frontier limb is disabled"
sudo jq -S . "$PIXEL_FRONTIER_BROKER_STATE_DIR/plans/$job_id.json"
if sudo test -f "$PIXEL_FRONTIER_RESULT_DIR/$job_id.json"; then sudo jq -S . "$PIXEL_FRONTIER_RESULT_DIR/$job_id.json"; fi
