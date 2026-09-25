#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

usage() {
  echo "Usage: scripts/run-upstream-canary.sh --quarantine ABSOLUTE_PATH --evidence ABSOLUTE_PATH [--observation-seconds N]" >&2
  exit 2
}

quarantine=""
evidence=""
observation_seconds=1800
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quarantine) quarantine=${2:-}; shift 2 ;;
    --evidence) evidence=${2:-}; shift 2 ;;
    --observation-seconds) observation_seconds=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$quarantine" == /* && "$evidence" == /* ]] || usage
[[ "$observation_seconds" =~ ^[0-9]+$ && $observation_seconds -ge 1800 && $observation_seconds -le 7200 ]] || {
  echo "Canary observation must be between 1800 and 7200 seconds" >&2
  exit 2
}
case "$quarantine/" in "$ROOT/"*) echo "Quarantine must be outside the source repository" >&2; exit 2 ;; esac
case "$evidence/" in "$ROOT/"*) echo "Evidence must be outside the source repository" >&2; exit 2 ;; esac

"$ROOT/scripts/run-upstream-runtime-matrix.sh" \
  --quarantine "$quarantine" \
  --evidence "$evidence" \
  --systemd-only \
  --observation-seconds "$observation_seconds"

summary="$evidence/ubuntu-24-vm/canary-summary.json"
jq -e --argjson seconds "$observation_seconds" '
  .status == "pass"
  and .isolated == true
  and .rollbackRehearsed == true
  and .postRollbackHealthy == true
  and .observation.seconds == $seconds
  and .observation.minutes >= 30
  and (.syntheticChecks | all(.[]; . == "pass"))
' "$summary" >/dev/null
printf 'Pixel isolated canary passed; evidence=%s\n' "$summary"
