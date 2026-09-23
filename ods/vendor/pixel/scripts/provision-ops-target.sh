#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ $# == 4 && $4 == --confirm ]] || pixel_die "Usage: ./pixel ops-target TARGET_ID OPERATOR_SSH_ALIAS EXPECTED_HOSTNAME --confirm"
target=$1
operator_alias=$2
expected=$3
[[ "$target" =~ ^[a-z][a-z0-9_-]{1,63}$ ]] || pixel_die "Unsafe target ID"
[[ "$operator_alias" =~ ^[A-Za-z0-9_.@:-]{1,255}$ ]] || pixel_die "Unsafe operator SSH alias"
[[ "$expected" =~ ^[A-Za-z0-9._-]{1,255}$ ]] || pixel_die "Unsafe expected hostname"
pixel_load_env
[[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 ]] || pixel_die "Operations limb is disabled"
for command in sudo ssh scp ssh-keygen base64 mktemp install awk grep sort; do pixel_require_command "$command"; done
key_dir="$PIXEL_OPS_BROKER_STATE_DIR/.ssh"
key_path="$key_dir/id_ed25519"
if ! sudo test -f "$key_path" || ! sudo test -f "$key_path.pub"; then
  pixel_die "Generate the broker key first with ./pixel ops-keygen --confirm"
fi
observed=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$operator_alias" hostname | tr -d '\r')
[[ "$observed" == "$expected" ]] || pixel_die "Target identity mismatch: expected $expected, observed $observed"
host=$(ssh -G "$operator_alias" 2>/dev/null | awk '/^hostname / {print $2; exit}')
port=$(ssh -G "$operator_alias" 2>/dev/null | awk '/^port / {print $2; exit}')
known_hosts=$(ssh -G "$operator_alias" 2>/dev/null | awk '/^userknownhostsfile / {print $2; exit}')
[[ -n "$host" && -n "$port" && -f "$known_hosts" ]] || pixel_die "Cannot resolve the operator alias or known_hosts file"
lookup=$host; [[ "$port" == 22 ]] || lookup="[$host]:$port"
pins=$(ssh-keygen -F "$lookup" -f "$known_hosts" | grep -v '^#' || true)
[[ -n "$pins" ]] || pixel_die "No already-trusted host key exists for $lookup; enroll it manually before provisioning"
public_key=$(sudo cat "$key_path.pub")
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
known_tmp=$(mktemp)
trap 'rm -f -- "$config_tmp" "$known_tmp"' EXIT
if sudo test -f "$key_dir/config"; then sudo cat "$key_dir/config" | tee "$config_tmp" >/dev/null; fi
if grep -Eq "^Host[[:space:]]+$target$" "$config_tmp"; then pixel_die "Operations SSH target already exists: $target"; fi
cat >> "$config_tmp" <<EOF

Host $target
  HostName $host
  Port $port
  User pixel-ops-transport
  IdentityFile $key_path
  IdentitiesOnly yes
  UserKnownHostsFile $key_dir/known_hosts
  StrictHostKeyChecking yes
  BatchMode yes
  ForwardAgent no
  ClearAllForwardings yes
EOF
if sudo test -f "$key_dir/known_hosts"; then sudo cat "$key_dir/known_hosts" | tee "$known_tmp" >/dev/null; fi
printf '%s\n' "$pins" >> "$known_tmp"
sort -u "$known_tmp" -o "$known_tmp"
sudo install -o "$PIXEL_OPS_BROKER_USER" -g "$PIXEL_OPS_BROKER_GROUP" -m 0600 "$config_tmp" "$key_dir/config"
sudo install -o "$PIXEL_OPS_BROKER_USER" -g "$PIXEL_OPS_BROKER_GROUP" -m 0600 "$known_tmp" "$key_dir/known_hosts"
broker_observed=$(sudo -u "$PIXEL_OPS_BROKER_USER" ssh "$target" hostname | tr -d '\r')
[[ "$broker_observed" == "$expected" ]] || pixel_die "Dedicated runner identity verification failed"
pixel_log "Enrolled $target with a dedicated Pixel transport identity and isolated pixel-runner workload on $expected"
