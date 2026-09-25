#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
for command in git node python3 sha256sum ssh-keygen tar; do command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }; done
[[ -x "$ROOT/tests/fixtures/bin/age" ]] || { echo "Missing fake age fixture" >&2; exit 1; }
tmp=$(mktemp -d)
cleanup() { rm -rf -- "$tmp"; }
trap cleanup EXIT

copy_source() {
  local destination=$1
  mkdir -p "$destination"
  tar -C "$ROOT" --exclude=.git --exclude=.env --exclude=.generated --exclude=.runtime --exclude=dist --exclude=node_modules --exclude=__pycache__ -cf - . | tar -C "$destination" -xf -
  chmod +x "$destination/pixel" "$destination/scripts/"*.sh "$destination/tests/fixtures/bin/"*
}

configure_client() {
  local label=$1 port=$2 repository=$3 state=$4
  mkdir -p "$state/home"
  python3 - "$repository/answers.json" "$repository" "$state" "$label" "$port" <<'PY'
import json, pathlib, sys
output, repository, state, label, port = sys.argv[1:]
state = pathlib.Path(state)
value = {
    "deploymentProfile": "prepared", "capabilityProfile": "minimal",
    "ownerName": f"Isolation Owner {label}", "organization": f"Client {label}",
    "deploymentName": f"isolation-{label.lower()}", "timeZone": "America/New_York",
    "agentId": "pixel", "agentName": f"Pixel {label}",
    "openclawBin": f"{repository}/tests/fixtures/bin/openclaw",
    "openclawHome": str(state / "home" / ".openclaw"),
    "installDir": str(state / "home" / ".local" / "share" / "pixel"),
    "workspace": str(state / "home" / ".openclaw" / "workspace-pixel"),
    "modelProvider": "local", "modelId": "test-model", "modelName": "Test Model",
    "modelBaseUrl": "http://127.0.0.1:8000/v1", "modelApiKey": "local-no-auth",
    "modelContextWindow": 8192, "modelMaxTokens": 1024,
    "searxngBaseUrl": "http://127.0.0.1:8890", "embeddingModel": "test.gguf",
    "embeddingCache": str(state / "home" / ".cache" / "embeddings"),
    "googleAccount": f"isolation-{label.lower()}@example.invalid", "calendarId": "primary",
    "gatewayPort": int(port), "emailLimbEnabled": False, "calendarLimbEnabled": False,
    "socialLimbEnabled": False, "webLimbEnabled": False, "operationsLimbEnabled": False,
}
pathlib.Path(output).write_text(json.dumps(value), encoding="utf-8")
PY
  (cd "$repository" && HOME="$state/home" XDG_CONFIG_HOME="$state/home/.config" ./pixel configure --answers answers.json >/dev/null)
  cat >> "$repository/.env" <<EOF
PIXEL_SOURCE_BROKER_STATE_DIR='$state/source-state'
PIXEL_SOURCE_BROKER_ENV='$state/source.env'
PIXEL_GOOGLE_TOKEN_PATH='$state/source-state/private/google-token.json'
PIXEL_OPS_BROKER_STATE_DIR='$state/ops-state'
PIXEL_OPS_BROKER_ENV='$state/ops.env'
PIXEL_OPS_POLICY_PATH='$state/ops-policy/policy.json'
EOF
  mkdir -p "$state/home/.openclaw/workspace-pixel"
  printf 'client-isolation-canary-%s\n' "$label" > "$state/home/.openclaw/workspace-pixel/IDENTITY.md"
}

repo_a="$tmp/repo-a"; repo_b="$tmp/repo-b"
state_a="$tmp/state-a"; state_b="$tmp/state-b"
copy_source "$repo_a"; copy_source "$repo_b"
configure_client alpha 19101 "$repo_a" "$state_a"
configure_client beta 19102 "$repo_b" "$state_b"

if grep -R -F 'Client beta' "$repo_a/.env" "$repo_a/.generated" >/dev/null; then echo "Client beta configuration crossed into alpha" >&2; exit 1; fi
if grep -R -F 'Client alpha' "$repo_b/.env" "$repo_b/.generated" >/dev/null; then echo "Client alpha configuration crossed into beta" >&2; exit 1; fi
token_a=$(awk -F= '/^PIXEL_GATEWAY_TOKEN=/ {gsub(/\047/, "", $2); print $2}' "$repo_a/.env")
token_b=$(awk -F= '/^PIXEL_GATEWAY_TOKEN=/ {gsub(/\047/, "", $2); print $2}' "$repo_b/.env")
[[ "$token_a" =~ ^[0-9a-f]{64}$ && "$token_b" =~ ^[0-9a-f]{64}$ && "$token_a" != "$token_b" ]]

identity_a="$tmp/identity-a"; identity_b="$tmp/identity-b"
printf '%s\n' age1-isolation-alpha > "$identity_a"
printf '%s\n' age1-isolation-beta > "$identity_b"
chmod 600 "$identity_a" "$identity_b"
backup_a=$(cd "$repo_a" && PATH="$repo_a/tests/fixtures/bin:$PATH" HOME="$state_a/home" XDG_CONFIG_HOME="$state_a/home/.config" PIXEL_BACKUP_AGE_RECIPIENT=age1-isolation-alpha ./pixel backup "$tmp/backups-a")
backup_b=$(cd "$repo_b" && PATH="$repo_b/tests/fixtures/bin:$PATH" HOME="$state_b/home" XDG_CONFIG_HOME="$state_b/home/.config" PIXEL_BACKUP_AGE_RECIPIENT=age1-isolation-beta ./pixel backup "$tmp/backups-b")
signers_a="$state_a/home/.config/pixel-deployment/backup-allowed-signers"
signers_b="$state_b/home/.config/pixel-deployment/backup-allowed-signers"
cmp -s "$signers_a" "$signers_b" && { echo "Clients share a backup trust anchor" >&2; exit 1; }

(cd "$repo_a" && PATH="$repo_a/tests/fixtures/bin:$PATH" HOME="$state_a/home" XDG_CONFIG_HOME="$state_a/home/.config" ./pixel restore "$backup_a" --identity "$identity_a" --validate-only >/dev/null)
(cd "$repo_b" && PATH="$repo_b/tests/fixtures/bin:$PATH" HOME="$state_b/home" XDG_CONFIG_HOME="$state_b/home/.config" ./pixel restore "$backup_b" --identity "$identity_b" --validate-only >/dev/null)
if (cd "$repo_b" && PATH="$repo_b/tests/fixtures/bin:$PATH" HOME="$state_b/home" XDG_CONFIG_HOME="$state_b/home/.config" ./pixel restore "$backup_a" --identity "$identity_a" --signers "$signers_b" --validate-only >/dev/null 2>&1); then
  echo "Client beta trusted client alpha's signer" >&2; exit 1
fi
if (cd "$repo_b" && PATH="$repo_b/tests/fixtures/bin:$PATH" HOME="$state_b/home" XDG_CONFIG_HOME="$state_b/home/.config" ./pixel restore "$backup_a" --identity "$identity_a" --signers "$signers_a" --validate-only >/dev/null 2>&1); then
  echo "Client beta accepted client alpha's private-state path contract" >&2; exit 1
fi

printf '%s\n' '{"status":"pass","environment":"clean-room","clients":2,"gatewayTokens":"distinct","backupTrust":"isolated","crossRestore":"denied"}'
