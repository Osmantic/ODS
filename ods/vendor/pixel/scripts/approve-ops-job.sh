#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ $# == 3 && $3 == --confirm ]] || pixel_die "Usage: ./pixel ops-approve JOB_ID PLAN_SHA256 --confirm"
job_id=$1
plan_hash=$2
[[ "$job_id" =~ ^ops-[0-9]{13}-[a-f0-9]{12}$ ]] || pixel_die "Unsafe Operations job ID"
[[ "$plan_hash" =~ ^[a-f0-9]{64}$ ]] || pixel_die "Plan hash must be one lowercase SHA-256 value"
pixel_load_env
[[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 ]] || pixel_die "Operations limb is disabled"
pixel_begin_human_approval_session "An Operations plan"
[[ -x /usr/bin/jq && ! -L /usr/bin/jq ]] || pixel_die "Pixel requires the trusted JSON renderer at /usr/bin/jq"
plan_path="$PIXEL_OPS_BROKER_STATE_DIR/plans/$job_id.json"
pixel_log "Review the complete protected Operations plan below."
# jq variables are supplied with --arg.
# shellcheck disable=SC2016
"$PIXEL_HUMAN_APPROVAL_SUDO" /usr/bin/jq -aS -e --arg job "$job_id" --arg hash "$plan_hash" \
  'select(.jobId == $job and .planHash == $hash)' "$plan_path" ||
  pixel_die "Protected Operations plan is missing, malformed, or does not match the supplied job and hash"
pixel_confirm_human_approval "the exact Operations plan" "$job_id" "$plan_hash"
pixel_log "Approving only job=$job_id planHash=$plan_hash"
"$PIXEL_HUMAN_APPROVAL_SUDO" -u "$PIXEL_OPS_BROKER_USER" "$PIXEL_OPS_BROKER_INSTALL_DIR/broker.py" \
  --policy "$PIXEL_OPS_POLICY_PATH" --state "$PIXEL_OPS_BROKER_STATE_DIR" \
  --approve "$job_id" --plan-hash "$plan_hash"
