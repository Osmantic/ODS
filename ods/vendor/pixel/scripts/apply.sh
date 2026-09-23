#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/release-build.sh
source "$ROOT/scripts/lib/release-build.sh"
# shellcheck source=scripts/lib/broker-bytes.sh
source "$ROOT/scripts/lib/broker-bytes.sh"
[[ ${1:-} == --confirm && $# == 1 ]] || pixel_die "Usage: ./pixel apply --confirm"
pixel_load_env
# Fail closed before any mutation unless either the root-custodied release operator or
# the explicitly selected reviewed direct-sudo fallback can perform broker reconciliation.
pixel_broker_bytes_require_trust
pixel_acquire_deployment_lock exclusive
bash "$ROOT/scripts/preflight.sh" --phase apply

candidate="$ROOT/dist/openclaw.json"
hash_file="$ROOT/dist/openclaw.sha256"
deployment_hash_file="$ROOT/dist/deployment.sha256"
release_identity_file="$ROOT/dist/release-identity.json"
source_runtime_file="$ROOT/dist/source-runtime.sha256"
[[ -f "$candidate" && -f "$hash_file" && -f "$deployment_hash_file" && -f "$release_identity_file" && -f "$source_runtime_file" ]] || pixel_die "No reviewed plan. Run ./pixel plan first."
(cd "$ROOT/dist" && sha256sum -c "$(basename "$hash_file")") >/dev/null || pixel_die "Reviewed candidate changed; run ./pixel plan again"
(cd "$ROOT" && sha256sum -c "$deployment_hash_file") >/dev/null || pixel_die "Deployment inputs changed after planning; run ./pixel plan again"
check=$(mktemp)
trap 'rm -f "$check"' EXIT
export PIXEL_PLUGIN_PATH="$PIXEL_INSTALL_DIR/current/plugin"
export PIXEL_OPS_PLUGIN_PATH="$PIXEL_INSTALL_DIR/current/plugin-ops"
export PIXEL_FRONTIER_PLUGIN_PATH="$PIXEL_INSTALL_DIR/current/plugin-frontier"
node "$ROOT/scripts/render-config.mjs" "$check" >/dev/null
cmp -s "$candidate" "$check" || pixel_die "Inputs changed after planning; run ./pixel plan again"

release_version=${PIXEL_RELEASE_VERSION:?}
release="$PIXEL_INSTALL_DIR/releases/$release_version"
stage="$PIXEL_INSTALL_DIR/releases/.${release_version}.stage.$$"
sandbox_uid=$(id -u)
candidate_sandbox_ref=$(pixel_sandbox_candidate_tag "$release_version" "$sandbox_uid")
candidate_sandbox_image_id=$(pixel_validate_sandbox_image "$candidate_sandbox_ref" "$release_version" "$sandbox_uid") \
  || pixel_die "Candidate sandbox image is missing or invalid; rerun candidate bootstrap --apply"
release_created=0
live_mutation_started=0
marker_temporary=""
backup="$OPENCLAW_HOME/backups/pixel-$(pixel_timestamp)-$$"
unit_dir="${PIXEL_GATEWAY_SYSTEMD_DIR:-/etc/systemd/system}"
unit_path="$unit_dir/$PIXEL_SYSTEMD_UNIT"
legacy_unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
legacy_unit_path="$legacy_unit_dir/$PIXEL_SYSTEMD_UNIT"
agent_env_dir="${XDG_CONFIG_HOME:-$HOME/.config}/pixel-agent"
courier_unit=${PIXEL_WEB_COURIER_UNIT:-pixel-web-courier.service}
courier_unit_path="${PIXEL_COURIER_SYSTEMD_DIR:-/etc/systemd/system}/$courier_unit"
previous_target=""
previous_version=""
previous_sandbox_image_id=""
if [[ -L "$PIXEL_INSTALL_DIR/current" ]]; then
  previous_target=$(readlink -f -- "$PIXEL_INSTALL_DIR/current") || pixel_die "Current release link cannot be resolved safely"
  releases_root=$(realpath -e -- "$PIXEL_INSTALL_DIR/releases") || pixel_die "Release root cannot be resolved safely"
  [[ ! -L "$previous_target" && -d "$previous_target" && "$(dirname "$previous_target")" == "$releases_root" ]] \
    || pixel_die "Current release target is outside the exact release root"
  previous_version=$(pixel_read_release_version "$previous_target") || pixel_die "Current release has no safe version identity"
  [[ "$(basename "$previous_target")" == "$previous_version" ]] || pixel_die "Current release directory does not match its version identity"
  # Pixel 4.3.8 wrote a three-file broker backup manifest that its own trusted
  # rollback controller requires. Pixel 4.3.9 deliberately restores the older
  # two-record contract, so accepting 4.3.8 as the live prestate would create a
  # rollback artifact that the still-trusted 4.3.8 controller cannot consume.
  # Refuse before release-tree, backup, service, symlink, or broker mutation.
  [[ "$previous_version" != 4.3.8 ]] || pixel_die "Pixel 4.3.8 has a rollback-incompatible broker manifest; restore a supported pre-4.3.8 release before applying Pixel $release_version"
  previous_live_sandbox_image_id=$(pixel_validate_sandbox_image "$PIXEL_SANDBOX_IMAGE" "$previous_version" "$sandbox_uid") \
    || pixel_die "Shared live sandbox image is missing or invalid for the current release"
  previous_sandbox_ref=$(pixel_sandbox_preserve_tag "$previous_version" "$sandbox_uid")
  if docker image inspect "$previous_sandbox_ref" >/dev/null 2>&1; then
    previous_sandbox_image_id=$(pixel_validate_sandbox_image "$previous_sandbox_ref" "$previous_version" "$sandbox_uid") \
      || pixel_die "Existing sandbox preserve tag is unsafe"
  else
    previous_sandbox_image_id=$(pixel_preserve_sandbox_image "$PIXEL_SANDBOX_IMAGE" "$previous_version" "$sandbox_uid") \
      || pixel_die "Current sandbox image could not be preserved for rollback"
  fi
  [[ "$previous_sandbox_image_id" == "$previous_live_sandbox_image_id" ]] \
    || pixel_die "Sandbox preserve tag does not match the shared live image"
elif docker image inspect "$PIXEL_SANDBOX_IMAGE" >/dev/null 2>&1; then
  # A first activation can be interrupted after the exact candidate was tagged
  # live but before `current` was published.  That state has no prior release
  # to preserve, yet it is safe to resume only when the shared tag still names
  # the already validated candidate image.  Keep every mismatched, malformed,
  # or foreign shared tag fail-closed.
  unbound_live_sandbox_image_id=$(pixel_validate_sandbox_image \
    "$PIXEL_SANDBOX_IMAGE" "$release_version" "$sandbox_uid") \
    || pixel_die "Shared live sandbox tag exists without an active Pixel release and is not valid for the reviewed candidate"
  [[ "$unbound_live_sandbox_image_id" == "$candidate_sandbox_image_id" ]] \
    || pixel_die "Shared live sandbox tag exists without an active Pixel release and does not match the reviewed candidate"
  pixel_warn "Recovering an exact candidate sandbox tag left by an interrupted first activation"
fi
had_config=0; [[ -f "$OPENCLAW_HOME/openclaw.json" ]] && had_config=1
had_gateway_env=0; [[ -f "$agent_env_dir/gateway.env" ]] && had_gateway_env=1
had_unit=0; [[ -f "$unit_path" ]] && had_unit=1
had_legacy_unit=0; [[ -f "$legacy_unit_path" ]] && had_legacy_unit=1
had_courier_env=0; [[ -f "$agent_env_dir/web-courier.env" ]] && had_courier_env=1
had_courier_unit=0; [[ -f "$courier_unit_path" ]] && had_courier_unit=1
managed_workspace_files=("AGENTS.md" "TOOLS.md" "WEB-NAVIGATION.md" "scripts/browse.sh" "scripts/research-ledger.py" "scripts/xfeed.sh")

restore_managed_workspace() {
  local relative destination saved mode
  for relative in "${managed_workspace_files[@]}"; do
    destination="$PIXEL_WORKSPACE/$relative"
    saved="$backup/workspace/$relative"
    mode=600; [[ "$relative" == scripts/* ]] && mode=700
    if [[ -f "$saved" ]]; then
      install -d -m 700 "$(dirname "$destination")"
      install -m "$mode" "$saved" "$destination"
    elif [[ -f "$saved.absent" ]]; then
      rm -f -- "$destination"
    fi
  done
}

rollback_apply() {
  status=$?
  [[ $status == 0 ]] && return
  rm -f -- "$check"
  if [[ -n "$marker_temporary" && "$marker_temporary" == "$OPENCLAW_HOME/backups/.last-apply."* ]]; then rm -f -- "$marker_temporary"; fi
  if [[ $live_mutation_started == 0 ]]; then
    if [[ "$stage" == "$PIXEL_INSTALL_DIR/releases/."*.stage.* ]]; then rm -rf -- "$stage"; fi
    if [[ $release_created == 1 && "$release" == "$PIXEL_INSTALL_DIR/releases/$release_version" && -d "$release" ]]; then rm -rf -- "$release"; fi
    pixel_warn "Apply failed before live deployment mutation; active Pixel state was left unchanged"
    exit "$status"
  fi
  pixel_warn "Apply failed; restoring the pre-apply configuration"
  pixel_systemctl stop "$PIXEL_SYSTEMD_UNIT" >/dev/null 2>&1 || true
  pixel_courier_systemctl stop "$courier_unit" >/dev/null 2>&1 || true
  pixel_retire_agent_sandboxes >/dev/null 2>&1 || true
  sandbox_restore_failed=0
  if [[ -n "$previous_target" ]]; then
    if ! pixel_restore_sandbox_image "$PIXEL_SANDBOX_IMAGE" "$previous_version" "$sandbox_uid" "$previous_sandbox_image_id"; then
      sandbox_restore_failed=1
    fi
  else
    if candidate_sandbox_image_now=$(pixel_validate_sandbox_image "$candidate_sandbox_ref" "$release_version" "$sandbox_uid"); then
      [[ "$candidate_sandbox_image_now" == "$candidate_sandbox_image_id" ]] || sandbox_restore_failed=1
    else
      sandbox_restore_failed=1
    fi
    if docker image inspect "$PIXEL_SANDBOX_IMAGE" >/dev/null 2>&1; then
      if shared_sandbox_image_now=$(pixel_validate_sandbox_image "$PIXEL_SANDBOX_IMAGE" "$release_version" "$sandbox_uid"); then
        [[ "$shared_sandbox_image_now" == "$candidate_sandbox_image_id" ]] || sandbox_restore_failed=1
      else
        sandbox_restore_failed=1
      fi
      if [[ $sandbox_restore_failed == 0 ]] && ! pixel_remove_sandbox_tag_exact "$PIXEL_SANDBOX_IMAGE" "$release_version" "$sandbox_uid" "$candidate_sandbox_image_id"; then
        sandbox_restore_failed=1
      fi
    fi
  fi
  attestation_invalidation_failed=0
  # The helper deliberately exits on an unsafe attestation object. Contain that fatal
  # boundary so compensation can still restore the exact release/config/broker prestate,
  # then withhold every service restart because receipt reconciliation was not honest.
  if ! ( pixel_invalidate_runtime_attestation "$PIXEL_INSTALL_DIR/runtime-attestation.json" ); then
    attestation_invalidation_failed=1
  fi
  [[ -f "$backup/openclaw.json" ]] && install -m 600 "$backup/openclaw.json" "$OPENCLAW_HOME/openclaw.json"
  [[ $had_config == 0 ]] && rm -f -- "$OPENCLAW_HOME/openclaw.json"
  if [[ -n "$previous_target" ]]; then
    pixel_atomic_symlink "$previous_target" "$PIXEL_INSTALL_DIR/current"
  else
    rm -f -- "$PIXEL_INSTALL_DIR/current"
  fi
  [[ -f "$backup/gateway.env" ]] && install -m 600 "$backup/gateway.env" "$agent_env_dir/gateway.env"
  [[ $had_gateway_env == 0 ]] && rm -f -- "$agent_env_dir/gateway.env"
  if [[ -f "$backup/openclaw-gateway.system.service" ]]; then pixel_gateway_install_unit "$backup/openclaw-gateway.system.service" "$unit_path"; else pixel_gateway_remove_unit "$unit_path"; fi
  if [[ -f "$backup/openclaw-gateway.user.service" ]]; then install -m 600 "$backup/openclaw-gateway.user.service" "$legacy_unit_path"; elif [[ $had_legacy_unit == 0 ]]; then rm -f -- "$legacy_unit_path"; fi
  [[ -f "$backup/web-courier.env" ]] && install -m 600 "$backup/web-courier.env" "$agent_env_dir/web-courier.env"
  [[ $had_courier_env == 0 ]] && rm -f -- "$agent_env_dir/web-courier.env"
  [[ -f "$backup/pixel-web-courier.service" ]] && pixel_courier_install_unit "$backup/pixel-web-courier.service" "$courier_unit_path"
  [[ $had_courier_unit == 0 ]] && pixel_courier_remove_unit "$courier_unit_path"
  restore_managed_workspace
  broker_restore_failed=0
  if ! pixel_broker_bytes_restore_all "$backup"; then broker_restore_failed=1; fi
  pixel_refresh_custom_plugin_registry >/dev/null 2>&1 || true
  pixel_systemctl daemon-reload >/dev/null 2>&1 || true
  pixel_legacy_systemctl daemon-reload >/dev/null 2>&1 || true
  pixel_courier_systemctl daemon-reload >/dev/null 2>&1 || true
  if [[ "$stage" == "$PIXEL_INSTALL_DIR/releases/."*.stage.* ]]; then rm -rf -- "$stage"; fi
  if [[ $release_created == 1 && "$release" == "$PIXEL_INSTALL_DIR/releases/$release_version" && -d "$release" ]]; then rm -rf -- "$release"; fi
  if [[ $sandbox_restore_failed == 1 || $broker_restore_failed == 1 || $attestation_invalidation_failed == 1 ]]; then
    pixel_warn "Apply compensation could not reconcile every sandbox, broker, and attestation runtime fact exactly; Pixel gateway and courier remain stopped"
    exit "$status"
  fi
  if [[ $had_unit == 1 ]]; then pixel_systemctl enable "$PIXEL_SYSTEMD_UNIT" >/dev/null 2>&1 || true; pixel_systemctl restart "$PIXEL_SYSTEMD_UNIT" >/dev/null 2>&1 || true; fi
  if [[ $had_legacy_unit == 1 && $had_unit == 0 ]]; then pixel_legacy_systemctl enable "$PIXEL_SYSTEMD_UNIT" >/dev/null 2>&1 || true; pixel_legacy_systemctl restart "$PIXEL_SYSTEMD_UNIT" >/dev/null 2>&1 || true; fi
  if [[ $had_courier_unit == 1 ]]; then
    pixel_courier_systemctl enable "$courier_unit" >/dev/null 2>&1 || true
    pixel_courier_systemctl restart "$courier_unit" >/dev/null 2>&1 || true
  else
    pixel_courier_systemctl disable --now "$courier_unit" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap rollback_apply EXIT

install -d -m 700 "$PIXEL_INSTALL_DIR/releases" "$OPENCLAW_HOME/backups" "$backup" "$agent_env_dir" "$PIXEL_WORKSPACE" "$OPENCLAW_HOME/logs" "$PIXEL_EMBEDDING_CACHE"
[[ -f "$OPENCLAW_HOME/openclaw.json" ]] && cp -p "$OPENCLAW_HOME/openclaw.json" "$backup/openclaw.json"
[[ -f "$agent_env_dir/gateway.env" ]] && cp -p "$agent_env_dir/gateway.env" "$backup/gateway.env"
[[ -f "$unit_path" ]] && cp -p "$unit_path" "$backup/openclaw-gateway.system.service"
[[ -f "$legacy_unit_path" ]] && cp -p "$legacy_unit_path" "$backup/openclaw-gateway.user.service"
[[ -f "$agent_env_dir/web-courier.env" ]] && cp -p "$agent_env_dir/web-courier.env" "$backup/web-courier.env"
[[ -f "$courier_unit_path" ]] && cp "$courier_unit_path" "$backup/pixel-web-courier.service"
for relative in "${managed_workspace_files[@]}"; do
  saved="$backup/workspace/$relative"
  install -d -m 700 "$(dirname "$saved")"
  if [[ -f "$PIXEL_WORKSPACE/$relative" ]]; then cp -p "$PIXEL_WORKSPACE/$relative" "$saved"; else touch "$saved.absent"; fi
done
printf '%s\n' "$previous_target" > "$backup/previous-release"
if [[ -n "$previous_target" ]]; then
  printf '%s\n' "$previous_version" > "$backup/previous-sandbox-version"
  printf '%s\n' "$previous_sandbox_image_id" > "$backup/previous-sandbox-image-id"
fi

# Build the exact release tree through the single shared release-build primitive so
# migration prepare and ordinary apply can never drift.
pixel_build_release_stage "$ROOT" "$stage"
stage_tree_sha=$(pixel_release_tree_sha "$stage")
if [[ -e "$release" || -L "$release" ]]; then
  [[ "$previous_target" != "$release" ]] || {
    rm -rf -- "$stage"
    pixel_die "Pixel $release_version is already the active release"
  }
  # A verified rollback deliberately retains the installed candidate as audit and
  # recovery evidence. Re-adopt it only when it is exactly the release rebuilt from
  # this reviewed plan: regular directory, valid per-file manifest, identical manifest,
  # matching version identity, and identical canonical type/path/mode/byte/symlink tree.
  # Never follow, modify, or replace a non-exact pre-existing target.
  if [[ -d "$release" && ! -L "$release" ]] \
     && (cd "$release" && sha256sum -c install-manifest.sha256) >/dev/null 2>&1 \
     && cmp -s "$release/install-manifest.sha256" "$stage/install-manifest.sha256" \
     && [[ "$(pixel_read_release_version "$release")" == "$release_version" ]] \
     && [[ "$(pixel_release_tree_sha "$release")" == "$stage_tree_sha" ]]; then
    pixel_log "Adopting exact pre-existing Pixel $release_version release"
    rm -rf -- "$stage"
  else
    rm -rf -- "$stage"
    pixel_die "Release already exists but is not byte-exact to the reviewed plan: $release"
  fi
else
  mv "$stage" "$release"
  release_created=1
fi
[[ -d "$release" && ! -L "$release" && "$(pixel_release_tree_sha "$release")" == "$stage_tree_sha" ]] \
  || pixel_die "Installed release changed before activation"
# Capture the exact privileged broker bytes before any live deployment mutation. The
# broker transaction itself is armed only immediately before the first broker mutation.
pixel_broker_bytes_backup_all "$backup"
if [[ -n ${PIXEL_RELEASE_UPDATE_LIVE_MUTATION_MARKER:-} ]]; then
  PYTHONDONTWRITEBYTECODE=1 python3 -B - "$PIXEL_RELEASE_UPDATE_LIVE_MUTATION_MARKER" <<'PY'
import os
import stat
import sys

path = os.path.abspath(sys.argv[1])
parent, name = os.path.split(path)
if not os.path.isabs(sys.argv[1]) or name != "LIVE-MUTATION-STARTED" or os.path.realpath(parent) != parent:
    raise SystemExit("invalid release-update live-mutation marker path")
directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    info = os.fstat(directory)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit("unsafe release-update live-mutation marker directory")
    marker = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
        dir_fd=directory,
    )
    try:
        payload = b"pixel-release-live-mutation-started-v1\n"
        written = 0
        while written < len(payload):
            count = os.write(marker, payload[written:])
            if count <= 0:
                raise OSError("release-update live-mutation marker write made no progress")
            written += count
        os.fsync(marker)
    finally:
        os.close(marker)
    os.fsync(directory)
finally:
    os.close(directory)
PY
fi
live_mutation_started=1
pixel_systemctl stop "$PIXEL_SYSTEMD_UNIT" >/dev/null 2>&1 || true
pixel_courier_systemctl stop "$courier_unit" >/dev/null 2>&1 || true
pixel_retire_agent_sandboxes
docker image tag "$candidate_sandbox_image_id" "$PIXEL_SANDBOX_IMAGE"
shared_sandbox_image_id=$(pixel_validate_sandbox_image "$PIXEL_SANDBOX_IMAGE" "$release_version" "$sandbox_uid") \
  || pixel_die "Shared sandbox image failed validation after activation"
[[ "$shared_sandbox_image_id" == "$candidate_sandbox_image_id" ]] \
  || pixel_die "Shared sandbox image changed during activation"
pixel_atomic_symlink "$release" "$PIXEL_INSTALL_DIR/current"

cp -a -n -- "$ROOT/.generated/workspace/." "$PIXEL_WORKSPACE/"
node "$ROOT/scripts/migrate-workspace-source-boundary.mjs" "$PIXEL_WORKSPACE" >/dev/null
rm -f -- "$PIXEL_WORKSPACE/scripts/xfeed.sh"
# Existing workspace data belongs to the owner and may include read-only sandbox mounts.
# Only managed/new paths receive deployment permissions; never recursively chmod memory.
chmod 700 "$PIXEL_WORKSPACE"
install -d -m 700 "$PIXEL_WORKSPACE/scripts"
install -m 600 "$ROOT/.generated/workspace/WEB-NAVIGATION.md" "$PIXEL_WORKSPACE/WEB-NAVIGATION.md"
install -m 700 "$ROOT/.generated/workspace/scripts/browse.sh" "$ROOT/.generated/workspace/scripts/research-ledger.py" "$PIXEL_WORKSPACE/scripts/"
if [[ ${PIXEL_LIMB_WEB_ENABLED:-1} == 0 ]]; then rm -f -- "$PIXEL_WORKSPACE/scripts/browse.sh" "$PIXEL_WORKSPACE/WEB-NAVIGATION.md"; fi
install -d -m 700 "$PIXEL_WORKSPACE/media/webq" "$PIXEL_WORKSPACE/media/inbound"
install -m 600 "$candidate" "$OPENCLAW_HOME/openclaw.json"
install -m 600 "$ROOT/.generated/gateway.env" "$agent_env_dir/gateway.env"
pixel_gateway_install_unit "$ROOT/.generated/openclaw-gateway.service" "$unit_path"
if [[ ${PIXEL_WEB_COURIER_ENABLED:-1} == 1 ]]; then
  install -m 600 "$ROOT/.generated/web-courier.env" "$agent_env_dir/web-courier.env"
  pixel_courier_install_unit "$ROOT/.generated/pixel-web-courier.service" "$courier_unit_path"
else
  pixel_courier_systemctl disable --now "$courier_unit" >/dev/null 2>&1 || true
  rm -f -- "$agent_env_dir/web-courier.env"
  pixel_courier_remove_unit "$courier_unit_path"
fi
pixel_courier_systemctl daemon-reload
if [[ -f "$legacy_unit_path" ]]; then
  pixel_legacy_systemctl disable --now "$PIXEL_SYSTEMD_UNIT" >/dev/null 2>&1 || true
  rm -f -- "$legacy_unit_path"
  pixel_legacy_systemctl daemon-reload >/dev/null 2>&1 || true
fi
pixel_systemctl daemon-reload
pixel_refresh_custom_plugin_registry
# Update and restart privileged broker runtimes before the gateway can send work to them.
pixel_broker_bytes_install_all "$PIXEL_INSTALL_DIR/current" "$backup"
pixel_systemctl enable "$PIXEL_SYSTEMD_UNIT"
pixel_systemctl restart "$PIXEL_SYSTEMD_UNIT"
if [[ ${PIXEL_WEB_COURIER_ENABLED:-1} == 1 ]]; then
  pixel_courier_systemctl enable "$courier_unit"
  pixel_courier_systemctl restart "$courier_unit"
fi
bash "$ROOT/scripts/verify.sh"
marker="$OPENCLAW_HOME/backups/last-apply"
marker_temporary=$(mktemp "$OPENCLAW_HOME/backups/.last-apply.XXXXXXXX")
printf '%s\n' "$backup" > "$marker_temporary"
chmod 600 "$marker_temporary"
mv -f -- "$marker_temporary" "$marker"
marker_temporary=""
trap - EXIT
rm -f "$check"
pixel_log "Applied Pixel $release_version from the reviewed plan. Rollback state: $backup"
