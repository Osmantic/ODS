#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ $# == 1 ]] || pixel_die "Usage: ./pixel ops-show JOB_ID"
job_id=$1
[[ "$job_id" =~ ^ops-[0-9]{13}-[a-f0-9]{12}$ ]] || pixel_die "Unsafe Operations job ID"
pixel_load_env
sudo jq -S . "$PIXEL_OPS_BROKER_STATE_DIR/plans/$job_id.json"
if sudo test -f "$PIXEL_OPS_RESULT_DIR/$job_id.json"; then sudo jq -S . "$PIXEL_OPS_RESULT_DIR/$job_id.json"; fi
