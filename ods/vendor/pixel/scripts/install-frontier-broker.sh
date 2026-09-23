#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ ${1:-} == --confirm && $# == 1 ]] || pixel_die "Usage: ./pixel frontier-broker --confirm"
pixel_load_env
# shellcheck source=scripts/lib/broker-reader-acls.sh
source "$ROOT/scripts/lib/broker-reader-acls.sh"
[[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 ]] || pixel_die "Frontier limb is disabled in this deployment"
for command in sudo systemctl useradd groupadd gpasswd getent install id python3 jq setfacl; do pixel_require_command "$command"; done

for name in PIXEL_FRONTIER_BROKER_USER PIXEL_FRONTIER_BROKER_GROUP PIXEL_FRONTIER_READER_USER PIXEL_FRONTIER_BROKER_INSTALL_DIR PIXEL_FRONTIER_BROKER_STATE_DIR PIXEL_FRONTIER_BROKER_ENV PIXEL_FRONTIER_BROKER_SYSTEMD_DIR PIXEL_FRONTIER_BROKER_UNIT PIXEL_FRONTIER_POLICY_PATH PIXEL_FRONTIER_CREDENTIAL_SOURCE PIXEL_FRONTIER_CREDENTIAL_PATH PIXEL_FRONTIER_REQUEST_DIR PIXEL_FRONTIER_RESULT_DIR PIXEL_FRONTIER_EVENT_DIR PIXEL_FRONTIER_CANCEL_DIR PIXEL_FRONTIER_FEEDBACK_DIR; do
  [[ -n ${!name:-} ]] || pixel_die "Missing setting: $name"
done
for name in PIXEL_FRONTIER_BROKER_INSTALL_DIR PIXEL_FRONTIER_BROKER_STATE_DIR PIXEL_FRONTIER_BROKER_SYSTEMD_DIR; do pixel_safe_absolute_dir "${!name}" "$name"; done
[[ "$PIXEL_FRONTIER_REQUEST_DIR" == "$PIXEL_FRONTIER_BROKER_STATE_DIR/"* && "$PIXEL_FRONTIER_RESULT_DIR" == "$PIXEL_FRONTIER_BROKER_STATE_DIR/"* && "$PIXEL_FRONTIER_EVENT_DIR" == "$PIXEL_FRONTIER_BROKER_STATE_DIR/"* && "$PIXEL_FRONTIER_CANCEL_DIR" == "$PIXEL_FRONTIER_BROKER_STATE_DIR/"* && "$PIXEL_FRONTIER_FEEDBACK_DIR" == "$PIXEL_FRONTIER_BROKER_STATE_DIR/"* ]] || pixel_die "Frontier spool directories must stay inside broker state"
[[ "$PIXEL_FRONTIER_BROKER_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || pixel_die "Unsafe Frontier Broker user"
[[ "$PIXEL_FRONTIER_BROKER_GROUP" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || pixel_die "Unsafe Frontier Broker group"
[[ "$PIXEL_FRONTIER_READER_USER" =~ ^[A-Za-z_][A-Za-z0-9_.-]{0,63}$ ]] || pixel_die "Unsafe Frontier reader user"
[[ "$PIXEL_FRONTIER_READER_USER" != "$PIXEL_FRONTIER_BROKER_USER" ]] || pixel_die "Frontier reader and broker identities must differ"
id "$PIXEL_FRONTIER_READER_USER" >/dev/null 2>&1 || pixel_die "Frontier reader user does not exist"
[[ -f "$ROOT/.generated/frontier-policy.json" && -f "$ROOT/.generated/frontier-broker.env" && -f "$ROOT/.generated/pixel-frontier-broker.service" ]] || pixel_die "Generated Frontier Broker files are missing; run ./pixel configure"
provider=$(jq -r '.provider.kind' "$ROOT/.generated/frontier-policy.json")
[[ "$provider" == codex ]] || pixel_die "Only the codex provider may be installed outside explicit test mode"
auth_mode=$(jq -r '.provider.authMode // "api-key"' "$ROOT/.generated/frontier-policy.json")
[[ "$auth_mode" == api-key || "$auth_mode" == chatgpt ]] || pixel_die "Frontier auth mode must be api-key or chatgpt"
[[ ${PIXEL_FRONTIER_AUTH_MODE:-$auth_mode} == "$auth_mode" ]] || pixel_die "Generated Frontier auth mode disagrees with policy"
[[ -f "$PIXEL_FRONTIER_CREDENTIAL_SOURCE" && ! -L "$PIXEL_FRONTIER_CREDENTIAL_SOURCE" ]] || pixel_die "Frontier credential source must be a regular non-symlink file"
if [[ "$auth_mode" == api-key ]]; then
  [[ "$PIXEL_FRONTIER_CREDENTIAL_PATH" == "$PIXEL_FRONTIER_BROKER_STATE_DIR/private/provider-key" ]] || pixel_die "API-key credential target escaped the private Frontier path"
  [[ -s "$PIXEL_FRONTIER_CREDENTIAL_SOURCE" && $(wc -c < "$PIXEL_FRONTIER_CREDENTIAL_SOURCE") -le 8192 ]] || pixel_die "Frontier credential source is empty or too large"
  [[ $(awk 'END { print NR }' "$PIXEL_FRONTIER_CREDENTIAL_SOURCE") -le 1 ]] || pixel_die "Frontier credential source must contain one line"
  if LC_ALL=C grep -q $'\r' "$PIXEL_FRONTIER_CREDENTIAL_SOURCE"; then pixel_die "Frontier credential source contains a carriage return"; fi
  python3 - "$PIXEL_FRONTIER_CREDENTIAL_SOURCE" <<'PY' || pixel_die "Frontier credential must be 8..8192 printable ASCII characters without spaces"
import pathlib
import sys

value = pathlib.Path(sys.argv[1]).read_bytes()
if value.endswith(b"\n"):
    value = value[:-1]
raise SystemExit(0 if 8 <= len(value) <= 8192 and all(33 <= byte <= 126 for byte in value) else 1)
PY
else
  [[ "$PIXEL_FRONTIER_CREDENTIAL_PATH" == "$PIXEL_FRONTIER_BROKER_STATE_DIR/private/codex-auth/auth.json" ]] || pixel_die "ChatGPT auth target escaped the private Frontier path"
  python3 - "$PIXEL_FRONTIER_CREDENTIAL_SOURCE" <<'PY' || pixel_die "ChatGPT auth source must be a private, bounded, non-empty JSON object"
import json
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 2 <= info.st_size <= 1024 * 1024:
    raise SystemExit(1)
if stat.S_IMODE(info.st_mode) & 0o077:
    raise SystemExit(1)
value = json.loads(path.read_text(encoding="utf-8"))
raise SystemExit(0 if isinstance(value, dict) and value else 1)
PY
fi
codex_binary=$(jq -r '.provider.codexBinary' "$ROOT/.generated/frontier-policy.json")
[[ "$codex_binary" == /* && -x "$codex_binary" ]] || pixel_die "Configured Codex executable is missing or not executable"
"$codex_binary" exec --help | grep -F -- '--ignore-user-config' >/dev/null || pixel_die "Configured Codex lacks --ignore-user-config"
"$codex_binary" exec --help | grep -F -- '--output-schema' >/dev/null || pixel_die "Configured Codex lacks structured output support"
if [[ "$auth_mode" == api-key ]]; then
  "$codex_binary" login --help | grep -F -- '--with-api-key' >/dev/null || pixel_die "Configured Codex lacks API-key login support"
fi
python3 "$ROOT/scripts/verify-frontier-codex.py" "$codex_binary" >/dev/null || pixel_die "Configured Codex failed the offline config/tool-surface qualification"

if ! getent group "$PIXEL_FRONTIER_BROKER_GROUP" >/dev/null; then sudo groupadd --system "$PIXEL_FRONTIER_BROKER_GROUP"; fi
if ! getent passwd "$PIXEL_FRONTIER_BROKER_USER" >/dev/null; then
  sudo useradd --system --gid "$PIXEL_FRONTIER_BROKER_GROUP" --home-dir "$PIXEL_FRONTIER_BROKER_STATE_DIR" --create-home --shell /usr/sbin/nologin "$PIXEL_FRONTIER_BROKER_USER"
fi
if id -nG "$PIXEL_FRONTIER_READER_USER" | tr ' ' '\n' | grep -Fx "$PIXEL_FRONTIER_BROKER_GROUP" >/dev/null; then
  sudo gpasswd -d "$PIXEL_FRONTIER_READER_USER" "$PIXEL_FRONTIER_BROKER_GROUP" >/dev/null
fi

sudo install -d -o root -g root -m 0755 "$PIXEL_FRONTIER_BROKER_INSTALL_DIR" "$(dirname "$PIXEL_FRONTIER_POLICY_PATH")"
sudo install -d -o "$PIXEL_FRONTIER_BROKER_USER" -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 0750 "$PIXEL_FRONTIER_BROKER_STATE_DIR"
sudo install -d -o "$PIXEL_FRONTIER_READER_USER" -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 2770 "$PIXEL_FRONTIER_REQUEST_DIR" "$PIXEL_FRONTIER_CANCEL_DIR" "$PIXEL_FRONTIER_FEEDBACK_DIR"
for directory in request-archive plans approvals runtime authority authority/leases cache integrations; do
  sudo install -d -o "$PIXEL_FRONTIER_BROKER_USER" -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 0700 "$PIXEL_FRONTIER_BROKER_STATE_DIR/$directory"
done
# The worker may read the root-owned provider key through its group, but cannot
# replace it by unlinking or renaming the containing directory entry.
sudo install -d -o root -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 0750 "$PIXEL_FRONTIER_BROKER_STATE_DIR/private"
for directory in results events metrics; do
  sudo install -d -o "$PIXEL_FRONTIER_BROKER_USER" -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 2750 "$PIXEL_FRONTIER_BROKER_STATE_DIR/$directory"
done
sudo install -d -o "$PIXEL_FRONTIER_BROKER_USER" -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 2750 "$PIXEL_FRONTIER_BROKER_STATE_DIR/metrics/qualifications"
sudo install -o root -g root -m 0755 "$ROOT/deploy/frontier-broker/broker.py" "$PIXEL_FRONTIER_BROKER_INSTALL_DIR/broker.py"
sudo install -o root -g root -m 0755 "$ROOT/scripts/verify-frontier-codex.py" "$PIXEL_FRONTIER_BROKER_INSTALL_DIR/verify-codex.py"
sudo install -o root -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 0640 "$ROOT/.generated/frontier-policy.json" "$PIXEL_FRONTIER_POLICY_PATH"
sudo install -o root -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 0640 "$ROOT/.generated/frontier-broker.env" "$PIXEL_FRONTIER_BROKER_ENV"
if [[ "$auth_mode" == api-key ]]; then
  sudo rm -f -- "$PIXEL_FRONTIER_BROKER_STATE_DIR/private/codex-auth/auth.json"
  sudo rmdir -- "$PIXEL_FRONTIER_BROKER_STATE_DIR/private/codex-auth" 2>/dev/null || true
  sudo install -o root -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 0640 "$PIXEL_FRONTIER_CREDENTIAL_SOURCE" "$PIXEL_FRONTIER_CREDENTIAL_PATH"
else
  sudo rm -f -- "$PIXEL_FRONTIER_BROKER_STATE_DIR/private/provider-key"
  frontier_auth_dir=$(dirname "$PIXEL_FRONTIER_CREDENTIAL_PATH")
  sudo install -d -o "$PIXEL_FRONTIER_BROKER_USER" -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 0700 "$frontier_auth_dir"
  sudo install -o "$PIXEL_FRONTIER_BROKER_USER" -g "$PIXEL_FRONTIER_BROKER_GROUP" -m 0600 "$PIXEL_FRONTIER_CREDENTIAL_SOURCE" "$PIXEL_FRONTIER_CREDENTIAL_PATH"
  login_status=$(sudo -u "$PIXEL_FRONTIER_BROKER_USER" env -i PATH="$(dirname "$codex_binary"):/usr/local/bin:/usr/bin:/bin" HOME="$frontier_auth_dir" CODEX_HOME="$frontier_auth_dir" LANG=C.UTF-8 \
    "$codex_binary" login status -c 'cli_auth_credentials_store="file"' 2>&1) || pixel_die "Imported ChatGPT Codex login is not active"
  [[ "$login_status" == *ChatGPT* ]] || pixel_die "Imported Codex login is not ChatGPT-authenticated; refusing to mislabel API billing as subscription usage"
fi
sudo install -o root -g root -m 0644 "$ROOT/.generated/pixel-frontier-broker.service" "$PIXEL_FRONTIER_BROKER_SYSTEMD_DIR/$PIXEL_FRONTIER_BROKER_UNIT"
sudo -u "$PIXEL_FRONTIER_BROKER_USER" "$PIXEL_FRONTIER_BROKER_INSTALL_DIR/broker.py" \
  --policy "$PIXEL_FRONTIER_POLICY_PATH" --state "$PIXEL_FRONTIER_BROKER_STATE_DIR" --usage-refresh >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$PIXEL_FRONTIER_BROKER_UNIT"
sudo systemctl restart "$PIXEL_FRONTIER_BROKER_UNIT"
for _ in {1..40}; do
  sudo systemctl is-active --quiet "$PIXEL_FRONTIER_BROKER_UNIT" && break
  sleep 0.25
done
sudo systemctl is-active --quiet "$PIXEL_FRONTIER_BROKER_UNIT" || pixel_die "Frontier Broker service failed to start"
pixel_apply_frontier_reader_acls
pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" w "$PIXEL_FRONTIER_REQUEST_DIR" || pixel_die "Gateway owner cannot publish Frontier requests"
pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" w "$PIXEL_FRONTIER_FEEDBACK_DIR" || pixel_die "Gateway owner cannot publish content-free Frontier integration receipts"
pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_RESULT_DIR" || pixel_die "Gateway owner cannot read Frontier result projections"
pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_BROKER_STATE_DIR/metrics/usage.json" || pixel_die "Gateway owner cannot read the content-free Frontier usage projection"
! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_CREDENTIAL_PATH" || pixel_die "Gateway owner can read Frontier provider credential"
! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_POLICY_PATH" || pixel_die "Gateway owner can read Frontier policy"
if [[ "$auth_mode" == api-key ]]; then
  ! pixel_user_can_access "$PIXEL_FRONTIER_BROKER_USER" w "$(dirname "$PIXEL_FRONTIER_CREDENTIAL_PATH")" || pixel_die "Frontier worker can replace its API key"
else
  pixel_user_can_access "$PIXEL_FRONTIER_BROKER_USER" w "$PIXEL_FRONTIER_CREDENTIAL_PATH" || pixel_die "Frontier worker cannot refresh its ChatGPT auth cache"
  ! pixel_user_can_access "$PIXEL_FRONTIER_BROKER_USER" w "$PIXEL_FRONTIER_BROKER_STATE_DIR/private" || pixel_die "Frontier worker can replace its ChatGPT auth directory"
fi
for directory in private request-archive plans approvals authority runtime cache integrations; do
  ! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_BROKER_STATE_DIR/$directory" || pixel_die "Gateway owner can read Frontier $directory state"
done
pixel_log "Frontier Broker installed with isolated credential, policy, spool, and provider worker"
