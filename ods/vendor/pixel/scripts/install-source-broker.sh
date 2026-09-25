#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ ${1:-} == --confirm && $# == 1 ]] || pixel_die "Usage: ./pixel source-broker --confirm"
pixel_load_env
for command in sudo systemctl useradd getent install id; do pixel_require_command "$command"; done

for name in PIXEL_SOURCE_BROKER_USER PIXEL_SOURCE_READER_USER PIXEL_SOURCE_BROKER_INSTALL_DIR PIXEL_SOURCE_BROKER_STATE_DIR PIXEL_SOURCE_PROJECTION_DIR PIXEL_ACTION_PROPOSAL_DIR PIXEL_ACTION_RESULT_DIR PIXEL_SOURCE_TOKEN_PATH PIXEL_SOURCE_BROKER_ENV PIXEL_SOURCE_BROKER_SYSTEMD_DIR PIXEL_SOURCE_BROKER_UNIT PIXEL_SOURCE_BROKER_TIMER PIXEL_SOURCE_ACTION_UNIT PIXEL_SOURCE_RECONCILE_UNIT PIXEL_SOURCE_DIRECT_UNIT PIXEL_SOURCE_DIRECT_PATH_UNIT; do
  [[ -n ${!name:-} ]] || pixel_die "Missing setting: $name"
done
for name in PIXEL_SOURCE_BROKER_INSTALL_DIR PIXEL_SOURCE_BROKER_STATE_DIR PIXEL_SOURCE_PROJECTION_DIR PIXEL_ACTION_PROPOSAL_DIR PIXEL_ACTION_RESULT_DIR; do pixel_safe_absolute_dir "${!name}" "$name"; done
[[ "$PIXEL_SOURCE_PROJECTION_DIR" == "$PIXEL_SOURCE_BROKER_STATE_DIR/"* ]] || pixel_die "Projection directory must be inside broker state"
[[ "$PIXEL_ACTION_PROPOSAL_DIR" == "$PIXEL_SOURCE_BROKER_STATE_DIR/"* ]] || pixel_die "Proposal directory must be inside broker state"
[[ "$PIXEL_ACTION_RESULT_DIR" == "$PIXEL_SOURCE_BROKER_STATE_DIR/"* ]] || pixel_die "Result directory must be inside broker state"
[[ "$PIXEL_SOURCE_BROKER_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || pixel_die "Unsafe Source Broker user"
[[ "$PIXEL_SOURCE_READER_USER" =~ ^[A-Za-z_][A-Za-z0-9_.-]{0,63}$ ]] || pixel_die "Unsafe projection reader user"
id "$PIXEL_SOURCE_READER_USER" >/dev/null 2>&1 || pixel_die "Projection reader user does not exist"

if ! getent passwd "$PIXEL_SOURCE_BROKER_USER" >/dev/null; then
  sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin "$PIXEL_SOURCE_BROKER_USER"
fi
reader_group=$(id -gn "$PIXEL_SOURCE_READER_USER")
broker_group=$(id -gn "$PIXEL_SOURCE_BROKER_USER")

sudo install -d -o root -g root -m 0755 "$PIXEL_SOURCE_BROKER_INSTALL_DIR"
sudo install -d -o "$PIXEL_SOURCE_BROKER_USER" -g "$broker_group" -m 0700 "$PIXEL_SOURCE_BROKER_STATE_DIR/private"
sudo install -d -o "$PIXEL_SOURCE_BROKER_USER" -g "$reader_group" -m 2750 "$PIXEL_SOURCE_PROJECTION_DIR" "$PIXEL_ACTION_RESULT_DIR"
sudo install -d -o "$PIXEL_SOURCE_READER_USER" -g "$broker_group" -m 2750 "$PIXEL_ACTION_PROPOSAL_DIR"
sudo install -o root -g root -m 0755 "$ROOT/deploy/source-broker/broker.py" "$PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py"
sudo install -o root -g "$broker_group" -m 0640 "$ROOT/.generated/source-broker.env" "$PIXEL_SOURCE_BROKER_ENV"
sudo install -o root -g root -m 0644 "$ROOT/.generated/pixel-source-broker.service" "$PIXEL_SOURCE_BROKER_SYSTEMD_DIR/$PIXEL_SOURCE_BROKER_UNIT"
sudo install -o root -g root -m 0644 "$ROOT/.generated/pixel-source-broker.timer" "$PIXEL_SOURCE_BROKER_SYSTEMD_DIR/$PIXEL_SOURCE_BROKER_TIMER"
sudo install -o root -g root -m 0644 "$ROOT/.generated/pixel-source-action@.service" "$PIXEL_SOURCE_BROKER_SYSTEMD_DIR/$PIXEL_SOURCE_ACTION_UNIT"
sudo install -o root -g root -m 0644 "$ROOT/.generated/pixel-source-reconcile@.service" "$PIXEL_SOURCE_BROKER_SYSTEMD_DIR/$PIXEL_SOURCE_RECONCILE_UNIT"
sudo install -o root -g root -m 0644 "$ROOT/.generated/pixel-source-direct.service" "$PIXEL_SOURCE_BROKER_SYSTEMD_DIR/$PIXEL_SOURCE_DIRECT_UNIT"
sudo install -o root -g root -m 0644 "$ROOT/.generated/pixel-source-direct.path" "$PIXEL_SOURCE_BROKER_SYSTEMD_DIR/$PIXEL_SOURCE_DIRECT_PATH_UNIT"

source_token=$PIXEL_GOOGLE_TOKEN_PATH
google_source_enabled=0
[[ ${PIXEL_LIMB_EMAIL_ENABLED:-1} == 1 || ${PIXEL_LIMB_CALENDAR_ENABLED:-1} == 1 ]] && google_source_enabled=1
if [[ $google_source_enabled == 1 && -f "$source_token" ]]; then
  sudo install -o "$PIXEL_SOURCE_BROKER_USER" -g "$broker_group" -m 0600 "$source_token" "$PIXEL_SOURCE_TOKEN_PATH"
fi
if [[ $google_source_enabled == 1 ]]; then
  sudo test -f "$PIXEL_SOURCE_TOKEN_PATH" || pixel_die "No broker OAuth token exists; run ./pixel authorize first"
fi

sudo systemctl daemon-reload
sudo systemctl start "$PIXEL_SOURCE_BROKER_UNIT"
sudo systemctl enable --now "$PIXEL_SOURCE_BROKER_TIMER"
if [[ ${PIXEL_CALENDAR_DIRECT_ENABLED:-0} == 1 ]]; then
  sudo systemctl enable "$PIXEL_SOURCE_DIRECT_UNIT"
  sudo systemctl enable --now "$PIXEL_SOURCE_DIRECT_PATH_UNIT"
  sudo systemctl start "$PIXEL_SOURCE_DIRECT_UNIT"
else
  sudo systemctl disable --now "$PIXEL_SOURCE_DIRECT_PATH_UNIT" >/dev/null 2>&1 || true
  sudo systemctl disable "$PIXEL_SOURCE_DIRECT_UNIT" >/dev/null 2>&1 || true
fi
for projection in email.json calendar.json social.json; do
  pixel_user_can_access "$PIXEL_SOURCE_READER_USER" r "$PIXEL_SOURCE_PROJECTION_DIR/$projection" || pixel_die "Projection is not readable: $projection"
done

target_resolved=""
if [[ $google_source_enabled == 1 ]]; then target_resolved=$(sudo realpath -e -- "$PIXEL_SOURCE_TOKEN_PATH"); fi
if [[ $google_source_enabled == 1 && -f "$source_token" ]]; then
  source_resolved=$(realpath -e -- "$source_token")
  if [[ "$source_resolved" != "$target_resolved" ]]; then rm -f -- "$source_resolved"; fi
fi
if [[ -f ${PIXEL_GOOGLE_CLIENT_FILE:-} ]]; then rm -f -- "$PIXEL_GOOGLE_CLIENT_FILE"; fi
pixel_log "Source Broker installed; Google credential migrated to the isolated service identity"
