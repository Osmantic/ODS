#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"

usage() {
  pixel_die "Usage: ./pixel update-archive --preview --candidate-id ID --allowed-signers ABSOLUTE_PATH --identity IDENTITY --activation-hash FULL_SHA256 | ./pixel update-archive --candidate-id ID --allowed-signers ABSOLUTE_PATH --identity IDENTITY --activation-hash FULL_SHA256 --archive-hash FULL_SHA256 --confirm"
}

pixel_load_env
pixel_require_linux
pixel_acquire_deployment_lock exclusive
staging_root="$PIXEL_INSTALL_DIR/update-staging"
archive_root="$PIXEL_INSTALL_DIR/update-archive"
pixel_safe_absolute_dir "$staging_root" PIXEL_UPDATE_STAGING_ROOT
active_version_file="$PIXEL_INSTALL_DIR/current/VERSION"
rollback_marker="$OPENCLAW_HOME/backups/last-apply"
[[ -f "$active_version_file" && ! -L "$active_version_file" ]] || pixel_die "The active Pixel version is unavailable or unsafe"

if [[ ${1:-} == --preview ]]; then
  shift
  exec python3 "$ROOT/scripts/release-update.py" archive-preview \
    "$@" \
    --staging-root "$staging_root" --archive-root "$archive_root" \
    --active-version-file "$active_version_file" --rollback-marker "$rollback_marker"
fi

candidate_id=""
allowed_signers=""
identity=""
activation_hash=""
archive_hash=""
confirmed=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --candidate-id)
      [[ -z "$candidate_id" && $# -ge 2 ]] || usage
      candidate_id=$2; shift 2 ;;
    --allowed-signers)
      [[ -z "$allowed_signers" && $# -ge 2 ]] || usage
      allowed_signers=$2; shift 2 ;;
    --identity)
      [[ -z "$identity" && $# -ge 2 ]] || usage
      identity=$2; shift 2 ;;
    --activation-hash)
      [[ -z "$activation_hash" && $# -ge 2 ]] || usage
      activation_hash=$2; shift 2 ;;
    --archive-hash)
      [[ -z "$archive_hash" && $# -ge 2 ]] || usage
      archive_hash=$2; shift 2 ;;
    --confirm)
      [[ $confirmed == 0 ]] || usage
      confirmed=1; shift ;;
    *) usage ;;
  esac
done
[[ "$candidate_id" =~ ^pixel-[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}-[0-9a-f]{64}$ ]] || usage
[[ "$allowed_signers" == /* && -n "$identity" ]] || usage
[[ "$activation_hash" =~ ^[0-9a-f]{64}$ && "$archive_hash" =~ ^[0-9a-f]{64}$ && $confirmed == 1 ]] || usage

exec python3 "$ROOT/scripts/release-update.py" archive \
  --staging-root "$staging_root" --archive-root "$archive_root" \
  --candidate-id "$candidate_id" --allowed-signers "$allowed_signers" --identity "$identity" \
  --activation-hash "$activation_hash" --archive-hash "$archive_hash" \
  --active-version-file "$active_version_file" --rollback-marker "$rollback_marker" --confirm
