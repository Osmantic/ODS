#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ $# == 4 && $4 == --confirm ]] || pixel_die "Usage: ./pixel ops-target-actions OPERATOR_SSH_ALIAS ACTIONS.json MANAGED.json --confirm"
operator_alias=$1
actions_file=$2
managed_file=$3
[[ "$operator_alias" =~ ^[A-Za-z0-9_.@:-]{1,255}$ ]] || pixel_die "Unsafe operator SSH alias"
for file in "$actions_file" "$managed_file"; do
  [[ -f "$file" && ! -L "$file" ]] || pixel_die "Action configuration must be a regular non-symlink file: $file"
  [[ $(wc -c < "$file") -le 1048576 ]] || pixel_die "Action configuration exceeds 1 MiB: $file"
  jq -e '.schemaVersion == 1 and type == "object"' "$file" >/dev/null || pixel_die "Invalid action configuration: $file"
done
for command in ssh scp jq wc; do pixel_require_command "$command"; done

actions_stage=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$operator_alias" 'mktemp /tmp/pixel-actions.XXXXXX.json')
managed_stage=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$operator_alias" 'mktemp /tmp/pixel-managed.XXXXXX.json')
sudoers_stage=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$operator_alias" 'mktemp /tmp/pixel-sudoers.XXXXXX')
[[ "$actions_stage" == /tmp/pixel-actions.*.json && "$managed_stage" == /tmp/pixel-managed.*.json && "$sudoers_stage" == /tmp/pixel-sudoers.* ]] || pixel_die "Remote staging path is unsafe"
cleanup() {
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$operator_alias" "rm -f -- '$actions_stage' '$managed_stage' '$sudoers_stage'" >/dev/null 2>&1 || true
}
trap cleanup EXIT
scp -q "$actions_file" "$operator_alias:$actions_stage"
scp -q "$managed_file" "$operator_alias:$managed_stage"

sudoers_tmp=$(mktemp)
trap 'rm -f -- "$sudoers_tmp"; cleanup' EXIT
cat > "$sudoers_tmp" <<'EOF'
Cmnd_Alias PIXEL_OPS_MANAGED = /usr/local/libexec/pixel-ops-managed service *, /usr/local/libexec/pixel-ops-managed deploy *, /usr/local/libexec/pixel-ops-managed package *, /usr/local/libexec/pixel-ops-managed host reboot approved, /usr/local/libexec/pixel-ops-managed --validate-config
pixel-ops-transport ALL=(root) NOPASSWD: PIXEL_OPS_MANAGED
EOF
scp -q "$sudoers_tmp" "$operator_alias:$sudoers_stage"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$operator_alias" "sudo getent passwd pixel-ops-transport >/dev/null && sudo getent passwd pixel-runner >/dev/null && sudo install -d -o root -g root -m 0755 /etc/pixel-ops-runner && sudo install -o root -g root -m 0644 '$actions_stage' /etc/pixel-ops-runner/actions.json && sudo install -o root -g root -m 0600 '$managed_stage' /etc/pixel-ops-runner/managed.json && sudo visudo -cf '$sudoers_stage' && sudo install -o root -g root -m 0440 '$sudoers_stage' /etc/sudoers.d/pixel-ops-managed && sudo -u pixel-runner /usr/local/libexec/pixel-ops-action --validate-config && sudo -u pixel-ops-transport sudo --non-interactive --user root -- /usr/local/libexec/pixel-ops-managed --validate-config && ! sudo -u pixel-runner sudo --non-interactive --user root -- /usr/local/libexec/pixel-ops-managed --validate-config >/dev/null 2>&1"
pixel_log "Installed root-owned action-pack configuration; only the isolated transport identity can invoke managed root actions on $operator_alias"
