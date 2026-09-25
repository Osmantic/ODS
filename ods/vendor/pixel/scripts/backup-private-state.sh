#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
pixel_load_env
pixel_acquire_deployment_lock shared
target=${1:?Usage: backup-private-state.sh /absolute/backup-directory [age-recipient]}
recipient=${2:-${PIXEL_BACKUP_AGE_RECIPIENT:-}}
[[ -n "$recipient" && "$recipient" != *$'\n'* && "$recipient" != *$'\r'* ]] || pixel_die "An age recipient is required as argument 2 or PIXEL_BACKUP_AGE_RECIPIENT"
[[ $# -le 2 ]] || pixel_die "Usage: backup-private-state.sh /absolute/backup-directory [age-recipient]"
for command in age python3 sha256sum ssh-keygen tar; do pixel_require_command "$command"; done
case ${PIXEL_DEEP_WORK_BACKUP_ENABLED:-0} in 0|1) ;; *) pixel_die "PIXEL_DEEP_WORK_BACKUP_ENABLED must be 0 or 1" ;; esac
[[ "$target" = /* && "$target" != / ]] || { echo "Backup target must be an absolute, non-root directory." >&2; exit 1; }
resolved=$(realpath -m "$target")
case "$resolved/" in
  /|"$OPENCLAW_HOME/"*|"$PIXEL_WORKSPACE/"*) pixel_die "Backup target must be outside OpenClaw and workspace state" ;;
esac
install -d -m 700 "$resolved"
umask 077
signing_key=${PIXEL_BACKUP_SIGNING_KEY:-${XDG_CONFIG_HOME:-$HOME/.config}/pixel-deployment/backup-signing-key}
allowed_signers=${PIXEL_BACKUP_ALLOWED_SIGNERS:-${XDG_CONFIG_HOME:-$HOME/.config}/pixel-deployment/backup-allowed-signers}
[[ "$signing_key" == /* && "$allowed_signers" == /* ]] || pixel_die "Backup signing paths must be absolute"
install -d -m 700 "$(dirname "$signing_key")" "$(dirname "$allowed_signers")"
if [[ ! -e "$signing_key" && ! -L "$signing_key" ]]; then
  ssh-keygen -q -t ed25519 -N '' -C pixel-private-backup -f "$signing_key" >/dev/null
fi
[[ -f "$signing_key" && ! -L "$signing_key" ]] || pixel_die "Backup signing key must be a regular non-symlink file"
key_mode=$(stat -c %a "$signing_key")
(( (8#$key_mode & 8#077) == 0 )) || pixel_die "Backup signing key must not be group/world accessible"
public_key=$(ssh-keygen -y -f "$signing_key")
[[ "$public_key" == ssh-ed25519\ * || "$public_key" == sk-ssh-ed25519@openssh.com\ * ]] || pixel_die "Backup signing key must be Ed25519"
signers_tmp="$allowed_signers.tmp.$$"
printf 'pixel-backup %s\n' "$public_key" > "$signers_tmp"
chmod 600 "$signers_tmp"
mv -f -- "$signers_tmp" "$allowed_signers"
[[ -f "$allowed_signers" && ! -L "$allowed_signers" ]] || pixel_die "Backup allowed-signers file is unsafe"
archive="$resolved/pixel-private-$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz.age"
checksum="$archive.sha256"
signature="$archive.sig"
[[ ! -e "$archive" && ! -L "$archive" && ! -e "$checksum" && ! -L "$checksum" && ! -e "$signature" && ! -L "$signature" ]] || pixel_die "Refusing to overwrite an existing backup"
items=()
active_work_units=()
work_units_quiesced=0
restart_work_units() {
  local unit kind
  [[ $work_units_quiesced == 1 ]] || return 0
  for kind in path timer service; do
    for unit in "${active_work_units[@]}"; do
      [[ $unit == *.$kind ]] || continue
      pixel_systemctl start "$unit" >/dev/null
    done
  done
  work_units_quiesced=0
}
quiesce_work_units() {
  local output line unit remaining
  output=$(pixel_systemctl_read list-units 'pixel-work-*' --all --state=active --plain --no-legend --no-pager) || pixel_die "Could not enumerate active Deep Work services for a consistent backup"
  while IFS= read -r line; do
    [[ -n $line ]] || continue
    unit=${line%%[[:space:]]*}
    [[ $unit =~ ^pixel-work-[a-z0-9][a-z0-9.-]*\.(service|timer|path)$ ]] || pixel_die "Systemd returned an unsafe Deep Work unit name"
    active_work_units+=("$unit")
  done <<<"$output"
  [[ ${#active_work_units[@]} -eq 0 ]] || work_units_quiesced=1
  pixel_systemctl stop 'pixel-work-*.path' 'pixel-work-*.timer' 'pixel-work-*.service' >/dev/null
  remaining=$(pixel_systemctl_read list-units 'pixel-work-*' --all --state=active --plain --no-legend --no-pager) || pixel_die "Could not verify Deep Work backup quiescence"
  [[ -z $remaining ]] || pixel_die "A Deep Work unit remained active during backup quiescence"
}
deep_path() {
  local value=$1 label=$2 normalized
  [[ "$value" == /* && "$value" != / && "$value" != *$'\n'* && "$value" != *$'\r'* ]] || pixel_die "$label must be an absolute non-root path"
  normalized=$(realpath -m "$value")
  [[ "$normalized" == "$value" ]] || pixel_die "$label must be normalized"
  printf '%s' "$normalized"
}
path_contains() { [[ $2 == "$1" || $2 == "$1/"* ]]; }
trap 'restart_work_units >/dev/null 2>&1 || true' EXIT
add_item() {
  local path=$1
  [[ -e "$path" || -L "$path" ]] || return 0
  [[ "$path" == /* && "$path" != / && "$path" != *$'\n'* ]] || pixel_die "Unsafe private-state backup path"
  items+=("${path#/}")
}
add_item "$OPENCLAW_HOME/openclaw.json"
add_item "$PIXEL_WORKSPACE"
add_item "$PIXEL_GOOGLE_TOKEN_PATH"
add_item "${XDG_CONFIG_HOME:-$HOME/.config}/pixel-agent/gateway.env"
add_item "${XDG_CONFIG_HOME:-$HOME/.config}/pixel-deployment"
add_item "${PIXEL_CONTROL_POLICY_PATH:-${XDG_CONFIG_HOME:-$HOME/.config}/pixel-control/policy.json}"
[[ -z ${PIXEL_PRIVATE_ONBOARDING_PATH:-} ]] || add_item "$PIXEL_PRIVATE_ONBOARDING_PATH"
add_item "$PIXEL_ROOT/.env"
add_item "${PIXEL_SOURCE_BROKER_STATE_DIR:-/var/lib/pixel-source-broker}"
add_item "${PIXEL_SOURCE_BROKER_ENV:-/etc/pixel-source-broker.env}"
add_item "${PIXEL_OPS_BROKER_STATE_DIR:-/var/lib/pixel-ops-broker}"
add_item "${PIXEL_OPS_BROKER_ENV:-/etc/pixel-ops-broker.env}"
add_item "$(dirname "${PIXEL_OPS_POLICY_PATH:-/etc/pixel-ops-broker/policy.json}")"
add_item "${PIXEL_FRONTIER_BROKER_STATE_DIR:-/var/lib/pixel-frontier-broker}"
add_item "${PIXEL_FRONTIER_BROKER_ENV:-/etc/pixel-frontier-broker.env}"
add_item "$(dirname "${PIXEL_FRONTIER_POLICY_PATH:-/etc/pixel-frontier-broker/policy.json}")"
if [[ ${PIXEL_DEEP_WORK_BACKUP_ENABLED:-0} == 1 ]]; then
  work_state_root=$(deep_path "${PIXEL_WORK_STATE_ROOT:-/var/lib/pixel-work}" "Deep Work state root")
  work_config_root=$(deep_path "${PIXEL_WORK_CONFIG_ROOT:-/etc/pixel-work}" "Deep Work configuration root")
  knowledge_vault_root=$(deep_path "${PIXEL_KNOWLEDGE_VAULT_ROOT:-/var/lib/pixel-knowledge/vault}" "knowledge vault root")
  knowledge_credential=$(deep_path "${PIXEL_KNOWLEDGE_VAULT_CREDENTIAL:-/etc/pixel-work-credentials/pixel-knowledge-vault-key}" "knowledge vault credential")
  for capture_root in "$work_state_root" "$work_config_root" "$knowledge_vault_root"; do
    path_contains "$capture_root" "$knowledge_credential" && pixel_die "The external knowledge-vault credential must be outside every captured backup root"
  done
  add_item "$work_state_root"
  add_item "$work_config_root"
  add_item "$knowledge_vault_root"
  quiesce_work_units
fi
[[ ${#items[@]} -gt 0 ]] || { echo "No private state found." >&2; exit 1; }
# Remove duplicate and nested roots so every archive member has one unambiguous owner.
pruned=()
declare -A seen=()
for candidate in "${items[@]}"; do
  nested=0
  for other in "${items[@]}"; do
    if [[ "$candidate" != "$other" && "$candidate" == "$other/"* ]]; then nested=1; break; fi
  done
  if [[ $nested == 0 && -z ${seen[$candidate]+x} ]]; then pruned+=("$candidate"); seen[$candidate]=1; fi
done
items=("${pruned[@]}")
for item in "${items[@]}"; do
  case "$resolved/" in
    "/$item/"|"/$item/"*) pixel_die "Backup target must be outside every captured private-state root" ;;
  esac
done
manifest_dir=$(mktemp -d)
manifest="$manifest_dir/.pixel-backup-manifest.json"
backup_version=$(tr -d '[:space:]' < "$ROOT/VERSION")
if [[ -L "$PIXEL_INSTALL_DIR/current" && -f "$PIXEL_INSTALL_DIR/current/VERSION" && ! -L "$PIXEL_INSTALL_DIR/current/VERSION" ]]; then
  backup_version=$(tr -d '[:space:]' < "$PIXEL_INSTALL_DIR/current/VERSION")
fi
[[ "$backup_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || pixel_die "Active Pixel version is malformed"
python3 - "$manifest" "$backup_version" "${items[@]}" <<'PY'
import datetime, json, pathlib, sys
output, pixel_version, *paths = sys.argv[1:]
value = {
    "schemaVersion": 1,
    "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "pixelVersion": pixel_version,
    "paths": sorted(paths),
}
pathlib.Path(output).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
PY
chmod 600 "$manifest"
cleanup_partial() { rm -f -- "$archive" "$checksum" "$signature"; rm -rf -- "$manifest_dir"; restart_work_units >/dev/null 2>&1 || true; }
trap cleanup_partial EXIT
sudo tar -czf - -C / "${items[@]}" -C "$manifest_dir" .pixel-backup-manifest.json | age --encrypt --recipient "$recipient" --output "$archive"
[[ -s "$archive" ]] || pixel_die "Encrypted backup is empty"
chmod 600 "$archive"
ssh-keygen -q -Y sign -f "$signing_key" -n pixel-private-backup "$archive" >/dev/null
[[ -s "$signature" && ! -L "$signature" ]] || pixel_die "Backup signature was not created safely"
chmod 600 "$signature"
observed=$(sha256sum "$archive" | awk '{print $1}')
printf '%s  %s\n' "$observed" "$(basename "$archive")" > "$checksum"; chmod 600 "$checksum"
restart_work_units
rm -rf -- "$manifest_dir"
trap - EXIT
echo "$archive"
