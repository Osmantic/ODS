#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ $# == 1 && $1 == --confirm ]] || pixel_die "Usage: ./pixel ops-keygen --confirm"
pixel_load_env
[[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 ]] || pixel_die "Operations limb is disabled"
for command in sudo ssh-keygen; do pixel_require_command "$command"; done
key_dir="$PIXEL_OPS_BROKER_STATE_DIR/.ssh"
key_path="$key_dir/id_ed25519"
sudo install -d -o "$PIXEL_OPS_BROKER_USER" -g "$PIXEL_OPS_BROKER_GROUP" -m 0700 "$key_dir"
if sudo test -e "$key_path" || sudo test -e "$key_path.pub"; then pixel_die "Operations Broker key already exists; refusing to rotate it implicitly"; fi
sudo -u "$PIXEL_OPS_BROKER_USER" ssh-keygen -q -t ed25519 -N '' -C "pixel-ops-${PIXEL_AGENT_ID}@${PIXEL_DEPLOYMENT_PROFILE}" -f "$key_path"
sudo chmod 0600 "$key_path"; sudo chmod 0644 "$key_path.pub"
pixel_log "Generated an isolated Operations Broker key. Enroll targets individually with ./pixel ops-target ..."
