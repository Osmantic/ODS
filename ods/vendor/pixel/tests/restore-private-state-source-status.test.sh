#!/usr/bin/env bash
# The snippets below are intentionally literal search patterns for the script under test.
# shellcheck disable=SC2016
set -euo pipefail
# verify.sh requires the Source Broker's status.json. A backup taken before 4.3.34 has none,
# and restore restarts only the refresh timer, so restore must run one refresh itself
# after the services restart and before it verifies the restored state.
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT="$SOURCE/scripts/restore-private-state.sh"

fail() { echo "restore-private-state source-status regression: $*" >&2; exit 1; }
first_line() { grep -Fn -m1 "$1" "$2" | cut -d: -f1; }

restarts=$(first_line 'for unit in "${units[@]}"; do pixel_systemctl restart "$unit" >/dev/null; done' "$SCRIPT")
guard=$(first_line 'if [[ ${PIXEL_SOURCE_BROKER_ENABLED:-0} == 1 ]]; then' "$SCRIPT")
refresh=$(first_line 'pixel_systemctl start "${PIXEL_SOURCE_BROKER_UNIT:-pixel-source-broker.service}" >/dev/null' "$SCRIPT")
verify=$(first_line 'if ! bash "$ROOT/scripts/verify.sh" >/dev/null; then' "$SCRIPT")
for marker in restarts guard refresh verify; do
  [[ -n ${!marker} && ${!marker} -gt 0 ]] || fail "could not locate structural marker $marker"
done
[[ "$refresh" -gt "$restarts" ]] || fail "the refresh runs before the services restart"
[[ "$refresh" -lt "$verify" ]] || fail "verify runs before the Source Broker refresh"
[[ "$guard" -lt "$refresh" && "$((refresh - guard))" -le 2 ]] || fail "the refresh is not limited to an enabled Source Broker"
echo "restore-private-state source-status regression passed"
