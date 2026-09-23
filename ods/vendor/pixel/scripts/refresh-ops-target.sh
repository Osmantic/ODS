#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ $# == 4 && $4 == --confirm ]] || pixel_die "Usage: ./pixel ops-target-refresh TARGET_ID OPERATOR_SSH_ALIAS EXPECTED_HOSTNAME --confirm"
target=$1
operator_alias=$2
expected=$3
[[ "$target" =~ ^[a-z][a-z0-9_-]{1,63}$ ]] || pixel_die "Unsafe target ID"
[[ "$operator_alias" =~ ^[A-Za-z0-9_.@:-]{1,255}$ ]] || pixel_die "Unsafe operator SSH alias"
[[ "$expected" =~ ^[A-Za-z0-9._-]{1,255}$ ]] || pixel_die "Unsafe expected hostname"
pixel_load_env
[[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 ]] || pixel_die "Operations limb is disabled"
for command in sudo ssh scp grep base64 awk mktemp install; do pixel_require_command "$command"; done
key_dir="$PIXEL_OPS_BROKER_STATE_DIR/.ssh"
sudo test -f "$key_dir/config" || pixel_die "Operations SSH configuration is absent; enroll the target first"
sudo test -f "$key_dir/id_ed25519.pub" || pixel_die "Operations SSH public key is absent"
sudo grep -Eq "^Host[[:space:]]+$target$" "$key_dir/config" || pixel_die "Operations SSH target is not enrolled: $target"
observed=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$operator_alias" hostname | tr -d '\r')
[[ "$observed" == "$expected" ]] || pixel_die "Target identity mismatch: expected $expected, observed $observed"

public_key=$(sudo cat "$key_dir/id_ed25519.pub")
encoded=$(printf '%s' "$public_key" | base64 -w0)
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$operator_alias" "sudo bash -s -- pixel-ops-transport pixel-runner '$encoded'" < "$ROOT/deploy/ops-runner/provision-target.sh"

for asset in run-test receive-artifact.py action.py managed.py dispatch.py; do
  remote_stage=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$operator_alias" "mktemp /tmp/pixel-ops-$asset.XXXXXX")
  [[ "$remote_stage" == /tmp/pixel-ops-$asset.* ]] || pixel_die "Remote runner staging path is unsafe"
  scp -q "$ROOT/deploy/ops-runner/$asset" "$operator_alias:$remote_stage"
  destination=/usr/local/libexec/pixel-ops-$asset
  [[ "$asset" == receive-artifact.py ]] && destination=/usr/local/libexec/pixel-ops-receive-artifact
  [[ "$asset" == action.py ]] && destination=/usr/local/libexec/pixel-ops-action
  [[ "$asset" == managed.py ]] && destination=/usr/local/libexec/pixel-ops-managed
  [[ "$asset" == dispatch.py ]] && destination=/usr/local/libexec/pixel-ops-dispatch
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$operator_alias" "sudo install -o root -g root -m 0755 '$remote_stage' '$destination' && rm -f -- '$remote_stage'"
done
config_tmp=$(mktemp)
trap 'rm -f -- "$config_tmp"' EXIT
sudo awk -v selected="$target" '
  /^Host[[:space:]]+/ { active = ($2 == selected) }
  active && /^[[:space:]]*User[[:space:]]+/ { print "  User pixel-ops-transport"; next }
  { print }
' "$key_dir/config" | tee "$config_tmp" >/dev/null
sudo install -o "$PIXEL_OPS_BROKER_USER" -g "$PIXEL_OPS_BROKER_GROUP" -m 0600 "$config_tmp" "$key_dir/config"
broker_observed=$(sudo -u "$PIXEL_OPS_BROKER_USER" ssh "$target" hostname | tr -d '\r')
[[ "$broker_observed" == "$expected" ]] || pixel_die "Dedicated runner identity verification failed after helper refresh"
pixel_log "Refreshed root-owned Operations helpers on enrolled target $target ($expected); SSH keys and trust pins were unchanged"
