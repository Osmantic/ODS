#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ ${1:-} == --confirm && $# == 1 ]] || pixel_die "Usage: ./pixel ops-broker --confirm"
pixel_load_env
# shellcheck source=scripts/lib/broker-reader-acls.sh
source "$ROOT/scripts/lib/broker-reader-acls.sh"
[[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 ]] || pixel_die "Operations limb is disabled in this deployment"
for command in sudo systemctl useradd groupadd gpasswd getent install id python3 jq setfacl; do pixel_require_command "$command"; done

for name in PIXEL_OPS_BROKER_USER PIXEL_OPS_BROKER_GROUP PIXEL_OPS_READER_USER PIXEL_OPS_BROKER_INSTALL_DIR PIXEL_OPS_BROKER_STATE_DIR PIXEL_OPS_BROKER_ENV PIXEL_OPS_BROKER_SYSTEMD_DIR PIXEL_OPS_BROKER_UNIT PIXEL_OPS_POLICY_PATH PIXEL_OPS_REQUEST_DIR PIXEL_OPS_RESULT_DIR PIXEL_OPS_EVENT_DIR PIXEL_OPS_CANCEL_DIR PIXEL_OPS_INVENTORY_PATH; do
  [[ -n ${!name:-} ]] || pixel_die "Missing setting: $name"
done
for name in PIXEL_OPS_BROKER_INSTALL_DIR PIXEL_OPS_BROKER_STATE_DIR PIXEL_OPS_BROKER_SYSTEMD_DIR; do pixel_safe_absolute_dir "${!name}" "$name"; done
[[ "$PIXEL_OPS_REQUEST_DIR" == "$PIXEL_OPS_BROKER_STATE_DIR/"* && "$PIXEL_OPS_RESULT_DIR" == "$PIXEL_OPS_BROKER_STATE_DIR/"* && "$PIXEL_OPS_EVENT_DIR" == "$PIXEL_OPS_BROKER_STATE_DIR/"* && "$PIXEL_OPS_CANCEL_DIR" == "$PIXEL_OPS_BROKER_STATE_DIR/"* ]] || pixel_die "Operations spool directories must stay inside broker state"
[[ "$PIXEL_OPS_BROKER_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || pixel_die "Unsafe Operations Broker user"
[[ "$PIXEL_OPS_BROKER_GROUP" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || pixel_die "Unsafe Operations Broker group"
[[ "$PIXEL_OPS_READER_USER" =~ ^[A-Za-z_][A-Za-z0-9_.-]{0,63}$ ]] || pixel_die "Unsafe Operations reader user"
id "$PIXEL_OPS_READER_USER" >/dev/null 2>&1 || pixel_die "Operations reader user does not exist"
[[ -f "$ROOT/.generated/ops-policy.json" && -f "$ROOT/.generated/ops-broker.env" && -f "$ROOT/.generated/pixel-ops-broker.service" ]] || pixel_die "Generated Operations Broker files are missing; run ./pixel configure"

if ! getent group "$PIXEL_OPS_BROKER_GROUP" >/dev/null; then sudo groupadd --system "$PIXEL_OPS_BROKER_GROUP"; fi
if ! getent passwd "$PIXEL_OPS_BROKER_USER" >/dev/null; then
  sudo useradd --system --gid "$PIXEL_OPS_BROKER_GROUP" --home-dir "$PIXEL_OPS_BROKER_STATE_DIR" --create-home --shell /usr/sbin/nologin "$PIXEL_OPS_BROKER_USER"
fi
# The gateway must never join the authority-bearing broker group: that group can read
# policy. Direct ACLs expose only the request/result projection and work immediately for
# already-running user services whose supplementary groups are stale.
if id -nG "$PIXEL_OPS_READER_USER" | tr ' ' '\n' | grep -Fx "$PIXEL_OPS_BROKER_GROUP" >/dev/null; then
  sudo gpasswd -d "$PIXEL_OPS_READER_USER" "$PIXEL_OPS_BROKER_GROUP" >/dev/null
fi

sudo install -d -o root -g root -m 0755 "$PIXEL_OPS_BROKER_INSTALL_DIR" "$(dirname "$PIXEL_OPS_POLICY_PATH")"
sudo install -d -o "$PIXEL_OPS_BROKER_USER" -g "$PIXEL_OPS_BROKER_GROUP" -m 0750 "$PIXEL_OPS_BROKER_STATE_DIR"
sudo install -d -o "$PIXEL_OPS_READER_USER" -g "$PIXEL_OPS_BROKER_GROUP" -m 2770 "$PIXEL_OPS_REQUEST_DIR" "$PIXEL_OPS_CANCEL_DIR"
for directory in request-archive plans approvals runtime private authority authority/leases; do
  sudo install -d -o "$PIXEL_OPS_BROKER_USER" -g "$PIXEL_OPS_BROKER_GROUP" -m 0700 "$PIXEL_OPS_BROKER_STATE_DIR/$directory"
done
sudo install -d -o "$PIXEL_OPS_BROKER_USER" -g "$PIXEL_OPS_BROKER_GROUP" -m 0700 "$PIXEL_OPS_BROKER_STATE_DIR/.ssh"
for directory in results events artifacts; do
  sudo install -d -o "$PIXEL_OPS_BROKER_USER" -g "$PIXEL_OPS_BROKER_GROUP" -m 2750 "$PIXEL_OPS_BROKER_STATE_DIR/$directory"
done
artifact_root=$(jq -r '.download.stagingRoot // empty' "$ROOT/.generated/ops-policy.json")
if [[ -n "$artifact_root" && "$artifact_root" != "$PIXEL_OPS_BROKER_STATE_DIR/artifacts" ]]; then
  pixel_safe_absolute_dir "$artifact_root" "operations download stagingRoot"
  sudo install -d -o "$PIXEL_OPS_BROKER_USER" -g "$PIXEL_OPS_BROKER_GROUP" -m 2750 "$artifact_root"
fi
sudo install -o root -g root -m 0755 "$ROOT/deploy/ops-broker/broker.py" "$PIXEL_OPS_BROKER_INSTALL_DIR/broker.py"
sudo install -o root -g "$PIXEL_OPS_BROKER_GROUP" -m 0640 "$ROOT/.generated/ops-policy.json" "$PIXEL_OPS_POLICY_PATH"
sudo install -o root -g "$PIXEL_OPS_BROKER_GROUP" -m 0640 "$ROOT/.generated/ops-broker.env" "$PIXEL_OPS_BROKER_ENV"
sudo install -o root -g root -m 0644 "$ROOT/.generated/pixel-ops-broker.service" "$PIXEL_OPS_BROKER_SYSTEMD_DIR/$PIXEL_OPS_BROKER_UNIT"

sudo systemctl daemon-reload
sudo systemctl enable "$PIXEL_OPS_BROKER_UNIT"
sudo systemctl restart "$PIXEL_OPS_BROKER_UNIT"
for _ in {1..40}; do
  sudo systemctl is-active --quiet "$PIXEL_OPS_BROKER_UNIT" && sudo test -f "$PIXEL_OPS_INVENTORY_PATH" && break
  sleep 0.25
done
sudo systemctl is-active --quiet "$PIXEL_OPS_BROKER_UNIT" || pixel_die "Operations Broker service failed to start"
pixel_apply_ops_reader_acls
pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_INVENTORY_PATH" || pixel_die "Gateway owner cannot read the Operations inventory projection"
! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/private" || pixel_die "Gateway owner can read Operations private state"
! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/.ssh" || pixel_die "Gateway owner can read Operations SSH state"
! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_POLICY_PATH" || pixel_die "Gateway owner can read Operations policy"
! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/plans" || pixel_die "Gateway owner can read immutable Operations plans"
! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/approvals" || pixel_die "Gateway owner can read Operations approvals"
! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/authority" || pixel_die "Gateway owner can read Operations authority state"
pixel_log "Operations Broker installed with isolated policy, spool, and execution identity"
