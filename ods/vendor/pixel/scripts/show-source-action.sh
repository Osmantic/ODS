#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
proposal=${1:-}
[[ $# == 1 && "$proposal" =~ ^calendar-[0-9]{13}-[a-f0-9]{8}$ ]] || pixel_die "Usage: ./pixel source-show PROPOSAL_ID"
pixel_load_env
for command in sudo install id sha256sum awk python3; do pixel_require_command "$command"; done
source_path="$PIXEL_ACTION_PROPOSAL_DIR/$proposal.json"
approved_path="$PIXEL_ACTION_RESULT_DIR/$proposal.approved.json"
if ! sudo test -e "$approved_path"; then
  [[ -f "$source_path" && ! -L "$source_path" ]] || pixel_die "Proposal is missing or is not a regular file: $proposal"
  snapshot=$(mktemp)
  staged="$PIXEL_ACTION_RESULT_DIR/.$proposal.snapshot.$$"
  cleanup_snapshot() { rm -f -- "$snapshot"; sudo rm -f -- "$staged"; }
  trap cleanup_snapshot EXIT
  python3 "$ROOT/scripts/snapshot-proposal.py" "$source_path" "$snapshot" >/dev/null
  reader_group=$(id -gn "$PIXEL_SOURCE_READER_USER")
  sudo install -o "$PIXEL_SOURCE_BROKER_USER" -g "$reader_group" -m 0640 "$snapshot" "$staged"
  sudo mv -n -- "$staged" "$approved_path"
  cleanup_snapshot
  trap - EXIT
fi
if ! sudo test -f "$approved_path" || sudo test -L "$approved_path"; then
  pixel_die "Protected proposal snapshot is invalid"
fi
proposal_hash=$(sudo sha256sum "$approved_path" | awk '{print $1}')
pixel_log "Protected Calendar proposal snapshot:"
sudo cat "$approved_path"
printf '\nSHA-256: %s\nApprove only this snapshot with:\n  ./pixel source-approve %s %s --confirm\n' "$proposal_hash" "$proposal" "$proposal_hash"
