#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
proposal=${1:-}
expected_hash=${2:-}
[[ ${3:-} == --confirm && $# == 3 ]] || pixel_die "Usage: ./pixel source-approve PROPOSAL_ID PROPOSAL_SHA256 --confirm"
[[ "$proposal" =~ ^calendar-[0-9]{13}-[a-f0-9]{8}$ ]] || pixel_die "Unsafe proposal ID"
[[ "$expected_hash" =~ ^[a-f0-9]{64}$ ]] || pixel_die "Proposal hash must be one lowercase SHA-256"
pixel_load_env
pixel_begin_human_approval_session "A protected source action"
for command in /usr/bin/systemctl /usr/bin/sha256sum /usr/bin/jq; do
  [[ -f "$command" && ! -L "$command" && -x "$command" ]] || pixel_die "Pixel requires trusted system command $command"
done
approved="$PIXEL_ACTION_RESULT_DIR/$proposal.approved.json"
if ! "$PIXEL_HUMAN_APPROVAL_SUDO" /usr/bin/test -f "$approved" || "$PIXEL_HUMAN_APPROVAL_SUDO" /usr/bin/test -L "$approved"; then
  pixel_die "Protected proposal snapshot is missing; run ./pixel source-show $proposal first"
fi
observed_hash=$("$PIXEL_HUMAN_APPROVAL_SUDO" /usr/bin/sha256sum "$approved")
observed_hash=${observed_hash%% *}
[[ "$observed_hash" == "$expected_hash" ]] || pixel_die "Proposal hash mismatch: expected $expected_hash, protected snapshot is $observed_hash"
pixel_log "Applying the protected proposal snapshot with SHA-256 $observed_hash:"
"$PIXEL_HUMAN_APPROVAL_SUDO" /usr/bin/jq -aS -e . "$approved" || pixel_die "Protected proposal snapshot is not valid JSON"
pixel_confirm_human_approval "the exact protected source action" "$proposal" "$expected_hash"
"$PIXEL_HUMAN_APPROVAL_SUDO" /usr/bin/systemctl start "pixel-source-action@$proposal.service"
"$PIXEL_HUMAN_APPROVAL_SUDO" /usr/bin/systemctl start "$PIXEL_SOURCE_BROKER_UNIT"
[[ -f "$PIXEL_ACTION_RESULT_DIR/$proposal.json" ]] || pixel_die "Actuator returned no result"
/usr/bin/jq -e --arg hash "$expected_hash" '.proposalSha256 == $hash and .approvalBinding == "sha256-protected-proposal-snapshot"' "$PIXEL_ACTION_RESULT_DIR/$proposal.json" >/dev/null || pixel_die "Actuator result is not bound to the approved proposal hash"
/usr/bin/cat "$PIXEL_ACTION_RESULT_DIR/$proposal.json"
