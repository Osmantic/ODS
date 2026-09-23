#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
[[ $# == 1 && $1 == --confirm ]] || pixel_die "Usage: ./pixel rotate gateway --confirm"
pixel_load_env
pixel_acquire_deployment_lock exclusive
for command in openssl python3; do pixel_require_command "$command"; done

config="$OPENCLAW_HOME/openclaw.json"
env_file="$ROOT/.env"
[[ -f "$config" && ! -L "$config" && -f "$env_file" && ! -L "$env_file" ]] || pixel_die "Gateway configuration or deployment environment is missing or unsafe"
install -d -m 700 "$OPENCLAW_HOME/backups"
backup="$OPENCLAW_HOME/backups/gateway-rotation-$(pixel_timestamp)-$$"
install -d -m 700 "$backup"
cp -p -- "$config" "$backup/openclaw.json"
cp -p -- "$env_file" "$backup/deployment.env"
new_token=$(openssl rand -hex 32)
[[ "$new_token" =~ ^[0-9a-f]{64}$ ]] || pixel_die "Could not generate a gateway token"
config_tmp="$config.rotate.$$"
env_tmp="$env_file.rotate.$$"
cleanup_rotation_files() { rm -f -- "$config_tmp" "$env_tmp"; }
rollback_rotation() {
  local status=$?
  cleanup_rotation_files
  [[ $status == 0 ]] && return
  pixel_warn "Gateway rotation failed; restoring the preceding credential"
  install -m 600 "$backup/openclaw.json" "$config"
  install -m 600 "$backup/deployment.env" "$env_file"
  pixel_systemctl restart "$PIXEL_SYSTEMD_UNIT" >/dev/null 2>&1 || true
  exit "$status"
}
trap rollback_rotation EXIT
python3 - "$env_file" "$config" "$env_tmp" "$config_tmp" "$new_token" <<'PY'
import json, os, pathlib, re, sys
env_path, config_path, env_output, config_output = map(pathlib.Path, sys.argv[1:5])
token = sys.argv[5]
if not re.fullmatch(r"[0-9a-f]{64}", token):
    raise SystemExit("invalid generated token")
lines = env_path.read_text(encoding="utf-8").splitlines()
indexes = [index for index, line in enumerate(lines) if line.startswith("PIXEL_GATEWAY_TOKEN=")]
if len(indexes) != 1:
    raise SystemExit("deployment environment must contain exactly one PIXEL_GATEWAY_TOKEN")
lines[indexes[0]] = f"PIXEL_GATEWAY_TOKEN='{token}'"
config = json.loads(config_path.read_text(encoding="utf-8"))
if not isinstance(config, dict):
    raise SystemExit("gateway configuration root is malformed")
gateway = config.setdefault("gateway", {})
if not isinstance(gateway, dict):
    raise SystemExit("gateway configuration is malformed")
gateway["auth"] = {"token": token}
for path, content in (
    (env_output, "\n".join(lines) + "\n"),
    (config_output, json.dumps(config, indent=2) + "\n"),
):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
PY
chmod 600 "$config_tmp" "$env_tmp"
mv -f -- "$config_tmp" "$config"
mv -f -- "$env_tmp" "$env_file"
pixel_systemctl restart "$PIXEL_SYSTEMD_UNIT"
bash "$ROOT/scripts/verify.sh" >/dev/null
printf '%s\n' '{"schemaVersion":1,"credential":"gateway","status":"rotated","secretRecorded":false}' > "$backup/result.json"
chmod 600 "$backup/result.json"
trap - EXIT
cleanup_rotation_files
pixel_log "Rotated the gateway credential transactionally; the previous value remains only in the private rollback record $backup"
