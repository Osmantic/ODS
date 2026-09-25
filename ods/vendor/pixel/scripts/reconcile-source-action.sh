#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
proposal=${1:-}
[[ $# == 1 && "$proposal" =~ ^calendar-[0-9]{13}-[a-f0-9]{8}$ ]] || pixel_die "Usage: ./pixel source-reconcile PROPOSAL_ID"
pixel_load_env
for command in /usr/bin/systemctl /usr/bin/jq; do
  [[ -f "$command" && ! -L "$command" && -x "$command" ]] || pixel_die "Pixel requires trusted system command $command"
done
reconcile_sudo=/usr/bin/sudo
[[ -f "$reconcile_sudo" && ! -L "$reconcile_sudo" && -x "$reconcile_sudo" ]] || pixel_die "Pixel requires the trusted system administrator tool at /usr/bin/sudo"
[[ -n ${PIXEL_SOURCE_RECONCILE_UNIT:-} ]] || pixel_die "Missing setting: PIXEL_SOURCE_RECONCILE_UNIT"
unit=${PIXEL_SOURCE_RECONCILE_UNIT/@./@$proposal.}
result="$PIXEL_ACTION_RESULT_DIR/$proposal.json"
if ! "$reconcile_sudo" /usr/bin/systemctl start "$unit"; then
  [[ ! -f "$result" ]] || /usr/bin/jq -e . "$result"
  pixel_die "Calendar action remains indeterminate or reconciliation failed; it was not retried. Inspect: systemctl status $unit"
fi
[[ -f "$result" ]] || pixel_die "Calendar action remains indeterminate; it was not retried"
/usr/bin/jq -e --arg proposal "$proposal" \
  '.proposalId == $proposal and ((.status == "applied" and .action == "create" and .eventPrecondition == "deterministic-provider-event-id" and (.reconciliation == "provider-get-after-indeterminate-write" or .reconciliation == "synchronous-provider-response")) or (.action == "update" and (.status == "desired-state-observed" or .status == "not-applied") and .eventPrecondition == "if-match" and .retryAllowed == false and .causationAsserted == false and (.reconciliation == "legacy-provider-state-match-after-indeterminate-write" or .reconciliation == "legacy-provider-etag-proves-not-applied")))' \
  "$result" >/dev/null || pixel_die "Reconciled result does not prove the exact indeterminate Calendar outcome"
/usr/bin/cat "$result"
