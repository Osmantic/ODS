#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/broker-bytes.sh
source "$ROOT/scripts/lib/broker-bytes.sh"
[[ ${1:-} == --confirm && $# == 1 ]] || pixel_die "Usage: ./pixel rollback --confirm"
pixel_load_env
pixel_broker_bytes_require_trust
pixel_acquire_deployment_lock exclusive
marker="$OPENCLAW_HOME/backups/last-apply"
[[ -f "$marker" ]] || pixel_die "No automatic rollback point is recorded"
backup=$(cat "$marker")
[[ "$backup" == "$OPENCLAW_HOME/backups/"* && -d "$backup" ]] || pixel_die "Unsafe or missing rollback directory: $backup"
previous_record=$(cat "$backup/previous-release")
[[ "$previous_record" == /* ]] || pixel_die "Rollback point records a non-absolute prior release"
previous=$(realpath -e -- "$previous_record") || pixel_die "Rollback point has no resolvable prior release"
releases_root=$(realpath -e -- "$PIXEL_INSTALL_DIR/releases") || pixel_die "Release root cannot be resolved safely"
[[ "$previous" == "$previous_record" && ! -L "$previous_record" && -d "$previous" && "$(dirname "$previous")" == "$releases_root" ]] \
  || pixel_die "Rollback point is outside the exact release root"
previous_version=$(pixel_read_release_version "$previous") || pixel_die "Rollback release has no safe version identity"
[[ "$(basename "$previous")" == "$previous_version" ]] || pixel_die "Rollback release directory does not match its version identity"
[[ -f "$backup/previous-sandbox-version" && ! -L "$backup/previous-sandbox-version" && -f "$backup/previous-sandbox-image-id" && ! -L "$backup/previous-sandbox-image-id" ]] \
  || pixel_die "Rollback point has no exact prior sandbox image record"
recorded_sandbox_version=$(cat "$backup/previous-sandbox-version")
recorded_sandbox_image_id=$(cat "$backup/previous-sandbox-image-id")
[[ "$recorded_sandbox_version" == "$previous_version" ]] || pixel_die "Rollback sandbox version does not match the prior release"
preserved_sandbox_ref=$(pixel_sandbox_preserve_tag "$previous_version" "$(id -u)")
preserved_sandbox_image_id=$(pixel_validate_sandbox_image "$preserved_sandbox_ref" "$previous_version" "$(id -u)") \
  || pixel_die "Preserved rollback sandbox image is missing or unsafe"
[[ "$recorded_sandbox_image_id" == "$preserved_sandbox_image_id" ]] || pixel_die "Rollback sandbox image does not match the apply record"
unit_dir="${PIXEL_GATEWAY_SYSTEMD_DIR:-/etc/systemd/system}"
unit_path="$unit_dir/$PIXEL_SYSTEMD_UNIT"
legacy_unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
legacy_unit_path="$legacy_unit_dir/$PIXEL_SYSTEMD_UNIT"
agent_env_dir="${XDG_CONFIG_HOME:-$HOME/.config}/pixel-agent"
courier_unit=${PIXEL_WEB_COURIER_UNIT:-pixel-web-courier.service}
courier_unit_path="${PIXEL_COURIER_SYSTEMD_DIR:-/etc/systemd/system}/$courier_unit"
pixel_systemctl disable --now "$PIXEL_SYSTEMD_UNIT" >/dev/null 2>&1 || true
pixel_courier_systemctl disable --now "$courier_unit" >/dev/null 2>&1 || true
pixel_retire_agent_sandboxes
pixel_restore_sandbox_image "$PIXEL_SANDBOX_IMAGE" "$previous_version" "$(id -u)" "$recorded_sandbox_image_id" \
  || pixel_die "Preserved rollback sandbox image could not be restored exactly; Pixel services remain stopped"
if [[ -f "$backup/openclaw.json" ]]; then install -m 600 "$backup/openclaw.json" "$OPENCLAW_HOME/openclaw.json"; else rm -f -- "$OPENCLAW_HOME/openclaw.json"; fi
if [[ -f "$backup/gateway.env" ]]; then install -m 600 "$backup/gateway.env" "$agent_env_dir/gateway.env"; else rm -f -- "$agent_env_dir/gateway.env"; fi
if [[ -f "$backup/openclaw-gateway.system.service" ]]; then pixel_gateway_install_unit "$backup/openclaw-gateway.system.service" "$unit_path"; else pixel_gateway_remove_unit "$unit_path"; fi
legacy_backup=""
[[ -f "$backup/openclaw-gateway.user.service" ]] && legacy_backup="$backup/openclaw-gateway.user.service"
[[ -z "$legacy_backup" && -f "$backup/openclaw-gateway.service" ]] && legacy_backup="$backup/openclaw-gateway.service"
if [[ -n "$legacy_backup" ]]; then install -d -m 700 "$legacy_unit_dir"; install -m 600 "$legacy_backup" "$legacy_unit_path"; else rm -f -- "$legacy_unit_path"; fi
if [[ -f "$backup/web-courier.env" ]]; then install -m 600 "$backup/web-courier.env" "$agent_env_dir/web-courier.env"; else rm -f -- "$agent_env_dir/web-courier.env"; fi
if [[ -f "$backup/pixel-web-courier.service" ]]; then pixel_courier_install_unit "$backup/pixel-web-courier.service" "$courier_unit_path"; else pixel_courier_remove_unit "$courier_unit_path"; fi
for relative in AGENTS.md TOOLS.md WEB-NAVIGATION.md scripts/browse.sh scripts/research-ledger.py scripts/xfeed.sh; do
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
pixel_atomic_symlink "$previous" "$PIXEL_INSTALL_DIR/current"
# Invalidate any prior runtime attestation: the active release has changed, so a stale
# positive receipt must never survive. Fail closed: a failure here aborts the rollback
# (nonzero) before the marker is consumed, leaving honest, retryable state with the active
# release already restored and no false receipt on disk.
pixel_invalidate_runtime_attestation "$PIXEL_INSTALL_DIR/runtime-attestation.json"
# Restore the exact prior installed broker bytes captured by apply and restart each
# restored broker service. Disabled/uninstalled limbs are never touched.
pixel_broker_bytes_restore_all "$backup"
pixel_refresh_custom_plugin_registry
pixel_systemctl daemon-reload
pixel_legacy_systemctl daemon-reload >/dev/null 2>&1 || true
if [[ -f "$unit_path" ]]; then
  pixel_systemctl enable "$PIXEL_SYSTEMD_UNIT"
  pixel_systemctl restart "$PIXEL_SYSTEMD_UNIT"
elif [[ -f "$legacy_unit_path" ]]; then
  pixel_legacy_systemctl enable "$PIXEL_SYSTEMD_UNIT"
  pixel_legacy_systemctl restart "$PIXEL_SYSTEMD_UNIT"
fi
pixel_courier_systemctl daemon-reload
if [[ -f "$courier_unit_path" ]]; then
  pixel_courier_systemctl enable "$courier_unit"
  pixel_courier_systemctl restart "$courier_unit"
else
  pixel_courier_systemctl disable --now "$courier_unit" >/dev/null 2>&1 || true
fi
mv "$marker" "$marker.used.$(pixel_timestamp)"
pixel_log "Rolled back to $previous"
