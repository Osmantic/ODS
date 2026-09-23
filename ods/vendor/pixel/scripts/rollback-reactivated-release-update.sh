#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"

usage() {
  pixel_die "Usage: ./pixel update-reactivation-rollback --preview --candidate-id ID --allowed-signers ABSOLUTE_PATH --identity IDENTITY --activation-hash FULL_SHA256 --reactivation-hash FULL_SHA256 | ./pixel update-reactivation-rollback --candidate-id ID --allowed-signers ABSOLUTE_PATH --identity IDENTITY --activation-hash FULL_SHA256 --reactivation-hash FULL_SHA256 --rollback-hash FULL_SHA256 --confirm"
}

pixel_load_env
pixel_require_linux
pixel_acquire_deployment_lock exclusive
staging_root="$PIXEL_INSTALL_DIR/update-staging"
pixel_safe_absolute_dir "$staging_root" PIXEL_UPDATE_STAGING_ROOT
active_version_file="$PIXEL_INSTALL_DIR/current/VERSION"
rollback_marker="$OPENCLAW_HOME/backups/last-apply"

if [[ ${1:-} == --preview ]]; then
  shift
  exec python3 "$ROOT/scripts/release-update.py" reactivation-rollback-preview \
    "$@" --staging-root "$staging_root" --active-version-file "$active_version_file" \
    --rollback-marker "$rollback_marker"
fi

candidate_id=""
allowed_signers=""
identity=""
activation_hash=""
reactivation_hash=""
rollback_hash=""
confirmed=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --candidate-id) [[ -z "$candidate_id" && $# -ge 2 ]] || usage; candidate_id=$2; shift 2 ;;
    --allowed-signers) [[ -z "$allowed_signers" && $# -ge 2 ]] || usage; allowed_signers=$2; shift 2 ;;
    --identity) [[ -z "$identity" && $# -ge 2 ]] || usage; identity=$2; shift 2 ;;
    --activation-hash) [[ -z "$activation_hash" && $# -ge 2 ]] || usage; activation_hash=$2; shift 2 ;;
    --reactivation-hash) [[ -z "$reactivation_hash" && $# -ge 2 ]] || usage; reactivation_hash=$2; shift 2 ;;
    --rollback-hash) [[ -z "$rollback_hash" && $# -ge 2 ]] || usage; rollback_hash=$2; shift 2 ;;
    --confirm) [[ $confirmed == 0 ]] || usage; confirmed=1; shift ;;
    *) usage ;;
  esac
done
[[ "$candidate_id" =~ ^pixel-[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}-[0-9a-f]{64}$ ]] || usage
[[ "$allowed_signers" == /* && -n "$identity" ]] || usage
[[ "$activation_hash" =~ ^[0-9a-f]{64}$ && "$reactivation_hash" =~ ^[0-9a-f]{64}$ ]] || usage
[[ "$rollback_hash" =~ ^[0-9a-f]{64}$ && $confirmed == 1 ]] || usage
pixel_require_interactive_terminal
[[ -f "$active_version_file" && ! -L "$active_version_file" ]] || pixel_die "The active Pixel version is unavailable or unsafe"
[[ -f "$rollback_marker" && ! -L "$rollback_marker" ]] || pixel_die "The reactivated update rollback marker is unavailable or unsafe"

python3 "$ROOT/scripts/release-update.py" reactivation-rollback-claim \
  --staging-root "$staging_root" --candidate-id "$candidate_id" \
  --allowed-signers "$allowed_signers" --identity "$identity" \
  --activation-hash "$activation_hash" --reactivation-hash "$reactivation_hash" \
  --rollback-hash "$rollback_hash" --active-version-file "$active_version_file" \
  --rollback-marker "$rollback_marker" --confirm

rollback_started=0
record_failure() {
  local status=$?
  trap - EXIT
  if [[ $status != 0 && $rollback_started == 1 ]]; then
    python3 "$ROOT/scripts/release-update.py" reactivation-rollback-result \
      --staging-root "$staging_root" --candidate-id "$candidate_id" \
      --allowed-signers "$allowed_signers" --identity "$identity" \
      --activation-hash "$activation_hash" --reactivation-hash "$reactivation_hash" \
      --rollback-hash "$rollback_hash" --active-version-file "$active_version_file" \
      --rollback-marker "$rollback_marker" --outcome failed --phase rollback --confirm >/dev/null \
      || pixel_warn "Reactivation rollback failed and its receipt could not be recorded; preserve update staging for recovery"
  fi
  exit "$status"
}
trap record_failure EXIT
rollback_started=1
bash "$ROOT/scripts/rollback.sh" --confirm
result=$(python3 "$ROOT/scripts/release-update.py" reactivation-rollback-result \
  --staging-root "$staging_root" --candidate-id "$candidate_id" \
  --allowed-signers "$allowed_signers" --identity "$identity" \
  --activation-hash "$activation_hash" --reactivation-hash "$reactivation_hash" \
  --rollback-hash "$rollback_hash" --active-version-file "$active_version_file" \
  --rollback-marker "$rollback_marker" --outcome rolled-back --phase record --confirm)
trap - EXIT
printf '%s\n' "$result"
