#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"

usage() {
  pixel_die "Usage: ./pixel update-activate --preview --candidate-id ID --allowed-signers ABSOLUTE_PATH --identity IDENTITY | ./pixel update-activate --candidate-id ID --allowed-signers ABSOLUTE_PATH --identity IDENTITY --activation-hash FULL_SHA256 --confirm"
}

pixel_load_env
pixel_require_linux
pixel_acquire_deployment_lock exclusive
staging_root="$PIXEL_INSTALL_DIR/update-staging"
pixel_safe_absolute_dir "$staging_root" PIXEL_UPDATE_STAGING_ROOT

if [[ ${1:-} == --preview ]]; then
  shift
  exec python3 "$ROOT/scripts/release-update.py" activation-preview "$@" --staging-root "$staging_root"
fi

candidate_id=""
allowed_signers=""
identity=""
activation_hash=""
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
    --confirm)
      [[ $confirmed == 0 ]] || usage
      confirmed=1; shift ;;
    *) usage ;;
  esac
done
[[ "$candidate_id" =~ ^pixel-[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}-[0-9a-f]{64}$ ]] || usage
[[ "$allowed_signers" == /* && -n "$identity" ]] || usage
[[ "$activation_hash" =~ ^[0-9a-f]{64}$ && $confirmed == 1 ]] || usage
pixel_require_interactive_terminal

active_version_file="$PIXEL_INSTALL_DIR/current/VERSION"
[[ -f "$active_version_file" && ! -L "$active_version_file" ]] || pixel_die "The active Pixel version is unavailable or unsafe"
IFS= read -r active_before < "$active_version_file"
[[ "$active_before" == "$PIXEL_RELEASE_VERSION" ]] || pixel_die "The active deployment differs from this trusted Pixel controller"
[[ ${PIXEL_PRIVATE_ONBOARDING_PATH:-} == /* && -f "$PIXEL_PRIVATE_ONBOARDING_PATH" && ! -L "$PIXEL_PRIVATE_ONBOARDING_PATH" ]] || pixel_die "Private onboarding configuration is unavailable or unsafe"
[[ $(realpath -e "$PIXEL_PRIVATE_ONBOARDING_PATH") == "$PIXEL_PRIVATE_ONBOARDING_PATH" ]] || pixel_die "Private onboarding configuration must not contain symbolic links"

python3 "$ROOT/scripts/release-update.py" activation-claim \
  --staging-root "$staging_root" --candidate-id "$candidate_id" \
  --allowed-signers "$allowed_signers" --identity "$identity" \
  --activation-hash "$activation_hash" --confirm

activation_root="$staging_root/activations/$candidate_id"
source_root="$activation_root/source"
[[ $(realpath -e "$activation_root") == "$activation_root" ]] || pixel_die "Claimed activation directory is unsafe"
[[ $(realpath -e "$source_root") == "$source_root" ]] || pixel_die "Claimed activation source is unsafe"
[[ -f "$activation_root/ACTIVATION.json" && ! -L "$activation_root/ACTIVATION.json" ]] || pixel_die "Activation claim is unavailable or unsafe"
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
      activation-result --staging-root "$staging_root" --candidate-id "$candidate_id"
      --allowed-signers "$allowed_signers" --identity "$identity"
      --activation-hash "$activation_hash" --outcome failed --phase "$phase"
      --active-version-file "$active_version_file" --confirm
    )
    if [[ -f "$OPENCLAW_HOME/backups/last-apply" && ! -L "$OPENCLAW_HOME/backups/last-apply" ]]; then
      result_args+=(--rollback-marker "$OPENCLAW_HOME/backups/last-apply")
    fi
    python3 "$ROOT/scripts/release-update.py" "${result_args[@]}" >/dev/null \
      || pixel_warn "Activation failed and its content-free result could not be recorded; preserve update staging for recovery"
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
"$bash_bin" "$source_root/scripts/apply.sh" --confirm
phase=verify
IFS= read -r active_after < "$active_version_file"
candidate_version=${candidate_id#pixel-}
candidate_version=${candidate_version%%-*}
[[ "$active_after" == "$candidate_version" ]] || pixel_die "Activation completed without the claimed Pixel version becoming active"
phase=record
result=$(python3 "$ROOT/scripts/release-update.py" activation-result \
  --staging-root "$staging_root" --candidate-id "$candidate_id" \
  --allowed-signers "$allowed_signers" --identity "$identity" \
  --activation-hash "$activation_hash" --outcome activated --phase record \
  --active-version-file "$active_version_file" \
  --rollback-marker "$OPENCLAW_HOME/backups/last-apply" --confirm)
trap - EXIT
printf '%s\n' "$result"
