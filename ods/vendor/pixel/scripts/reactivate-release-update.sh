#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"

usage() {
  pixel_die "Usage: ./pixel update-reactivate --preview --candidate-id ID --allowed-signers ABSOLUTE_PATH --identity IDENTITY --activation-hash FULL_SHA256 | ./pixel update-reactivate --candidate-id ID --allowed-signers ABSOLUTE_PATH --identity IDENTITY --activation-hash FULL_SHA256 --reactivation-hash FULL_SHA256 --confirm"
}

pixel_load_env
pixel_require_linux
pixel_acquire_deployment_lock exclusive
staging_root="$PIXEL_INSTALL_DIR/update-staging"
pixel_safe_absolute_dir "$staging_root" PIXEL_UPDATE_STAGING_ROOT
active_version_file="$PIXEL_INSTALL_DIR/current/VERSION"
rollback_marker="$OPENCLAW_HOME/backups/last-apply"
[[ -f "$active_version_file" && ! -L "$active_version_file" ]] || pixel_die "The active Pixel version is unavailable or unsafe"

if [[ ${1:-} == --preview ]]; then
  shift
  exec python3 "$ROOT/scripts/release-update.py" reactivation-preview \
    "$@" --staging-root "$staging_root" --active-version-file "$active_version_file" \
    --rollback-marker "$rollback_marker"
fi

candidate_id=""
allowed_signers=""
identity=""
activation_hash=""
reactivation_hash=""
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
    --reactivation-hash)
      [[ -z "$reactivation_hash" && $# -ge 2 ]] || usage
      reactivation_hash=$2; shift 2 ;;
    --confirm)
      [[ $confirmed == 0 ]] || usage
      confirmed=1; shift ;;
    *) usage ;;
  esac
done
[[ "$candidate_id" =~ ^pixel-[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}-[0-9a-f]{64}$ ]] || usage
[[ "$allowed_signers" == /* && -n "$identity" ]] || usage
[[ "$activation_hash" =~ ^[0-9a-f]{64}$ && "$reactivation_hash" =~ ^[0-9a-f]{64}$ && $confirmed == 1 ]] || usage
pixel_require_interactive_terminal
candidate_version=${candidate_id#pixel-}
candidate_version=${candidate_version%%-*}
[[ "$candidate_version" == "$PIXEL_RELEASE_VERSION" ]] || pixel_die "Reactivation requires the previously activated Pixel controller"
[[ ${PIXEL_PRIVATE_ONBOARDING_PATH:-} == /* && -f "$PIXEL_PRIVATE_ONBOARDING_PATH" && ! -L "$PIXEL_PRIVATE_ONBOARDING_PATH" ]] || pixel_die "Private onboarding configuration is unavailable or unsafe"
[[ $(realpath -e "$PIXEL_PRIVATE_ONBOARDING_PATH") == "$PIXEL_PRIVATE_ONBOARDING_PATH" ]] || pixel_die "Private onboarding configuration must not contain symbolic links"

python3 "$ROOT/scripts/release-update.py" reactivation-claim \
  --staging-root "$staging_root" --candidate-id "$candidate_id" \
  --allowed-signers "$allowed_signers" --identity "$identity" \
  --activation-hash "$activation_hash" --reactivation-hash "$reactivation_hash" \
  --active-version-file "$active_version_file" --rollback-marker "$rollback_marker" --confirm

reactivation_root="$staging_root/reactivations/$candidate_id"
reactivation_attempt="$reactivation_root"
if [[ -d "$staging_root/reactivation-attempts/$candidate_id/$reactivation_hash" ]]; then
  reactivation_attempt="$staging_root/reactivation-attempts/$candidate_id/$reactivation_hash"
fi
source_root="$reactivation_attempt/source"
live_mutation_marker="$reactivation_attempt/LIVE-MUTATION-STARTED"
[[ $(realpath -e "$reactivation_attempt") == "$reactivation_attempt" ]] || pixel_die "Claimed reactivation directory is unsafe"
[[ $(realpath -e "$source_root") == "$source_root" ]] || pixel_die "Claimed reactivation source is unsafe"
[[ -f "$reactivation_attempt/REACTIVATION.json" && ! -L "$reactivation_attempt/REACTIVATION.json" ]] || pixel_die "Reactivation claim is unavailable or unsafe"
[[ ! -e "$live_mutation_marker" && ! -L "$live_mutation_marker" ]] || pixel_die "Reactivation live-mutation marker already exists"
node_bin=$(realpath -e "$(command -v node)") || pixel_die "Trusted Node runtime is unavailable"
bash_bin=$(realpath -e "$(command -v bash)") || pixel_die "Trusted Bash runtime is unavailable"
[[ "$node_bin" != "$source_root"/* && "$bash_bin" != "$source_root"/* ]] || pixel_die "Candidate-controlled interpreters are denied"

phase=configure
candidate_started=0
record_failure() {
  local status=$?
  trap - EXIT
  if [[ $status != 0 && $candidate_started == 1 ]]; then
    local -a result_args=(
      reactivation-result --staging-root "$staging_root" --candidate-id "$candidate_id"
      --allowed-signers "$allowed_signers" --identity "$identity"
      --activation-hash "$activation_hash" --reactivation-hash "$reactivation_hash"
      --outcome failed --phase "$phase" --active-version-file "$active_version_file" --confirm
    )
    if [[ -f "$rollback_marker" && ! -L "$rollback_marker" ]]; then
      result_args+=(--rollback-marker "$rollback_marker")
    fi
    python3 "$ROOT/scripts/release-update.py" "${result_args[@]}" >/dev/null \
      || pixel_warn "Reactivation failed and its content-free result could not be recorded; preserve update staging for recovery"
  fi
  exit "$status"
}
trap record_failure EXIT

candidate_started=1
"$node_bin" "$source_root/scripts/configure.mjs" --answers "$PIXEL_PRIVATE_ONBOARDING_PATH" --force
phase=bootstrap
"$bash_bin" "$source_root/scripts/bootstrap.sh" --apply
phase=plan
"$bash_bin" "$source_root/scripts/plan.sh"
phase=apply
PIXEL_RELEASE_UPDATE_LIVE_MUTATION_MARKER="$live_mutation_marker" \
  "$bash_bin" "$source_root/scripts/apply.sh" --confirm
phase=verify
IFS= read -r active_after < "$active_version_file"
[[ "$active_after" == "$candidate_version" ]] || pixel_die "Reactivation completed without the claimed Pixel version becoming active"
phase=record
result=$(python3 "$ROOT/scripts/release-update.py" reactivation-result \
  --staging-root "$staging_root" --candidate-id "$candidate_id" \
  --allowed-signers "$allowed_signers" --identity "$identity" \
  --activation-hash "$activation_hash" --reactivation-hash "$reactivation_hash" \
  --outcome reactivated --phase record --active-version-file "$active_version_file" \
  --rollback-marker "$rollback_marker" --confirm)
trap - EXIT
printf '%s\n' "$result"
