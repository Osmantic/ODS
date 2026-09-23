#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
ui_pid=''
cleanup() {
  [[ -z "$ui_pid" ]] || kill "$ui_pid" >/dev/null 2>&1 || true
  rm -rf -- "$tmp"
}
trap cleanup EXIT
repo="$tmp/repo"
mkdir -p "$repo"
tar --exclude=.git --exclude=.env --exclude=.generated --exclude=.runtime --exclude=dist --exclude=__pycache__ --exclude='*.pyc' -C "$SOURCE" -cf - . | tar -xf - -C "$repo"
personal='owner''@example.com'
personal_occurrences() {
  local status=0
  grep -R -F -o "$personal" "$repo" --exclude=.env --exclude=deployment.json --exclude='*.pyc' --exclude-dir=__pycache__ || status=$?
  [[ $status == 0 || $status == 1 ]] || return "$status"
}
personal_occurrences_before=$(personal_occurrences | wc -l)
export PATH="$repo/tests/fixtures/bin:$PATH"
export HOME="$tmp/home"
export XDG_CONFIG_HOME="$HOME/.config"
mkdir -p "$HOME"
answers="$tmp/answers.json"
node - "$answers" "$tmp" "$repo" <<'NODE'
const fs=require('fs'); const [out,tmp,repo]=process.argv.slice(2); const account=['owner','example.com'].join('@');
fs.writeFileSync(out, JSON.stringify({deploymentProfile:'prepared',ownerName:'Test Owner',organization:'Test Client',deploymentName:'e2e',timeZone:'America/New_York',agentId:'pixel',agentName:'Pixel',openclawBin:`${repo}/tests/fixtures/bin/openclaw`,openclawHome:`${tmp}/home/.openclaw`,installDir:`${tmp}/home/.local/share/pixel`,workspace:`${tmp}/home/.openclaw/workspace-pixel`,modelProvider:'local',modelId:'test-model',modelName:'Test Model',modelBaseUrl:'http://127.0.0.1:8000/v1',modelApiKey:'preserve-existing',modelContextWindow:8192,modelMaxTokens:1024,searxngBaseUrl:'http://127.0.0.1:8890',embeddingModel:'test.gguf',embeddingCache:`${tmp}/home/.cache/embeddings`,googleAccount:account,calendarId:'primary',gatewayPort:18789,webCourierEnabled:false}));
NODE
cd "$repo"
# shellcheck source=scripts/generated/release.env
source "$repo/scripts/generated/release.env"
pixel_version=$(tr -d '[:space:]' < VERSION)
./pixel configure --answers "$answers"
test -f "$tmp/home/.config/pixel-deployment/onboarding.json"
test "$(stat -c %a "$tmp/home/.config/pixel-deployment/onboarding.json")" = 600
cmp "$answers" "$tmp/home/.config/pixel-deployment/onboarding.json"
grep -Fx "PIXEL_RELEASE_OPERATOR_ENABLED='0'" .env >/dev/null
grep -Fx "PIXEL_RELEASE_OPERATOR_USER='pixel-release-transport'" .env >/dev/null
grep -Fx "PIXEL_RELEASE_OPERATOR_KEY='$tmp/home/.config/pixel-deployment/release-operator.key'" .env >/dev/null
operator_answers="$tmp/operator-answers.json"
node - "$answers" "$operator_answers" "$tmp" <<'NODE'
const fs=require('fs'); const [input,output,tmp]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.releaseOperator={enabled:true,user:'pixel-release-transport',key:`${tmp}/release-operator.key`};
fs.writeFileSync(output,JSON.stringify(answers));
NODE
./pixel configure --answers "$operator_answers" --force
grep -Fx "PIXEL_RELEASE_OPERATOR_ENABLED='1'" .env >/dev/null
grep -Fx "PIXEL_RELEASE_OPERATOR_USER='pixel-release-transport'" .env >/dev/null
grep -Fx "PIXEL_RELEASE_OPERATOR_KEY='$tmp/release-operator.key'" .env >/dev/null
jq -e '.releaseOperator == {enabled:true}' .generated/deployment.json >/dev/null
bad_operator_answers="$tmp/bad-operator-answers.json"
node - "$operator_answers" "$bad_operator_answers" <<'NODE'
const fs=require('fs'); const [input,output]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.releaseOperator.user='root;id';
fs.writeFileSync(output,JSON.stringify(answers));
NODE
if ./pixel configure --answers "$bad_operator_answers" --force >/dev/null 2>&1; then echo "Configure accepted an unsafe release-operator user" >&2; exit 1; fi
node - "$operator_answers" "$bad_operator_answers" <<'NODE'
const fs=require('fs'); const [input,output]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.releaseOperator.key='relative/key';
fs.writeFileSync(output,JSON.stringify(answers));
NODE
if ./pixel configure --answers "$bad_operator_answers" --force >/dev/null 2>&1; then echo "Configure accepted a relative release-operator key" >&2; exit 1; fi
node - "$operator_answers" "$bad_operator_answers" <<'NODE'
const fs=require('fs'); const [input,output]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.releaseOperator=[];
fs.writeFileSync(output,JSON.stringify(answers));
NODE
if ./pixel configure --answers "$bad_operator_answers" --force >/dev/null 2>&1; then echo "Configure accepted a non-object release operator" >&2; exit 1; fi
node - "$operator_answers" "$bad_operator_answers" <<'NODE'
const fs=require('fs'); const [input,output]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.releaseOperator=null;
fs.writeFileSync(output,JSON.stringify(answers));
NODE
if ./pixel configure --answers "$bad_operator_answers" --force >/dev/null 2>&1; then echo "Configure accepted a null release operator" >&2; exit 1; fi
./pixel configure --answers "$answers" --force
python3 - "$repo" "$tmp/home/.config/pixel-deployment/onboarding.json" "$tmp/home/.config/pixel-control" <<'PY'
import importlib.util
import json
from pathlib import Path
import sys

repo, onboarding, control_state = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("pixel_control_e2e", repo / "control/server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
state = module.ControlState(repo, control_state, onboarding)
projection = state.onboarding()
assert projection["settings"]["limbs"]["web"] is False
assert projection["credentialsExposed"] is False
assert "preserve-existing" not in json.dumps(projection)
saved = state.save_onboarding({"schemaVersion": 1, "revision": projection["revision"], "settings": projection["settings"]})
preview = state.preview_action({"schemaVersion": 1, "kind": "configure"})
result = state.execute_action({"schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"]})
assert result["status"] == "succeeded"
private = module.read_json(onboarding, private=True)
assert private["modelApiKey"] == "preserve-existing"
assert private["webCourierEnabled"] is False
assert private["webLimbEnabled"] is False
PY
jq -e '.limbs.web == false' .generated/deployment.json >/dev/null
./pixel ui --state "$tmp/ui-state" --onboarding "$tmp/home/.config/pixel-deployment/onboarding.json" --port 43117 > "$tmp/ui-ready.json" &
ui_pid=$!
for _attempt in $(seq 1 50); do
  [[ -s "$tmp/ui-ready.json" ]] && break
  sleep 0.1
done
python3 - "$tmp/ui-ready.json" <<'PY'
import http.client
import json
from pathlib import Path
import sys

ready = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert ready["url"].startswith("http://127.0.0.1:43117/#review=")
assert len(ready["url"].split("#review=", 1)[1]) == 43
connection = http.client.HTTPConnection("127.0.0.1", 43117, timeout=3)
connection.request("GET", "/", headers={"Host": "127.0.0.1:43117"})
response = connection.getresponse()
assert response.status == 200
cookie = response.getheader("Set-Cookie").split(";", 1)[0]
response.read()
connection.request("GET", "/api/v1/status", headers={"Host": "127.0.0.1:43117", "Cookie": cookie})
response = connection.getresponse()
status = json.loads(response.read())
assert response.status == 200 and status["privacy"]["credentialsExposed"] is False
connection.close()
PY
kill "$ui_pid"
wait "$ui_pid" || true
ui_pid=''
jq -e '.capabilityProfile == "chief-of-staff" and .limbs.email == true and .limbs.calendar == true and .limbs.web == false and .limbs.operations == false and .limbs.frontier == false' .generated/deployment.json >/dev/null
if grep -Eq '^PIXEL_(OPS|FRONTIER)_POLICY_SOURCE=' .env; then echo "Generated environment retained a redundant policy source path" >&2; exit 1; fi
grep -Fx "PIXEL_FRONTIER_CREDENTIAL_SOURCE='/secure/client-config/openai-api-key'" .env >/dev/null
jq -e '.modelReasoning == true' .generated/deployment.json >/dev/null
jq -e '.calendarMutationPolicy.boundedDirectEnabled == true and .calendarMutationPolicy.separateApprovalForConsequentialChanges == true' .generated/deployment.json >/dev/null
grep -Fx "PIXEL_SOURCE_GMAIL_QUERY='in:inbox'" .generated/source-broker.env >/dev/null
grep -Fx "PIXEL_SOURCE_GMAIL_PAGE_SIZE='100'" .generated/source-broker.env >/dev/null
grep -Fx "PIXEL_SOURCE_GMAIL_MAX_PAGES='1000'" .generated/source-broker.env >/dev/null
grep -Fx "PIXEL_SOURCE_GMAIL_SENT_QUERY='in:sent'" .generated/source-broker.env >/dev/null
grep -Fx "PIXEL_SOURCE_GMAIL_SENT_PAGE_SIZE='100'" .generated/source-broker.env >/dev/null
grep -Fx "PIXEL_SOURCE_GMAIL_SENT_MAX_PAGES='1000'" .generated/source-broker.env >/dev/null
grep -Fx "PIXEL_CALENDAR_DIRECT_ENABLED='1'" .generated/source-broker.env >/dev/null
grep -Fx "PIXEL_SOURCE_STALE_AFTER_MS='180000'" .generated/gateway.env >/dev/null
printf "PIXEL_SOURCE_BROKER_STATE_DIR='%s'\nPIXEL_SOURCE_TOKEN_PATH='%s'\nPIXEL_SOURCE_BROKER_ENV='%s'\nPIXEL_OPS_BROKER_STATE_DIR='%s'\nPIXEL_OPS_BROKER_ENV='%s'\nPIXEL_OPS_POLICY_PATH='%s'\n" \
  "$tmp/isolated/source-state" "$tmp/isolated/source-state/private/google-token.json" "$tmp/isolated/source.env" \
  "$tmp/isolated/ops-state" "$tmp/isolated/ops.env" "$tmp/isolated/ops-policy/policy.json" >> .env
backup_dir="$tmp/private-backups"
export PIXEL_BACKUP_AGE_RECIPIENT=age1testrecipient
backup_path=$(python3 - "$repo" "$tmp/home/.config/pixel-deployment/onboarding.json" "$tmp/home/.config/pixel-control" "$backup_dir" <<'PY'
import importlib.util
from pathlib import Path
import sys

repo, onboarding, control_state, backup_dir = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("pixel_control_backup_e2e", repo / "control/server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
policy = module.default_control_policy()
policy["actions"]["backupCreate"] = True
policy["backup"] = {"directory": str(backup_dir), "ageRecipient": "age1testrecipient"}
module.atomic_json(control_state / "policy.json", policy, 0o600)
state = module.ControlState(repo, control_state, onboarding)
preview = state.preview_action({"schemaVersion": 1, "kind": "backup-create"})
assert str(backup_dir) not in str(preview)
assert "age1testrecipient" not in str(preview)
result = state.execute_action({"schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"]})
assert result["status"] == "succeeded"
archives = sorted(backup_dir.glob("pixel-private-*.tar.gz.age"))
assert len(archives) == 1
print(archives[0])
PY
)
[[ "$backup_path" == "$backup_dir/"*.tar.gz.age && -s "$backup_path" && -f "$backup_path.sha256" && -s "$backup_path.sig" ]]
test "$(stat -c %a "$backup_path")" = 600
test "$(stat -c %a "$backup_path.sha256")" = 600
test "$(stat -c %a "$backup_path.sig")" = 600
test -s "$tmp/home/.config/pixel-deployment/backup-signing-key"
test "$(stat -c %a "$tmp/home/.config/pixel-deployment/backup-signing-key")" = 600
test -s "$tmp/home/.config/pixel-deployment/backup-allowed-signers"
head -n 1 "$backup_path" | grep -Fx 'PIXEL-FAKE-AGE' >/dev/null
if find "$backup_dir" -maxdepth 1 -type f -name '*.tar.gz' | grep -q .; then echo "Backup left a plaintext tarball" >&2; exit 1; fi
if env -u PIXEL_BACKUP_AGE_RECIPIENT ./pixel backup "$tmp/backup-without-recipient" >/dev/null 2>&1; then echo "Backup accepted no encryption recipient" >&2; exit 1; fi
if ./pixel backup "$tmp/home/.config/pixel-deployment/nested-backup" >/dev/null 2>&1; then echo "Backup accepted a destination inside captured private state" >&2; exit 1; fi
identity="$tmp/age-identity.txt"
printf '%s\n' age1testrecipient > "$identity"; chmod 600 "$identity"
./pixel restore "$backup_path" --identity "$identity" --validate-only | jq -e --arg version "$pixel_version" '.status == "pass" and .roots > 0 and .pixelVersion == $version' >/dev/null
mkdir -p "$tmp/home/.local/share/pixel/releases/0.8.0"
printf '0.8.0\n' > "$tmp/home/.local/share/pixel/releases/0.8.0/VERSION"
ln -s "$tmp/home/.local/share/pixel/releases/0.8.0" "$tmp/home/.local/share/pixel/current"
legacy_state_backup=$(./pixel backup "$tmp/legacy-state-backup")
./pixel restore "$legacy_state_backup" --identity "$identity" --validate-only | jq -e '.pixelVersion == "0.8.0"' >/dev/null
legacy_rehearsal="$tmp/restore-legacy-rehearsal"
./pixel restore "$legacy_state_backup" --identity "$identity" --rehearse "$legacy_rehearsal" | jq -e '.status == "pass" and .liveStateChanged == false' >/dev/null
rm -f -- "$tmp/home/.local/share/pixel/current"
rehearsal="$tmp/restore-rehearsal"
./pixel restore "$backup_path" --identity "$identity" --rehearse "$rehearsal" | jq -e '.status == "pass" and .liveStateChanged == false' >/dev/null
test -f "$rehearsal/${repo#/}/.env"
test -f "$rehearsal/${tmp#/}/home/.config/pixel-deployment/onboarding.json"
test -f "$rehearsal/${tmp#/}/home/.config/pixel-control/policy.json"
jq -e '.actions.backupCreate == true and .backup.ageRecipient == "age1testrecipient"' \
  "$rehearsal/${tmp#/}/home/.config/pixel-control/policy.json" >/dev/null
wrong_identity="$tmp/wrong-age-identity.txt"
printf '%s\n' wrong-recipient > "$wrong_identity"; chmod 600 "$wrong_identity"
if ./pixel restore "$backup_path" --identity "$wrong_identity" --validate-only >/dev/null 2>&1; then echo "Restore accepted the wrong identity" >&2; exit 1; fi
corrupt="$backup_dir/corrupt.tar.gz.age"
cp "$backup_path" "$corrupt"; cp "$backup_path.sig" "$corrupt.sig"
printf 'x' >> "$corrupt"
sha256sum "$corrupt" > "$corrupt.sha256"
if ./pixel restore "$corrupt" --identity "$identity" --validate-only >/dev/null 2>&1; then echo "Restore accepted a corrupt encrypted backup" >&2; exit 1; fi
unsigned="$backup_dir/unsigned.tar.gz.age"
cp "$backup_path" "$unsigned"; cp "$backup_path.sha256" "$unsigned.sha256"
if ./pixel restore "$unsigned" --identity "$identity" --validate-only >/dev/null 2>&1; then echo "Restore accepted an unsigned encrypted backup" >&2; exit 1; fi
bad_signature="$backup_dir/bad-signature.tar.gz.age"
cp "$backup_path" "$bad_signature"; cp "$backup_path.sha256" "$bad_signature.sha256"; cp "$backup_path.sig" "$bad_signature.sig"
printf 'x' >> "$bad_signature.sig"
if ./pixel restore "$bad_signature" --identity "$identity" --validate-only >/dev/null 2>&1; then echo "Restore accepted an untrusted signature" >&2; exit 1; fi
fake_plugin_base="$tmp/fake-plugins"
plugin_cache="$tmp/home/.local/share/pixel/bootstrap/openclaw-plugins"
mkdir -p "$fake_plugin_base/discord" "$fake_plugin_base/searxng" "$fake_plugin_base/llama-cpp" "$plugin_cache"
discord_version=$(jq -r '.openclawPlugins["@openclaw/discord"]' RELEASE-MANIFEST.json)
searxng_version=$(jq -r '.openclawPlugins["@openclaw/searxng-plugin"]' RELEASE-MANIFEST.json)
llama_version=$(jq -r '.openclawPlugins["@openclaw/llama-cpp-provider"]' RELEASE-MANIFEST.json)
printf '{"name":"@openclaw/discord","version":"%s"}\n' "$discord_version" > "$fake_plugin_base/discord/package.json"
printf '{"name":"@openclaw/searxng-plugin","version":"%s"}\n' "$searxng_version" > "$fake_plugin_base/searxng/package.json"
printf '{"name":"@openclaw/llama-cpp-provider","version":"%s"}\n' "$llama_version" > "$fake_plugin_base/llama-cpp/package.json"
tar -czf "$plugin_cache/discord-$discord_version.tgz" --transform='s,^,package/,' -C "$fake_plugin_base/discord" package.json
tar -czf "$plugin_cache/searxng-$searxng_version.tgz" --transform='s,^,package/,' -C "$fake_plugin_base/searxng" package.json
tar -czf "$plugin_cache/llama-cpp-$llama_version.tgz" --transform='s,^,package/,' -C "$fake_plugin_base/llama-cpp" package.json
discord_fixture_sha=$(sha256sum "$plugin_cache/discord-$discord_version.tgz" | awk '{print $1}')
searxng_fixture_sha=$(sha256sum "$plugin_cache/searxng-$searxng_version.tgz" | awk '{print $1}')
llama_fixture_sha=$(sha256sum "$plugin_cache/llama-cpp-$llama_version.tgz" | awk '{print $1}')
node - "$discord_fixture_sha" "$searxng_fixture_sha" "$llama_fixture_sha" <<'NODE'
const fs = require('fs'); const [discord, searxng, llama] = process.argv.slice(2);
const manifest = JSON.parse(fs.readFileSync('RELEASE-MANIFEST.json', 'utf8'));
manifest.openclawPluginPackages.discord.sha256 = discord;
manifest.openclawPluginPackages.searxng.sha256 = searxng;
manifest.openclawPluginPackages.llamaCpp.sha256 = llama;
fs.writeFileSync('RELEASE-MANIFEST.json', `${JSON.stringify(manifest, null, 2)}\n`);
NODE
printf "PIXEL_FAKE_PLUGIN_BASE='%s'\n" "$fake_plugin_base" >> .env
printf "PIXEL_FAKE_CONFIG_STATE_LOG='%s'\n" "$tmp/config-state.log" >> .env
if PIXEL_FAKE_SANDBOX_VERSION=wrong bash scripts/preflight.sh --phase plan --skip-endpoints >/dev/null 2>&1; then echo "Preflight accepted a stale sandbox image" >&2; exit 1; fi
if PIXEL_FAKE_SANDBOX_USER=root bash scripts/preflight.sh --phase plan --skip-endpoints >/dev/null 2>&1; then echo "Preflight accepted a root sandbox image" >&2; exit 1; fi
if PIXEL_FAKE_SANDBOX_UID=0 bash scripts/preflight.sh --phase plan --skip-endpoints >/dev/null 2>&1; then echo "Preflight accepted a sandbox image for another host owner" >&2; exit 1; fi
cp .env "$tmp/current.env"
sed -i "s/^PIXEL_RELEASE_VERSION=.*/PIXEL_RELEASE_VERSION='0.0.0'/" .env
if ./pixel plan >/dev/null 2>&1; then echo "Plan accepted a stale generated release version" >&2; exit 1; fi
mv "$tmp/current.env" .env
legacy_config="$tmp/legacy-openclaw.json"
printf '%s\n' '{"agents":{"list":[{"id":"main","tools":{"deny":["pixel_calendar_create"]}},{"id":"pixel"}]},"tools":{"sandbox":{"tools":{"allow":["exec","pixel_calendar_create"]}}},"plugins":{"entries":{"pixel-google-workspace":{"enabled":true},"pixel-gmail-readonly":{"enabled":true}},"allow":["pixel-google-workspace","pixel-gmail-readonly"],"load":{"paths":["/legacy/plugin"]}}}' > "$legacy_config"
node scripts/migrate-source-broker-config.mjs "$legacy_config" /new/plugin /legacy/plugin >/dev/null
jq -e '.plugins.entries["pixel-source-broker"].enabled == true and (.plugins.entries["pixel-google-workspace"] == null) and (.plugins.entries["pixel-gmail-readonly"] == null) and (.plugins.allow == ["pixel-source-broker"]) and (.plugins.load.paths == ["/new/plugin"])' "$legacy_config" >/dev/null
jq -e '(.tools.sandbox.tools.allow | contains(["pixel_limb_status","pixel_gmail_sent","pixel_calendar_propose_create","pixel_social_feed"])) and (.tools.sandbox.tools.allow | contains(["pixel_calendar_create"]) | not) and (.agents.list[0].tools.deny | contains(["pixel_limb_status","pixel_gmail_sent","pixel_calendar_propose_create","pixel_social_feed"]))' "$legacy_config" >/dev/null
legacy_ops_policy="$tmp/legacy-ops-policy.json"
migrated_ops_policy="$tmp/migrated-ops-policy.json"
legacy_ops_onboarding="$tmp/legacy-ops-onboarding.json"
printf '%s\n' '{"schemaVersion":1,"autoTiers":["read"],"targets":{"host":{"backend":"local","expectedHostname":"host","allowedRoots":["/srv/pixel"]}},"actions":{"host.identity":{"description":"identity","tier":"read","targets":["host"],"argv":["/bin/hostname"]}}}' > "$legacy_ops_policy"
printf '{"operationsPolicyFile":"%s"}\n' "$legacy_ops_policy" > "$legacy_ops_onboarding"
./pixel ops-policy-migrate "$legacy_ops_policy" "$migrated_ops_policy" --environment host=production --update-onboarding "$legacy_ops_onboarding" --confirm >/dev/null
jq -e '.schemaVersion == 2 and .migratedFromSchemaVersion == 1 and .targets.host.environment == "production" and .authority.grants[0].id == "legacy-auto-read"' "$migrated_ops_policy" >/dev/null
jq -e --arg policy "$migrated_ops_policy" '.operationsPolicyFile == $policy' "$legacy_ops_onboarding" >/dev/null
test "$(find "$tmp" -maxdepth 1 -name 'legacy-ops-onboarding.json.before-ops-v2-*' | wc -l)" = 1
./pixel ops-policy-tighten "$migrated_ops_policy" --confirm >/dev/null
jq -e '(.authority.grants | length) == 0 and (.authority.migrationNotice == null) and (.compatibilityGrantsRemovedAt | type == "string")' "$migrated_ops_policy" >/dev/null
test "$(find "$tmp" -maxdepth 1 -name 'migrated-ops-policy.json.before-tighten-*' | wc -l)" = 1
if ./pixel ops-policy-tighten "$migrated_ops_policy" --confirm >/dev/null 2>&1; then echo "Policy tightening unexpectedly repeated" >&2; exit 1; fi
if ./pixel ops-policy-migrate "$legacy_ops_policy" "$migrated_ops_policy" --confirm >/dev/null 2>&1; then echo "Policy migration overwrote existing output" >&2; exit 1; fi
if ./pixel ops-policy-migrate "$legacy_ops_policy" "$tmp/bad-migrated.json" --environment host=invalid --confirm >/dev/null 2>&1; then echo "Policy migration accepted an invalid environment" >&2; exit 1; fi
legacy_workspace="$tmp/legacy-workspace"
mkdir -p "$legacy_workspace"
printf '%s\n' '# Agent' '## Calendar: writing to it' 'You hold pixel_calendar_create and pixel_calendar_update write tools.' '## Replying' 'Reply.' > "$legacy_workspace/AGENTS.md"
printf '%s\n' '# Tools' "## Michael's Gmail — read-only" 'Old Gmail notes.' "## Michael's Google Calendar" 'pixel_calendar_create changes the real calendar.' "## Michael's X feed" 'Run xfeed.sh directly.' '## Portal files' 'Keep this.' > "$legacy_workspace/TOOLS.md"
node scripts/migrate-workspace-source-boundary.mjs "$legacy_workspace" >/dev/null
node scripts/migrate-workspace-source-boundary.mjs "$legacy_workspace" >/dev/null
grep -F 'pixel_calendar_propose_update' "$legacy_workspace/AGENTS.md" >/dev/null
grep -F 'pixel_limb_status' "$legacy_workspace/AGENTS.md" >/dev/null
test "$(grep -c '^## Tool availability$' "$legacy_workspace/AGENTS.md")" = 1
grep -F 'pixel_calendar_propose_create' "$legacy_workspace/TOOLS.md" >/dev/null
grep -F 'pixel_gmail_sent' "$legacy_workspace/TOOLS.md" >/dev/null
grep -F 'pixel_social_feed' "$legacy_workspace/TOOLS.md" >/dev/null
grep -F 'never capability discovery for another limb' "$legacy_workspace/TOOLS.md" >/dev/null
grep -F "Never use generic \`exec\`, \`process\`" "$legacy_workspace/AGENTS.md" >/dev/null
grep -F "Generic \`exec\`, \`process\`, shell" "$legacy_workspace/TOOLS.md" >/dev/null
if grep -E 'pixel_calendar_(create|update|delete)|Run xfeed\.sh' "$legacy_workspace/AGENTS.md" "$legacy_workspace/TOOLS.md"; then echo "Legacy direct-source workspace guidance survived migration" >&2; exit 1; fi
grep -F 'NoNewPrivileges=true' .generated/pixel-web-courier.service >/dev/null
grep -F 'User=' .generated/openclaw-gateway.service >/dev/null
grep -F 'ExecStart=' .generated/openclaw-gateway.service | grep -F -- '--bind loopback --auth token' >/dev/null
grep -F 'NoNewPrivileges=true' .generated/openclaw-gateway.service >/dev/null
grep -F "ReadOnlyPaths=$tmp/home/.openclaw/npm" .generated/openclaw-gateway.service >/dev/null
grep -F 'PrivateDevices=true' .generated/openclaw-gateway.service >/dev/null
grep -F 'ProtectHome=tmpfs' .generated/openclaw-gateway.service >/dev/null
grep -F 'ProtectSystem=strict' .generated/openclaw-gateway.service >/dev/null
grep -F 'CapabilityBoundingSet=' .generated/openclaw-gateway.service >/dev/null
grep -F 'User=pixel-source-broker' .generated/pixel-source-broker.service >/dev/null
grep -F 'ProtectHome=true' .generated/pixel-source-broker.service >/dev/null
grep -F 'ExecStart="/opt/pixel-source-broker/broker.py" --reconcile %i' .generated/pixel-source-reconcile@.service >/dev/null
grep -F 'ReadWritePaths=/var/lib/pixel-source-broker/results' .generated/pixel-source-reconcile@.service >/dev/null
grep -F 'ExecStart="/opt/pixel-source-broker/broker.py" --drain-direct' .generated/pixel-source-direct.service >/dev/null
grep -F 'ReadOnlyPaths=/opt/pixel-source-broker /var/lib/pixel-source-broker/private /var/lib/pixel-source-broker/proposals' .generated/pixel-source-direct.service >/dev/null
grep -F 'PathChanged=/var/lib/pixel-source-broker/proposals' .generated/pixel-source-direct.path >/dev/null
grep -F 'NoNewPrivileges=true' .generated/openclaw-gateway.service >/dev/null
grep -F 'OnUnitActiveSec=1min' .generated/pixel-source-broker.timer >/dev/null
grep -F 'User=pixel-ops-broker' .generated/pixel-ops-broker.service >/dev/null
grep -F 'ProtectSystem=strict' .generated/pixel-ops-broker.service >/dev/null
grep -F "PIXEL_LIMB_OPERATIONS_ENABLED='0'" .generated/gateway.env >/dev/null
export PIXEL_FAKE_SYSTEMCTL_LOG="$tmp/fake-systemctl.log"
grep -F 'PIXEL_SOURCE_PROJECTION_DIR=' .generated/gateway.env >/dev/null
if grep -F 'PIXEL_GOOGLE_TOKEN_PATH=' .generated/gateway.env; then echo "Gateway retained direct Google credential access" >&2; exit 1; fi
grep -F 'ProtectHome=tmpfs' .generated/pixel-web-courier.service >/dev/null
grep -F "BindPaths=$tmp/home/.openclaw/workspace-pixel $tmp/home/.openclaw/logs" .generated/pixel-web-courier.service >/dev/null
grep -F "PIXEL_WEB_COURIER_WORKSPACES='$tmp/home/.openclaw/workspace-pixel'" .generated/web-courier.env >/dev/null
printf "PIXEL_SKIP_ENDPOINT_CHECKS='1'\nPIXEL_SKIP_NPM_CI='1'\nPIXEL_SOURCE_BROKER_ENABLED='0'\nPIXEL_SYSTEMCTL_BIN='systemctl'\nPIXEL_LEGACY_SYSTEMCTL_BIN='systemctl'\nPIXEL_GATEWAY_SYSTEMD_DIR='%s'\nPIXEL_COURIER_SYSTEMCTL_BIN='systemctl'\nPIXEL_COURIER_SYSTEMD_DIR='%s'\n" "$tmp/home/.config/systemd/system" "$tmp/home/.config/systemd/system" >> .env
printf "PIXEL_FAKE_PLUGIN_REGISTRY_LOG='%s'\n" "$tmp/plugin-registry.log" >> .env
mkdir -p "$tmp/home/.openclaw" "$tmp/home/.local/share/pixel/releases/0.9.0"
mkdir -p "$tmp/home/.openclaw/workspace-pixel"
mkdir -p "$tmp/home/.config/systemd/user" "$tmp/home/.config/systemd/system"
printf '%s\n' '[Service]' 'ExecStart=/bin/true' > "$tmp/home/.config/systemd/user/openclaw-gateway.service"
printf '%s\n' 'owner-controlled' > "$tmp/home/.openclaw/workspace-pixel/private-existing.txt"
chmod 640 "$tmp/home/.openclaw/workspace-pixel/private-existing.txt"
mkdir -p "$tmp/old-source-plugin"
printf '%s\n' '{"id":"pixel-source-broker"}' > "$tmp/old-source-plugin/openclaw.plugin.json"
printf '{"hooks":{"enabled":true},"cron":{"enabled":true},"browser":{"executablePath":"/tmp/ambient-browser"},"bindings":[{"agentId":"main"}],"gateway":{"bind":"lan","auth":{"token":"fixture-gateway-token"},"tailscale":{"mode":"funnel"},"http":{"endpoints":{"chatCompletions":{"enabled":true},"ambient":{"enabled":true}},"dangerousFutureOption":true}},"session":{"dmScope":"main","ambientVisibility":"all"},"messages":{"suppressToolErrors":true,"ambientCommand":true},"models":{"providers":{"local":{"apiKey":"local-no-auth","timeoutSeconds":77,"headers":{"X-Ambient":"secret"}},"unused":{"apiKey":"unused-secret"}}},"agents":{"defaults":{"workspace":"/home","hooks":{"enabled":true},"compaction":{"mode":"ambient"},"sandbox":{"mode":"off","docker":{"binds":["/:/host"]}},"memorySearch":{"extraPaths":["/home"]}},"list":[{"id":"main"},{"id":"pixel","sandbox":{"mode":"off"},"tools":{"allow":["sessions_history"]}}]},"tools":{"exec":{"host":"gateway"},"fs":{"workspaceOnly":false},"sandbox":{"tools":{"allow":["sessions_history","message"]}}},"plugins":{"allow":["evil"],"entries":{"evil":{"enabled":true}},"load":{"paths":["%s"]}},"channels":{"evil":{"enabled":true}}}\n' "$tmp/old-source-plugin" > "$tmp/original-openclaw.json"
cp "$tmp/original-openclaw.json" "$tmp/home/.openclaw/openclaw.json"
printf '0.9.0\n' > "$tmp/home/.local/share/pixel/releases/0.9.0/VERSION"
ln -s releases/0.9.0 "$tmp/home/.local/share/pixel/current"
export PIXEL_FAKE_DOCKER_LOG="$tmp/fake-docker.log"
export PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR="$tmp/fake-docker-images"
mkdir -p "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR"
previous_sandbox_image_id=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
candidate_sandbox_image_id=sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
printf '%s\n%s\n%s\n%s\n' 0.9.0 "$(id -u)" sandbox "$previous_sandbox_image_id" > "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/shared.state"
bash -c 'source scripts/lib/common.sh; pixel_load_env; pixel_preserve_sandbox_image "$PIXEL_SANDBOX_IMAGE" 0.9.0 "$(id -u)"' >/dev/null
grep -F "image tag $previous_sandbox_image_id pixel-sandbox-preserve:0.9.0-uid-$(id -u)" "$PIXEL_FAKE_DOCKER_LOG" >/dev/null
candidate_sandbox_ref="pixel-sandbox-candidate:$pixel_version-uid-$(id -u)"
docker build --build-arg "PIXEL_SANDBOX_UID=$(id -u)" -t "$candidate_sandbox_ref" .
test "$(sed -n '1p' "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/shared.state")" = 0.9.0
test "$(sed -n '1p' "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/candidate.state")" = "$pixel_version"
if bash -c 'source scripts/lib/common.sh; pixel_load_env; pixel_remove_sandbox_tag_exact "$PIXEL_SANDBOX_IMAGE" 0.9.0 "$(id -u)" sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' >/dev/null 2>&1; then
  echo "Exact sandbox-tag removal accepted the wrong image ID" >&2
  exit 1
fi
test "$(sed -n '4p' "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/shared.state")" = "$previous_sandbox_image_id"

# A reviewed plan is later rebuilt from a distinct absolute reactivation source root.
# Exercise the real planner in both locations and require every plan artifact to remain
# byte-identical. The checksum manifest must name its member relatively so a retained
# release and a fresh reactivation stage can have the same canonical tree.
location_peer="$tmp/repo-location-peer"
mkdir -p "$location_peer"
tar --exclude=.git --exclude=.runtime --exclude=dist --exclude=node_modules \
  --exclude=__pycache__ --exclude='*.pyc' -C "$repo" -cf - . | tar -xf - -C "$location_peer"
./pixel plan
(cd "$location_peer" && ./pixel plan >/dev/null)
diff -ru "$repo/dist" "$location_peer/dist"
grep -Eq '^[a-f0-9]{64}  openclaw\.json$' "$repo/dist/openclaw.sha256"
if grep -F "$repo" "$repo/dist/openclaw.sha256" >/dev/null || \
   grep -F "$location_peer" "$location_peer/dist/openclaw.sha256" >/dev/null; then
  echo "Reviewed candidate checksum retained an absolute source path" >&2
  exit 1
fi
printf '\n' >> "$location_peer/dist/openclaw.json"
if (cd "$location_peer/dist" && sha256sum -c openclaw.sha256) >/dev/null 2>&1; then
  echo "Reviewed candidate checksum accepted tampered bytes" >&2
  exit 1
fi
validation_state=$(head -n 1 "$tmp/config-state.log")
[[ "$validation_state" == /tmp/pixel-openclaw-validation.* ]]
test ! -e "$validation_state"
# A mismatched shared tag without an active release remains foreign state and
# must fail before release, backup, service, or symlink mutation.
rm -f -- "$tmp/home/.local/share/pixel/current"
: > "$PIXEL_FAKE_SYSTEMCTL_LOG"
unbound_mismatch_output="$tmp/unbound-mismatch-apply.out"
if ./pixel apply --confirm >"$unbound_mismatch_output" 2>&1; then
  echo "Apply accepted a mismatched shared sandbox tag without an active release" >&2
  exit 1
fi
grep -F "Shared live sandbox tag exists without an active Pixel release and is not valid for the reviewed candidate" \
  "$unbound_mismatch_output" >/dev/null
test ! -e "$tmp/home/.local/share/pixel/releases/$pixel_version"
test -z "$(grep -E '^(enable|disable|start|stop|restart) ' "$PIXEL_FAKE_SYSTEMCTL_LOG" || true)"

# An interrupted first activation can leave the shared tag pointing to the
# exact, already validated candidate while `current` is absent.  Prove apply
# passes that guard and reaches later verification; compensation must then
# remove only the exact shared tag and retain the candidate.
docker image tag "$candidate_sandbox_image_id" "$PIXEL_GENERATED_SANDBOX_IMAGE"
: > "$PIXEL_FAKE_SYSTEMCTL_LOG"
unbound_exact_output="$tmp/unbound-exact-apply.out"
if PIXEL_FAKE_SYSTEMCTL_FAIL_IS_ACTIVE=1 ./pixel apply --confirm >"$unbound_exact_output" 2>&1; then
  echo "Apply unexpectedly survived the forced post-activation verification failure" >&2
  exit 1
fi
grep -F "Recovering an exact candidate sandbox tag left by an interrupted first activation" \
  "$unbound_exact_output" >/dev/null
test ! -e "$tmp/home/.local/share/pixel/current"
test ! -e "$tmp/home/.local/share/pixel/releases/$pixel_version"
test ! -e "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/shared.state"
test "$(sed -n '4p' "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/candidate.state")" = "$candidate_sandbox_image_id"

# Restore the exact prior fixture before the remaining upgrade/rollback matrix.
docker image tag "$previous_sandbox_image_id" "$PIXEL_GENERATED_SANDBOX_IMAGE"
ln -s releases/0.9.0 "$tmp/home/.local/share/pixel/current"
# Pixel 4.3.8 is a blocked release whose trusted rollback controller requires
# the expanded helper-file manifest. A 4.3.9 apply from that exact prestate must
# fail before creating the release tree, backup, or rollback marker. The plan is
# already present here so the asserted failure is the prestate guard itself.
rm -f -- "$tmp/home/.local/share/pixel/current"
mkdir -p "$tmp/home/.local/share/pixel/releases/4.3.8"
printf '4.3.8\n' > "$tmp/home/.local/share/pixel/releases/4.3.8/VERSION"
ln -s releases/4.3.8 "$tmp/home/.local/share/pixel/current"
blocked_apply_output="$tmp/blocked-4.3.8-apply.out"
if ./pixel apply --confirm >"$blocked_apply_output" 2>&1; then
  echo "Apply unexpectedly accepted the rollback-incompatible Pixel 4.3.8 prestate" >&2
  exit 1
fi
grep -F "Pixel 4.3.8 has a rollback-incompatible broker manifest" "$blocked_apply_output" >/dev/null
test "$(readlink "$tmp/home/.local/share/pixel/current")" = releases/4.3.8
test ! -e "$tmp/home/.local/share/pixel/releases/$pixel_version"
test ! -e "$tmp/home/.openclaw/backups/last-apply"
rm -f -- "$tmp/home/.local/share/pixel/current"
rm -rf -- "$tmp/home/.local/share/pixel/releases/4.3.8"
ln -s releases/0.9.0 "$tmp/home/.local/share/pixel/current"
unsafe_apply_attestation="$tmp/home/.local/share/pixel/runtime-attestation.json"
mkdir "$unsafe_apply_attestation"
: > "$PIXEL_FAKE_SYSTEMCTL_LOG"
unsafe_apply_output="$tmp/unsafe-attestation-apply.out"
if ./pixel apply --confirm >"$unsafe_apply_output" 2>&1; then
  echo "Apply unexpectedly survived an unsafe attestation during compensation" >&2
  exit 1
fi
test "$(grep -Fc 'Runtime attestation path is an unsafe directory' "$unsafe_apply_output")" = 2
test -d "$unsafe_apply_attestation"
test "$(readlink "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/0.9.0"
test ! -e "$tmp/home/.local/share/pixel/releases/$pixel_version"
cmp "$tmp/original-openclaw.json" "$tmp/home/.openclaw/openclaw.json"
test -f "$tmp/home/.config/systemd/user/openclaw-gateway.service"
test ! -e "$tmp/home/.config/systemd/system/openclaw-gateway.service"
test "$(sed -n '4p' "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/shared.state")" = "$previous_sandbox_image_id"
last_gateway_mutation=$(grep -E '^(enable|disable|start|stop|restart) (.* )?openclaw-gateway\.service$' "$PIXEL_FAKE_SYSTEMCTL_LOG" | tail -n 1)
last_courier_mutation=$(grep -E '^(enable|disable|start|stop|restart) (.* )?pixel-web-courier\.service$' "$PIXEL_FAKE_SYSTEMCTL_LOG" | tail -n 1)
test "$last_gateway_mutation" = 'stop openclaw-gateway.service'
test "$last_courier_mutation" = 'stop pixel-web-courier.service'
rmdir "$unsafe_apply_attestation"
tampered_sandbox_image_id=sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
if PIXEL_FAKE_DOCKER_TAMPER_AFTER_TAG_SOURCE="$candidate_sandbox_image_id" \
   PIXEL_FAKE_DOCKER_TAMPER_AFTER_TAG_ID="$tampered_sandbox_image_id" \
   ./pixel apply --confirm; then
  echo "Apply unexpectedly survived a shared-tag race after activation" >&2
  exit 1
fi
test "$(readlink "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/0.9.0"
test ! -e "$tmp/home/.local/share/pixel/releases/$pixel_version"
test "$(sed -n '4p' "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/shared.state")" = "$previous_sandbox_image_id"
test "$(sed -n '4p' "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/candidate.state")" = "$candidate_sandbox_image_id"
export PIXEL_FAKE_SYSTEMCTL_FAIL_IS_ACTIVE=1
apply_failure_tmp="$tmp/apply-failure-tmp"
mkdir -m 700 "$apply_failure_tmp"
if TMPDIR="$apply_failure_tmp" ./pixel apply --confirm; then echo "Apply unexpectedly survived a failed verification" >&2; exit 1; fi
unset PIXEL_FAKE_SYSTEMCTL_FAIL_IS_ACTIVE
test -z "$(find "$apply_failure_tmp" -mindepth 1 -print -quit)"
test "$(readlink "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/0.9.0"
test ! -e "$tmp/home/.local/share/pixel/releases/$pixel_version"
cmp "$tmp/original-openclaw.json" "$tmp/home/.openclaw/openclaw.json"
test -f "$tmp/home/.config/systemd/user/openclaw-gateway.service"
test ! -e "$tmp/home/.config/systemd/system/openclaw-gateway.service"
test "$(sed -n '1p' "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/shared.state")" = 0.9.0
if PIXEL_FAKE_SYSTEMCTL_FAIL_IS_ACTIVE=1 PIXEL_FAKE_DOCKER_FAIL_TAG_SOURCE="$previous_sandbox_image_id" ./pixel apply --confirm; then echo "Apply unexpectedly survived failed verification and failed sandbox compensation" >&2; exit 1; fi
test "$(readlink "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/0.9.0"
test ! -e "$tmp/home/.local/share/pixel/releases/$pixel_version"
cmp "$tmp/original-openclaw.json" "$tmp/home/.openclaw/openclaw.json"
test "$(sed -n '1p' "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/shared.state")" = "$pixel_version"
docker image tag "$previous_sandbox_image_id" "$PIXEL_GENERATED_SANDBOX_IMAGE"
test "$(sed -n '1p' "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/shared.state")" = 0.9.0
export PIXEL_FAKE_DOCKER_STATE="$tmp/fake-docker-sandbox.state"
printf '%s\n' afe79ff2ad96 > "$PIXEL_FAKE_DOCKER_STATE"
marker_mutations_before=$(grep -Ec '^(enable|disable|start|stop|restart) ' "$PIXEL_FAKE_SYSTEMCTL_LOG" || true)
unsafe_apply_marker_dir="$tmp/reactivation-attempt-unsafe"
mkdir -m 755 "$unsafe_apply_marker_dir"
if PIXEL_RELEASE_UPDATE_LIVE_MUTATION_MARKER="$unsafe_apply_marker_dir/LIVE-MUTATION-STARTED" \
   ./pixel apply --confirm; then
  echo "Apply accepted an unsafe live-mutation marker directory" >&2
  exit 1
fi
test ! -e "$unsafe_apply_marker_dir/LIVE-MUTATION-STARTED"
test "$(readlink "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/0.9.0"
test ! -e "$tmp/home/.local/share/pixel/releases/$pixel_version"
test "$marker_mutations_before" = "$(grep -Ec '^(enable|disable|start|stop|restart) ' "$PIXEL_FAKE_SYSTEMCTL_LOG" || true)"
collision_apply_marker_dir="$tmp/reactivation-attempt-collision"
mkdir -m 700 "$collision_apply_marker_dir"
printf '%s\n' 'pre-existing-marker-must-survive' > "$collision_apply_marker_dir/LIVE-MUTATION-STARTED"
chmod 600 "$collision_apply_marker_dir/LIVE-MUTATION-STARTED"
if PIXEL_RELEASE_UPDATE_LIVE_MUTATION_MARKER="$collision_apply_marker_dir/LIVE-MUTATION-STARTED" \
   ./pixel apply --confirm; then
  echo "Apply overwrote a pre-existing live-mutation marker" >&2
  exit 1
fi
test "$(cat "$collision_apply_marker_dir/LIVE-MUTATION-STARTED")" = 'pre-existing-marker-must-survive'
test "$(readlink "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/0.9.0"
test ! -e "$tmp/home/.local/share/pixel/releases/$pixel_version"
test "$marker_mutations_before" = "$(grep -Ec '^(enable|disable|start|stop|restart) ' "$PIXEL_FAKE_SYSTEMCTL_LOG" || true)"
apply_marker_dir="$tmp/reactivation-attempt"
mkdir -m 700 "$apply_marker_dir"
PIXEL_RELEASE_UPDATE_LIVE_MUTATION_MARKER="$apply_marker_dir/LIVE-MUTATION-STARTED" \
  ./pixel apply --confirm
test "$(cat "$apply_marker_dir/LIVE-MUTATION-STARTED")" = 'pixel-release-live-mutation-started-v1'
test "$(stat -c '%a' "$apply_marker_dir/LIVE-MUTATION-STARTED")" = 600
test ! -e "$PIXEL_FAKE_DOCKER_STATE"
apply_backup=$(cat "$tmp/home/.openclaw/backups/last-apply")
cp -p .env "$tmp/exact-release-plan.env"
test "$(cat "$apply_backup/previous-release")" = "$tmp/home/.local/share/pixel/releases/0.9.0"
test "$(cat "$apply_backup/previous-sandbox-version")" = 0.9.0
test "$(cat "$apply_backup/previous-sandbox-image-id")" = "$previous_sandbox_image_id"
test -f "$tmp/home/.local/share/pixel/releases/$pixel_version/plugin/email-query.js"
test -f "$tmp/home/.local/share/pixel/releases/$pixel_version/plugin/web-courier.js"
test -f "$tmp/home/.local/share/pixel/releases/$pixel_version/plugin/web-tool.js"
grep -F 'rm -f -- afe79ff2ad96' "$PIXEL_FAKE_DOCKER_LOG" >/dev/null
test "$(grep -c '^refresh$' "$tmp/plugin-registry.log")" -ge 1
./pixel verify
active_release="$tmp/home/.local/share/pixel/releases/$pixel_version"
chmod 602 "$active_release/VERSION"
if ./pixel verify >/dev/null 2>&1; then
  echo "Verify accepted unsafe installed-release mode drift" >&2
  exit 1
fi
chmod 600 "$active_release/VERSION"
ln -sfn "$tmp/home/.local/share/pixel/releases/0.9.0" "$tmp/home/.local/share/pixel/current"
if ./pixel verify >/dev/null 2>&1; then
  echo "Verify accepted an active-release pointer inconsistent with configuration" >&2
  exit 1
fi
ln -sfn "$active_release" "$tmp/home/.local/share/pixel/current"
verify_identity_tmpdir="$tmp/verify-identity-tmp"
mkdir -m 700 "$verify_identity_tmpdir"
cp "$repo/RELEASE-MANIFEST.json" "$tmp/release-manifest.saved"
printf '\n' >> "$repo/RELEASE-MANIFEST.json"
if TMPDIR="$verify_identity_tmpdir" ./pixel verify >/dev/null 2>&1; then
  echo "Verify accepted a changed source release identity" >&2
  exit 1
fi
test -z "$(find "$verify_identity_tmpdir" -mindepth 1 -print -quit)"
mv "$tmp/release-manifest.saved" "$repo/RELEASE-MANIFEST.json"
./pixel verify
runtime_attestation="$tmp/home/.local/share/pixel/runtime-attestation.json"
test -f "$runtime_attestation"
test "$(stat -c %a "$runtime_attestation")" = 600
jq -e '
  .schemaVersion == 1 and .kind == "pixel-runtime-attestation" and .status == "limited" and
  .source == {"state":"unavailable","commit":null,"tree":null} and
  .qualification.relationship == "unavailable" and
  .runtime.state == "gateway-verified-model-unproven" and .runtime.endpointChecks == "skipped" and
  (.runtime.modelIdSha256 | test("^[a-f0-9]{64}$")) and
  ([.connectors[].id] | sort) == (["calendar","email","frontier","operations","social","web"] | sort)
' "$runtime_attestation" >/dev/null
if grep -F 'test-model' "$runtime_attestation"; then echo "Runtime attestation exposed the private model ID" >&2; exit 1; fi
test -L "$tmp/home/.local/share/pixel/current"
test -f "$tmp/home/.local/share/pixel/current/plugin-ops/publish-json.js"
test -f "$tmp/home/.local/share/pixel/current/plugin-ops/secure-read.js"
test -f "$tmp/home/.local/share/pixel/current/plugin/chat-audit.js"
test -f "$tmp/home/.local/share/pixel/current/plugin/oauth-security.mjs"
test -f "$tmp/home/.local/share/pixel/current/plugin-ops/chat-audit.js"
test -f "$tmp/home/.local/share/pixel/current/plugin-frontier/chat-audit.js"
test -f "$tmp/home/.local/share/pixel/current/plugin-frontier/secure-read.js"
test -f "$tmp/home/.local/share/pixel/current/plugin-frontier/local-finalize.js"
test -f "$tmp/home/.openclaw/openclaw.json"
test "$(stat -c %a "$tmp/home/.openclaw/workspace-pixel/private-existing.txt")" = 640
jq -e '.models.providers.local.apiKey == "local-no-auth"' "$tmp/home/.openclaw/openclaw.json" >/dev/null
jq -e '.models.providers.local.timeoutSeconds == 77' "$tmp/home/.openclaw/openclaw.json" >/dev/null
result_keys=$(jq -r 'keys | sort | join(",")' "$tmp/home/.openclaw/openclaw.json")
test "$result_keys" = 'agents,channels,gateway,messages,models,plugins,session,tools'
jq -e '(.hooks == null) and (.cron == null) and (.browser == null) and (.bindings == null) and (.agents.defaults.workspace == null) and (.agents.defaults.hooks == null) and (.agents.defaults.compaction == null) and (.models.providers.local.headers == null) and (.messages == {"suppressToolErrors":true}) and (.session == {"dmScope":"per-account-channel-peer"}) and (.gateway.http == {"endpoints":{"chatCompletions":{"enabled":true}}})' "$tmp/home/.openclaw/openclaw.json" >/dev/null
jq -e '(.models.providers.unused == null) and (.agents.list | length == 1) and .agents.list[0].id == "pixel" and .agents.list[0].skills == []' "$tmp/home/.openclaw/openclaw.json" >/dev/null
jq -e '.gateway.mode == "local" and .gateway.bind == "loopback" and (.gateway.auth.token | test("^[0-9a-f]{64}$")) and (.gateway.tailscale == null)' "$tmp/home/.openclaw/openclaw.json" >/dev/null
jq -e '.session.dmScope == "per-account-channel-peer" and .tools.fs.workspaceOnly == true' "$tmp/home/.openclaw/openclaw.json" >/dev/null
jq -e '.agents.defaults.sandbox.mode == "all" and .agents.defaults.sandbox.scope == "agent" and .agents.defaults.sandbox.docker.network == "none" and .agents.defaults.sandbox.docker.user == "sandbox" and .agents.defaults.sandbox.docker.capDrop == ["ALL"] and (.agents.defaults.sandbox.docker.binds == null)' "$tmp/home/.openclaw/openclaw.json" >/dev/null
jq -e '(.agents.defaults.memorySearch.extraPaths == ["daily-notes","context","library","reflections"]) and (.tools.exec == null) and (.channels == {})' "$tmp/home/.openclaw/openclaw.json" >/dev/null
jq -e '(.agents.list[0].tools.deny | contains(["discord","message","pixel_web_browse"])) and (.agents.list[0].tools.deny | contains(["sessions_list","sessions_history","sessions_send"]) | not) and (.tools.sandbox.tools.allow | contains(["sessions_list","sessions_history","sessions_send"])) and (.tools.sandbox.tools.allow | contains(["pixel_web_browse"]) | not) and (.tools.alsoAllow | contains(["pixel_limb_status","pixel_gmail_inbox","pixel_calendar_list"])) and (.tools.alsoAllow | contains(["pixel_web_browse"]) | not) and .tools.sessions.visibility == "tree" and (.tools.sandbox.tools.allow | contains(["discord","message"]) | not)' "$tmp/home/.openclaw/openclaw.json" >/dev/null
(
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  PIXEL_LIMB_EMAIL_ENABLED=0 PIXEL_LIMB_CALENDAR_ENABLED=0 PIXEL_LIMB_SOCIAL_ENABLED=0 PIXEL_LIMB_WEB_ENABLED=1 \
    PIXEL_LIMB_OPERATIONS_ENABLED=0 PIXEL_LIMB_FRONTIER_ENABLED=0 \
    PIXEL_PLUGIN_PATH="$repo/plugin" PIXEL_OPS_PLUGIN_PATH="$repo/plugin-ops" PIXEL_FRONTIER_PLUGIN_PATH="$repo/plugin-frontier" \
    node scripts/render-config.mjs "$tmp/web-only-openclaw.json" >/dev/null
)
jq -e --arg path "$repo/plugin" '
  (.plugins.allow | contains(["pixel-source-broker"])) and
  .plugins.entries["pixel-source-broker"].enabled == true and
  (.plugins.load.paths | index($path) != null) and
  (.tools.alsoAllow | contains(["pixel_limb_status","pixel_web_browse"])) and
  (.tools.sandbox.tools.allow | contains(["pixel_limb_status","pixel_web_browse"])) and
  (.agents.list[0].tools.deny | contains(["pixel_web_browse"]) | not) and
  (.agents.list[0].tools.deny | contains(["pixel_gmail_inbox","pixel_calendar_list","pixel_social_feed"]))
' "$tmp/web-only-openclaw.json" >/dev/null
jq -e '(.plugins.allow | index("evil") | not) and (.plugins.allow | index("pixel-frontier-broker") | not) and (.plugins.entries.evil == null) and (.plugins.entries["pixel-frontier-broker"] == null)' "$tmp/home/.openclaw/openclaw.json" >/dev/null
jq -e '(.agents.list[] | select(.id=="pixel") | .tools.deny | contains(["pixel_frontier_plan_review","pixel_frontier_failure_triage","pixel_frontier_job_get","pixel_frontier_job_wait","pixel_frontier_job_events","pixel_frontier_job_cancel","pixel_frontier_usage","pixel_frontier_finalize"]))' "$tmp/home/.openclaw/openclaw.json" >/dev/null
jq -e --arg old "$tmp/old-source-plugin" --arg current "$tmp/home/.local/share/pixel/current/plugin" '(.plugins.load.paths | index($old) | not) and (.plugins.load.paths | index($current) != null)' "$tmp/home/.openclaw/openclaw.json" >/dev/null
test ! -e "$tmp/home/.config/systemd/user/openclaw-gateway.service"
test -f "$tmp/home/.config/systemd/system/openclaw-gateway.service"
test ! -e "$tmp/home/.config/systemd/system/pixel-web-courier.service"
grep -F 'Test Owner' "$tmp/home/.openclaw/workspace-pixel/IDENTITY.md" >/dev/null
grep -F 'pixel_calendar_propose_update' "$tmp/home/.openclaw/workspace-pixel/AGENTS.md" >/dev/null
grep -F 'pixel_limb_status' "$tmp/home/.openclaw/workspace-pixel/AGENTS.md" >/dev/null
grep -F 'never capability discovery for another limb' "$tmp/home/.openclaw/workspace-pixel/TOOLS.md" >/dev/null
test ! -e "$tmp/home/.openclaw/workspace-pixel/scripts/xfeed.sh"
printf '%s\n' afe79ff2ad96 > "$PIXEL_FAKE_DOCKER_STATE"
if PIXEL_FAKE_SANDBOX_CONTAINER_IMAGE=sha256:stale ./pixel verify >/dev/null 2>&1; then echo "Verify accepted a stale agent sandbox container" >&2; exit 1; fi
test ! -e "$runtime_attestation"
PIXEL_FAKE_SANDBOX_CONTAINER_IMAGE="$candidate_sandbox_image_id" ./pixel verify >/dev/null
test -f "$runtime_attestation"
dormant_verify="$tmp/dormant-sandbox-verify.out"
PIXEL_FAKE_SANDBOX_CONTAINER_IMAGE="$candidate_sandbox_image_id" \
  PIXEL_FAKE_SANDBOX_STATE_STATUS=exited PIXEL_FAKE_SANDBOX_STATE_RUNNING=false \
  PIXEL_FAKE_SANDBOX_STATE_PID=0 PIXEL_FAKE_SANDBOX_STATE_EXIT_CODE=143 \
  ./pixel verify >"$dormant_verify" 2>&1
grep -F 'Agent sandbox container is dormant after a recognized lifecycle boundary' "$dormant_verify" >/dev/null
if PIXEL_FAKE_SANDBOX_CONTAINER_IMAGE="$candidate_sandbox_image_id" \
   PIXEL_FAKE_SANDBOX_STATE_STATUS=exited PIXEL_FAKE_SANDBOX_STATE_RUNNING=false \
   PIXEL_FAKE_SANDBOX_STATE_PID=0 PIXEL_FAKE_SANDBOX_STATE_EXIT_CODE=137 \
   ./pixel verify >/dev/null 2>&1; then
  echo "Verify accepted an OOM-like dormant sandbox exit" >&2
  exit 1
fi
if PIXEL_FAKE_SANDBOX_CONTAINER_IMAGE="$candidate_sandbox_image_id" \
   PIXEL_FAKE_SANDBOX_STATE_STATUS=exited PIXEL_FAKE_SANDBOX_STATE_RUNNING=false \
   PIXEL_FAKE_SANDBOX_STATE_PID=0 PIXEL_FAKE_SANDBOX_STATE_EXIT_CODE=143 \
   PIXEL_FAKE_SANDBOX_NETWORK_MODE=host ./pixel verify >/dev/null 2>&1; then
  echo "Verify accepted a dormant sandbox with unsafe static confinement" >&2
  exit 1
fi
if PIXEL_FAKE_SANDBOX_CONTAINER_IMAGE="$candidate_sandbox_image_id" \
   PIXEL_FAKE_SANDBOX_STATE_RESTARTING=true ./pixel verify >/dev/null 2>&1; then
  echo "Verify accepted a restarting sandbox" >&2
  exit 1
fi
if PIXEL_FAKE_SANDBOX_CONTAINER_IMAGE="$candidate_sandbox_image_id" \
   PIXEL_FAKE_SANDBOX_RESTART_POLICY=always ./pixel verify >/dev/null 2>&1; then
  echo "Verify accepted a restartable sandbox" >&2
  exit 1
fi
printf '%s\n' afe79ff2ad96 bbbbbbbbbbbb > "$PIXEL_FAKE_DOCKER_STATE"
if PIXEL_FAKE_SANDBOX_CONTAINER_IMAGE="$candidate_sandbox_image_id" ./pixel verify >/dev/null 2>&1; then
  echo "Verify accepted multiple agent-scoped sandbox containers" >&2
  exit 1
fi
printf '%s\n' afe79ff2ad96 > "$PIXEL_FAKE_DOCKER_STATE"
PIXEL_FAKE_SANDBOX_CONTAINER_IMAGE="$candidate_sandbox_image_id" \
  PIXEL_FAKE_SANDBOX_STATE_STATUS=exited PIXEL_FAKE_SANDBOX_STATE_RUNNING=false \
  PIXEL_FAKE_SANDBOX_STATE_PID=0 PIXEL_FAKE_SANDBOX_STATE_EXIT_CODE=143 \
  bash -c 'source scripts/lib/common.sh; pixel_load_env; pixel_retire_agent_sandboxes' >/dev/null
test ! -e "$PIXEL_FAKE_DOCKER_STATE"
printf '%s\n' afe79ff2ad96 > "$PIXEL_FAKE_DOCKER_STATE"
test -z "$(find "$repo/dist" -maxdepth 1 -name '.verify-release-identity.*' -print -quit)"
active_release=$(realpath -e "$tmp/home/.local/share/pixel/current")
if find "$active_release" -type f -name '*.pyc' -print -quit | grep -q .; then
  echo "Verify wrote Python bytecode into the immutable installed release" >&2
  exit 1
fi
mkdir "$active_release/__pycache__"
printf 'runtime-generated-bytecode\n' > "$active_release/__pycache__/fixture.cpython-312.pyc"
unmanifested_verify="$tmp/unmanifested-release-verify.out"
if PIXEL_FAKE_SANDBOX_CONTAINER_IMAGE="$candidate_sandbox_image_id" ./pixel verify >"$unmanifested_verify" 2>&1; then
  echo "Verify accepted runtime-generated bytecode in the immutable installed release" >&2
  exit 1
fi
grep -F 'Installed release tree contains unmanifested or unsafe entries' "$unmanifested_verify" >/dev/null
rm -f -- "$active_release/__pycache__/fixture.cpython-312.pyc"
rmdir "$active_release/__pycache__"
PIXEL_FAKE_SANDBOX_CONTAINER_IMAGE="$candidate_sandbox_image_id" ./pixel verify >/dev/null
test -f "$runtime_attestation"
rm -f -- "$PIXEL_FAKE_DOCKER_STATE"
bash -c 'source scripts/lib/common.sh; pixel_load_env; PIXEL_SOURCE_BROKER_ENABLED=1; PIXEL_OPS_BROKER_ENABLED=1; pixel_refresh_custom_plugin_registry' >/dev/null
if PIXEL_FAKE_CUSTOM_PLUGIN_VERSION=0.0.0 bash -c 'source scripts/lib/common.sh; pixel_load_env; PIXEL_SOURCE_BROKER_ENABLED=1; PIXEL_OPS_BROKER_ENABLED=1; pixel_refresh_custom_plugin_registry' >/dev/null 2>&1; then echo "Registry refresh accepted a stale custom plugin version" >&2; exit 1; fi
if PIXEL_FAKE_CUSTOM_PLUGIN_ROOT="$tmp/old-source-plugin" bash -c 'source scripts/lib/common.sh; pixel_load_env; PIXEL_SOURCE_BROKER_ENABLED=1; PIXEL_OPS_BROKER_ENABLED=1; pixel_refresh_custom_plugin_registry' >/dev/null 2>&1; then echo "Registry refresh accepted a stale custom plugin root" >&2; exit 1; fi
gateway_token_before=$(jq -r '.gateway.auth.token' "$tmp/home/.openclaw/openclaw.json")
./pixel rotate gateway --confirm >/dev/null
gateway_token_after=$(jq -r '.gateway.auth.token' "$tmp/home/.openclaw/openclaw.json")
[[ "$gateway_token_after" =~ ^[0-9a-f]{64}$ && "$gateway_token_after" != "$gateway_token_before" ]]
python3 - "$gateway_token_after" .env <<'PY'
import pathlib, sys
token, path = sys.argv[1:]
if f"PIXEL_GATEWAY_TOKEN='{token}'" not in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
    raise SystemExit("rotated gateway token was not persisted")
PY
gateway_token_stable=$gateway_token_after
export PIXEL_FAKE_SYSTEMCTL_FAIL_IS_ACTIVE=1
if ./pixel rotate gateway --confirm >/dev/null 2>&1; then echo "Gateway rotation unexpectedly survived failed verification" >&2; exit 1; fi
unset PIXEL_FAKE_SYSTEMCTL_FAIL_IS_ACTIVE
test "$(jq -r '.gateway.auth.token' "$tmp/home/.openclaw/openclaw.json")" = "$gateway_token_stable"
lock_ready="$tmp/deployment-lock-ready"
(
  source "$repo/scripts/lib/common.sh"
  pixel_load_env
  pixel_acquire_deployment_lock exclusive
  touch "$lock_ready"
  sleep 2
) &
lock_holder=$!
for _ in {1..40}; do [[ -f "$lock_ready" ]] && break; sleep 0.05; done
test -f "$lock_ready"
if ./pixel backup "$tmp/lock-conflict-backup" >/dev/null 2>&1; then echo "Concurrent backup bypassed the deployment lock" >&2; exit 1; fi
wait "$lock_holder"
transaction_backup_dir="$tmp/transaction-backups"
transaction_backup=$(./pixel backup "$transaction_backup_dir")
printf '%s\n' 'corrupted identity fixture' > "$tmp/home/.openclaw/workspace-pixel/IDENTITY.md"
./pixel restore "$transaction_backup" --identity "$identity" --replace --confirm | jq -e '.status == "pass" and .verified == true' >/dev/null
grep -F 'Test Owner' "$tmp/home/.openclaw/workspace-pixel/IDENTITY.md" >/dev/null
test "$(find "$transaction_backup_dir" -maxdepth 2 -type f -name '*.tar.gz.age' | wc -l)" -ge 2
rollback_probe_backup=$(./pixel backup "$tmp/restore-rollback-probe")
printf '%s\n' 'pre-restore state must survive failed verification' > "$tmp/home/.openclaw/workspace-pixel/IDENTITY.md"
export PIXEL_FAKE_SYSTEMCTL_FAIL_IS_ACTIVE=1
if ./pixel restore "$rollback_probe_backup" --identity "$identity" --replace --confirm >/dev/null 2>&1; then echo "Restore unexpectedly survived failed verification" >&2; exit 1; fi
unset PIXEL_FAKE_SYSTEMCTL_FAIL_IS_ACTIVE
grep -Fx 'pre-restore state must survive failed verification' "$tmp/home/.openclaw/workspace-pixel/IDENTITY.md" >/dev/null
./pixel restore "$rollback_probe_backup" --identity "$identity" --replace --confirm >/dev/null
grep -F 'Test Owner' "$tmp/home/.openclaw/workspace-pixel/IDENTITY.md" >/dev/null
personal_occurrences_after=$(personal_occurrences | wc -l)
if [[ "$personal_occurrences_after" != "$personal_occurrences_before" ]]; then echo "Personal answers escaped generated paths" >&2; exit 1; fi
rollback_config_hash=$(sha256sum "$tmp/home/.openclaw/openclaw.json" | awk '{print $1}')
rollback_unit_hash=$(sha256sum "$tmp/home/.config/systemd/system/openclaw-gateway.service" | awk '{print $1}')
printf '%s\n' releases/0.9.0 > "$apply_backup/previous-release"
if ./pixel rollback --confirm >/dev/null 2>&1; then echo "Rollback accepted a relative prior release" >&2; exit 1; fi
test "$(readlink -f "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/$pixel_version"
test "$(sha256sum "$tmp/home/.openclaw/openclaw.json" | awk '{print $1}')" = "$rollback_config_hash"
test "$(sha256sum "$tmp/home/.config/systemd/system/openclaw-gateway.service" | awk '{print $1}')" = "$rollback_unit_hash"
printf '%s\n' /etc/passwd > "$apply_backup/previous-release"
if ./pixel rollback --confirm >/dev/null 2>&1; then echo "Rollback accepted a prior release outside the release root" >&2; exit 1; fi
test "$(readlink -f "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/$pixel_version"
printf '%s\n' "$tmp/home/.local/share/pixel/releases/0.9.0" > "$apply_backup/previous-release"
mv "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/preserve.state" "$tmp/missing-preserve.state"
if ./pixel rollback --confirm >/dev/null 2>&1; then echo "Rollback accepted a missing preserved sandbox image" >&2; exit 1; fi
mv "$tmp/missing-preserve.state" "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/preserve.state"
test "$(readlink -f "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/$pixel_version"
mv "$apply_backup/previous-sandbox-version" "$tmp/previous-sandbox-version"
ln -s "$tmp/previous-sandbox-version" "$apply_backup/previous-sandbox-version"
if ./pixel rollback --confirm >/dev/null 2>&1; then echo "Rollback accepted a symlinked sandbox version record" >&2; exit 1; fi
rm -f -- "$apply_backup/previous-sandbox-version"
mv "$tmp/previous-sandbox-version" "$apply_backup/previous-sandbox-version"
printf '%s\n' 0.8.0 > "$apply_backup/previous-sandbox-version"
if ./pixel rollback --confirm >/dev/null 2>&1; then echo "Rollback accepted a mismatched sandbox version record" >&2; exit 1; fi
printf '%s\n' 0.9.0 > "$apply_backup/previous-sandbox-version"
printf '%s\n' sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb > "$apply_backup/previous-sandbox-image-id"
if ./pixel rollback --confirm >/dev/null 2>&1; then echo "Rollback accepted a sandbox digest that differs from the preserved image" >&2; exit 1; fi
printf '%s\n' "$previous_sandbox_image_id" > "$apply_backup/previous-sandbox-image-id"
if PIXEL_FAKE_DOCKER_FAIL_TAG_SOURCE="$previous_sandbox_image_id" ./pixel rollback --confirm >/dev/null 2>&1; then echo "Rollback unexpectedly survived a failed offline sandbox restore" >&2; exit 1; fi
test "$(readlink -f "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/$pixel_version"
test "$(sha256sum "$tmp/home/.openclaw/openclaw.json" | awk '{print $1}')" = "$rollback_config_hash"
# Fail-closed adversarial check: if the runtime attestation cannot be safely invalidated
# (here an unsafe directory occupies the path), a rollback must abort nonzero and must NOT
# consume the single-use rollback marker, leaving honest, retryable recovery state. No stale
# positive receipt may survive: the active release has been switched to the previous release
# (the symlink flips before reconciliation), so the pre-existing positive attestation must
# not be left asserting the rolled-back-from release.
test -e "$runtime_attestation"
mv "$runtime_attestation" "$tmp/saved-attestation"
mkdir "$runtime_attestation"
if ./pixel rollback --confirm >/dev/null 2>&1; then echo "Rollback succeeded with an unsafe attestation path" >&2; exit 1; fi
test -d "$runtime_attestation"
test -f "$tmp/home/.openclaw/backups/last-apply"
# The active release already switched to the previous release (0.9.0), but the unsafe
# attestation path proves the stale positive receipt was not left in place.
rmdir "$runtime_attestation"
mv "$tmp/saved-attestation" "$runtime_attestation"
test -e "$runtime_attestation"
./pixel rollback --confirm
test "$(readlink "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/0.9.0"
# A successful rollback must never retain the prior release's positive runtime
# attestation: the active release changed, so any receipt would be a stale false positive.
test ! -e "$runtime_attestation"
test "$(sed -n '1p' "$PIXEL_FAKE_DOCKER_IMAGE_STATE_DIR/shared.state")" = 0.9.0
grep -F "image tag $previous_sandbox_image_id $PIXEL_GENERATED_SANDBOX_IMAGE" "$PIXEL_FAKE_DOCKER_LOG" >/dev/null
cmp "$tmp/original-openclaw.json" "$tmp/home/.openclaw/openclaw.json"
test ! -e "$tmp/home/.config/pixel-agent/gateway.env"
test -f "$tmp/home/.config/systemd/user/openclaw-gateway.service"
test ! -e "$tmp/home/.config/systemd/system/openclaw-gateway.service"
test ! -e "$tmp/home/.openclaw/workspace-pixel/WEB-NAVIGATION.md"
test ! -e "$tmp/home/.openclaw/workspace-pixel/AGENTS.md"
test ! -e "$tmp/home/.openclaw/workspace-pixel/TOOLS.md"
test ! -e "$tmp/home/.openclaw/workspace-pixel/scripts/browse.sh"

# A rolled-back release remains as immutable audit/recovery evidence. A changed copy must
# fail before live mutation and remain untouched; restoring the exact bytes must permit a
# second apply to adopt (not rebuild or replace) that release, after which rollback remains
# available and returns to the same prior deployment.
retained_release="$tmp/home/.local/share/pixel/releases/$pixel_version"
retained_tree_sha=$(python3 scripts/lib/release-tree-sha.py "$retained_release")
cp -p "$retained_release/plugin/email-query.js" "$tmp/exact-email-query.js"
printf '\n// tampered retained release\n' >> "$retained_release/plugin/email-query.js"
tampered_file_sha=$(sha256sum "$retained_release/plugin/email-query.js" | awk '{print $1}')
test "$tampered_file_sha" != "$(sha256sum "$tmp/exact-email-query.js" | awk '{print $1}')"
# Release-update prepare loads the candidate sandbox image before activation. Recreate
# that precondition after rollback, then refresh the reviewed plan because the earlier
# gateway-rotation assertions intentionally changed a hashed deployment input. Restore
# the exact original plan inputs: a changed plan must not adopt the retained release.
cp -p "$tmp/exact-release-plan.env" .env
./pixel plan >/dev/null
tampered_apply_output="$tmp/tampered-retained-release-apply.out"
if ./pixel apply --confirm >"$tampered_apply_output" 2>&1; then
  echo "Apply unexpectedly adopted a tampered retained release" >&2
  exit 1
fi
grep -F "Release already exists but is not byte-exact to the reviewed plan" "$tampered_apply_output" >/dev/null
test "$(readlink "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/0.9.0"
test "$(sha256sum "$retained_release/plugin/email-query.js" | awk '{print $1}')" = "$tampered_file_sha"
cp -p "$tmp/exact-email-query.js" "$retained_release/plugin/email-query.js"
test "$(python3 scripts/lib/release-tree-sha.py "$retained_release")" = "$retained_tree_sha"
./pixel apply --confirm
test "$(readlink -f "$tmp/home/.local/share/pixel/current")" = "$retained_release"
test "$(python3 scripts/lib/release-tree-sha.py "$retained_release")" = "$retained_tree_sha"
reapply_backup=$(cat "$tmp/home/.openclaw/backups/last-apply")
test "$(cat "$reapply_backup/previous-release")" = "$tmp/home/.local/share/pixel/releases/0.9.0"
./pixel rollback --confirm
test "$(readlink "$tmp/home/.local/share/pixel/current")" = "$tmp/home/.local/share/pixel/releases/0.9.0"
test "$(python3 scripts/lib/release-tree-sha.py "$retained_release")" = "$retained_tree_sha"
# Rollback from the re-adopted retained release must also clear the freshly regenerated
# attestation; a stale positive receipt cannot survive any successful rollback.
test ! -e "$runtime_attestation"
ops_policy="$tmp/ops-policy.json"
modular_answers="$tmp/modular-answers.json"
frontier_key="$tmp/frontier-key"
extension_dir="$tmp/reviewed-extension"
limb_pack="$tmp/signed-policy-limb"
mkdir -p "$extension_dir"
printf '%s\n' 'test-only-provider-key' > "$frontier_key"
chmod 600 "$frontier_key"
printf '%s\n' '{"id":"fixture-extension","contracts":{"tools":["pixel_fixture_status"]}}' > "$extension_dir/openclaw.plugin.json"
printf '%s\n' 'export const fixture = true;' > "$extension_dir/index.js"
extension_digest=$(./pixel extension-hash "$extension_dir")
./pixel limb-kit generate fixture-metrics "$limb_pack" --name 'Fixture Metrics' >/dev/null
./pixel limb-kit add-local "$limb_pack" fixture-status --name 'Fixture status' >/dev/null
./pixel limb-kit add-operations "$limb_pack" fixture-actions --name 'Fixture actions' --target-placeholder fixture-target >/dev/null
./pixel limb-kit add-frontier "$limb_pack" fixture-review --name 'Fixture review' --task-class plan_review >/dev/null
ssh-keygen -q -t ed25519 -N '' -f "$tmp/limb-publisher"
chmod 600 "$tmp/limb-publisher"
./pixel limb-kit sign "$limb_pack" --signing-key "$tmp/limb-publisher" --identity fixture-publisher --confirm >/dev/null
limb_digest=$(./pixel extension-hash "$limb_pack")
node - "$ops_policy" "$modular_answers" "$tmp" "$repo" "$extension_dir" "$extension_digest" "$frontier_key" "$limb_pack" "$limb_digest" <<'NODE'
const fs=require('fs'); const crypto=require('crypto'); const [policyOut,answersOut,tmp,repo,extensionDir,extensionDigest,frontierKey,limbPack,limbDigest]=process.argv.slice(2);
const policy=JSON.parse(fs.readFileSync(`${repo}/deploy/ops-broker/policy.example.json`,'utf8'));
policy.targets['control-host'].expectedHostname='test-host';
policy.targets['control-host'].defaultCwd='/tmp/pixel-operations';
policy.targets['control-host'].allowedRoots=['/tmp/pixel-operations'];
policy.actions['host.identity'].cwd='/tmp/pixel-operations';
policy.actions['host.platform'].cwd='/tmp/pixel-operations';
policy.actions['test.named'].cwd='/tmp/pixel-operations';
policy.actions['host.processes']=JSON.parse(fs.readFileSync(`${repo}/deploy/ops-broker/action-packs.example.json`,'utf8')).actions['host.processes'];
fs.writeFileSync(policyOut,JSON.stringify(policy));
const limbManifest=JSON.parse(fs.readFileSync(`${limbPack}/pixel-limb.json`,'utf8'));
const limbLock=JSON.parse(fs.readFileSync(`${limbPack}/pixel-pack.lock.json`,'utf8'));
const sourceLimbPack={id:limbManifest.id,version:limbManifest.version,treeSha256:limbLock.treeSha256};
const receipt=(id)=>{const file=`${limbPack}/packs/${id}.json`;return {file,sha256:crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'),policyPackId:id,sourceLimbPack};};
const localReceipt=receipt('fixture-status'); const actionReceipt=receipt('fixture-actions'); const frontierReceipt=receipt('fixture-review');
fs.writeFileSync(answersOut,JSON.stringify({deploymentProfile:'prepared',ownerName:'Modular Owner',organization:'Test Client',deploymentName:'modular',timeZone:'America/New_York',agentId:'pixel',agentName:'Pixel',openclawBin:`${repo}/tests/fixtures/bin/openclaw`,openclawHome:`${tmp}/home/.openclaw`,installDir:`${tmp}/home/.local/share/pixel`,workspace:`${tmp}/home/.openclaw/workspace-pixel`,modelProvider:'local',modelId:'test-model',modelName:'Test Model',modelBaseUrl:'http://127.0.0.1:8000/v1',modelApiKey:'local-no-auth',modelContextWindow:8192,modelMaxTokens:1024,searxngBaseUrl:'http://127.0.0.1:8890',embeddingModel:'test.gguf',embeddingCache:`${tmp}/home/.cache/embeddings`,googleAccount:['owner','example.com'].join('@'),calendarId:'primary',gatewayPort:18789,gatewayExtensions:[{id:'discord'},{id:'fixture-extension',path:extensionDir,sha256:extensionDigest,tools:['pixel_fixture_status']},{id:limbManifest.id,path:limbPack,sha256:limbDigest,tools:limbManifest.tools.map(({name})=>name)}],localCapabilityPacks:[localReceipt],emailLimbEnabled:false,calendarLimbEnabled:true,socialLimbEnabled:false,webLimbEnabled:false,operationsLimbEnabled:true,operationsPolicyFile:policyOut,operationsActionPacks:[{file:`${repo}/deploy/ops-broker/action-packs.example.json`,targets:{'example-worker':['control-host']},skipActions:['host.processes']},{...actionReceipt,targets:{'fixture-target':['control-host']}}],frontierLimbEnabled:true,frontierPolicyFile:`${repo}/deploy/frontier-broker/policy.example.json`,frontierTaskPacks:[frontierReceipt],frontierCredentialFile:frontierKey}));
NODE
bad_extension_answers="$tmp/bad-extension-answers.json"
node - "$modular_answers" "$bad_extension_answers" <<'NODE'
const fs=require('fs'); const [input,output]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.gatewayExtensions=[{id:'ambient-evil'}];
fs.writeFileSync(output,JSON.stringify(answers));
NODE
if ./pixel configure --answers "$bad_extension_answers" --force >/dev/null 2>&1; then echo "Configure accepted an unpinned ambient extension ID" >&2; exit 1; fi
bad_policy_receipt_answers="$tmp/bad-policy-receipt-answers.json"
node - "$modular_answers" "$bad_policy_receipt_answers" <<'NODE'
const fs=require('fs'); const [input,output]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.localCapabilityPacks[0].sha256='0'.repeat(64);
fs.writeFileSync(output,JSON.stringify(answers));
NODE
if ./pixel configure --answers "$bad_policy_receipt_answers" --force >/dev/null 2>&1; then echo "Configure accepted a changed signed policy-pack receipt" >&2; exit 1; fi
widening_frontier_policy="$tmp/widening-frontier-base.json"
widening_frontier_answers="$tmp/widening-frontier-answers.json"
node - "$modular_answers" "$widening_frontier_answers" "$repo" "$widening_frontier_policy" <<'NODE'
const fs=require('fs'); const [input,output,repo,policyOut]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
const policy=JSON.parse(fs.readFileSync(`${repo}/deploy/frontier-broker/policy.example.json`,'utf8'));
policy.taskClasses.plan_review.enabled=false;
fs.writeFileSync(policyOut,JSON.stringify(policy));
answers.frontierPolicyFile=policyOut;
fs.writeFileSync(output,JSON.stringify(answers));
NODE
if ./pixel configure --answers "$widening_frontier_answers" --force >/dev/null 2>&1; then echo "Configure allowed a signed Frontier restriction to enable a base-disabled task" >&2; exit 1; fi
public_model_answers="$tmp/public-model-answers.json"
node - "$modular_answers" "$public_model_answers" <<'NODE'
const fs=require('fs'); const [input,output]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.modelBaseUrl='https://models.example.invalid/v1';
fs.writeFileSync(output,JSON.stringify(answers));
NODE
if ./pixel configure --answers "$public_model_answers" --force >/dev/null 2>&1; then echo "Frontier configure accepted a public primary model endpoint without an explicit private-host exception" >&2; exit 1; fi
attested_model_answers="$tmp/attested-model-answers.json"
node - "$public_model_answers" "$attested_model_answers" <<'NODE'
const fs=require('fs'); const [input,output]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.modelPrivateHosts=['models.example.invalid'];
fs.writeFileSync(output,JSON.stringify(answers));
NODE
./pixel configure --answers "$attested_model_answers" --force
jq -e '.frontierBroker.primaryModelPrivate == false and .frontierBroker.primaryModelOperatorAttested == true' .generated/deployment.json >/dev/null
./pixel configure --answers "$modular_answers" --force
if grep -Eq '^PIXEL_(OPS|FRONTIER)_POLICY_SOURCE=' .env; then echo "Enabled broker configuration retained a redundant policy source path" >&2; exit 1; fi
grep -Fx "PIXEL_FRONTIER_CREDENTIAL_SOURCE='$frontier_key'" .env >/dev/null
jq -e '.schemaVersion == 2 and .actions["deploy.activate"].tier == "managed" and .actions["deploy.activate"].targets == ["control-host"] and .actions["fixture-metrics.status"].targets == ["control-host"] and (.authority.grants[] | select(.id == "fixture-deploy-window") | .targets == ["control-host"])' .generated/ops-policy.json >/dev/null
jq -e '.schemaVersion == 2 and .deployment == "modular" and .provider.kind == "codex" and .taskClasses.plan_review.allowedClassifications == ["public","internal-derived"] and .taskClasses.plan_review.maxInputTokens == 4096 and .taskClasses.plan_review.maxOutputTokens == 1024 and .taskClasses.plan_review.rehydrate == false and .routing.maxLocalAttempts == 3 and .routing.cache.enabled == true' .generated/frontier-policy.json >/dev/null
jq -e '.limbs.frontier == true and .frontierBroker.enabled == true and .frontierBroker.credentialVisibleToGateway == false and .frontierBroker.primaryModelPrivate == true and .frontierBroker.primaryModelOperatorAttested == false and .localCapabilityPacks[0].id == "fixture-status" and .signedOperationsActionPacks[0].id == "fixture-actions" and .frontierTaskPacks[0].id == "fixture-review"' .generated/deployment.json >/dev/null
grep -F "PIXEL_FRONTIER_FEEDBACK_DIR='/var/lib/pixel-frontier-broker/feedback'" .generated/gateway.env >/dev/null
grep -F 'InaccessiblePaths=-/srv -/mnt -/media' .generated/pixel-frontier-broker.service >/dev/null
grep -F 'ExecStartPre="/opt/pixel-frontier-broker/verify-codex.py" "/usr/local/bin/codex"' .generated/pixel-frontier-broker.service >/dev/null
subscription_auth="$tmp/codex-auth.json"
subscription_answers="$tmp/subscription-answers.json"
printf '%s\n' '{"auth_mode":"chatgpt","fixture":"test-only-private-auth-material"}' > "$subscription_auth"
chmod 600 "$subscription_auth"
node - "$modular_answers" "$subscription_answers" "$repo" "$subscription_auth" <<'NODE'
const fs=require('fs'); const [input,output,repo,auth]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.frontierPolicyFile=`${repo}/deploy/frontier-broker/policy.chatgpt.example.json`;
answers.frontierCredentialFile=auth;
fs.writeFileSync(output,JSON.stringify(answers));
NODE
./pixel configure --answers "$subscription_answers" --force
jq -e '.provider.authMode == "chatgpt"' .generated/frontier-policy.json >/dev/null
jq -e '.frontierBroker.authMode == "chatgpt" and .frontierBroker.credentialVisibleToGateway == false' .generated/deployment.json >/dev/null
grep -F "PIXEL_FRONTIER_AUTH_MODE='chatgpt'" .generated/frontier-broker.env >/dev/null
grep -F "PIXEL_FRONTIER_CREDENTIAL_PATH='/var/lib/pixel-frontier-broker/private/codex-auth/auth.json'" .env >/dev/null
if grep -F '/var/lib/pixel-frontier-broker/private/codex-auth/auth.json' .generated/pixel-frontier-broker.service; then echo "Writable ChatGPT auth cache was forced read-only" >&2; exit 1; fi
if grep -Eq '^PIXEL_FRONTIER_(POLICY_PATH|CREDENTIAL_PATH|CREDENTIAL_SOURCE)=' .generated/gateway.env; then echo "Gateway environment received ChatGPT Frontier authority or credentials" >&2; exit 1; fi
managed_frontier_answers="$tmp/managed-frontier-answers.json"
node - "$modular_answers" "$managed_frontier_answers" <<'NODE'
const fs=require('fs'); const [input,output]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
delete answers.frontierPolicyFile;
delete answers.frontierCredentialFile;
answers.frontierAuthMode='chatgpt';
answers.frontierBudgetProfile='starter';
fs.writeFileSync(output,JSON.stringify(answers));
NODE
./pixel configure --answers "$managed_frontier_answers" --force
jq -e '.provider.authMode == "chatgpt" and .provider.cost.mode == "subscription" and .budgets == {windowSeconds:86400,maxJobs:5,maxInputTokens:50000,maxOutputTokens:10000,maxFailures:2,maxEstimatedCostMicros:null}' .generated/frontier-policy.json >/dev/null
jq -e '.frontierBroker.authMode == "chatgpt" and .frontierBroker.budgetProfile == "starter" and .frontierBroker.credentialVisibleToGateway == false' .generated/deployment.json >/dev/null

managed_api_answers="$tmp/managed-api-answers.json"
node - "$managed_frontier_answers" "$managed_api_answers" <<'NODE'
const fs=require('fs'); const [input,output]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.frontierAuthMode='api-key';
answers.frontierBudgetProfile='expanded';
fs.writeFileSync(output,JSON.stringify(answers));
NODE
./pixel configure --answers "$managed_api_answers" --force
jq -e '.provider.authMode == "api-key" and .provider.cost.mode == "unavailable" and .budgets == {windowSeconds:86400,maxJobs:50,maxInputTokens:500000,maxOutputTokens:100000,maxFailures:10,maxEstimatedCostMicros:null}' .generated/frontier-policy.json >/dev/null
jq -e '.frontierBroker.authMode == "api-key" and .frontierBroker.budgetProfile == "expanded"' .generated/deployment.json >/dev/null

custom_api_policy="$tmp/custom-api-policy.json"
cp "$repo/deploy/frontier-broker/policy.example.json" "$custom_api_policy"
mismatched_frontier_answers="$tmp/mismatched-frontier-answers.json"
node - "$managed_frontier_answers" "$mismatched_frontier_answers" "$custom_api_policy" <<'NODE'
const fs=require('fs'); const [input,output,policy]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.frontierPolicyFile=policy;
fs.writeFileSync(output,JSON.stringify(answers));
NODE
if ./pixel configure --answers "$mismatched_frontier_answers" --force >/dev/null 2>&1; then echo "Configure silently overrode a custom Frontier billing boundary" >&2; exit 1; fi
custom_budget_answers="$tmp/custom-budget-answers.json"
node - "$managed_api_answers" "$custom_budget_answers" "$custom_api_policy" <<'NODE'
const fs=require('fs'); const [input,output,policy]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(input,'utf8'));
answers.frontierPolicyFile=policy;
fs.writeFileSync(output,JSON.stringify(answers));
NODE
if ./pixel configure --answers "$custom_budget_answers" --force >/dev/null 2>&1; then echo "Configure silently ignored a managed budget preset for a custom Frontier policy" >&2; exit 1; fi
node - "$custom_budget_answers" <<'NODE'
const fs=require('fs'); const [file]=process.argv.slice(2);
const answers=JSON.parse(fs.readFileSync(file,'utf8'));
answers.frontierBudgetProfile='custom';
fs.writeFileSync(file,JSON.stringify(answers));
NODE
./pixel configure --answers "$custom_budget_answers" --force
jq -e '.frontierBroker.authMode == "api-key" and .frontierBroker.budgetProfile == "custom"' .generated/deployment.json >/dev/null
jq -e '.budgets == {windowSeconds:86400,maxJobs:20,maxInputTokens:200000,maxOutputTokens:40000,maxFailures:5,maxEstimatedCostMicros:null}' .generated/frontier-policy.json >/dev/null
./pixel configure --answers "$modular_answers" --force
private_onboarding="$tmp/private-onboarding.json"
cp "$modular_answers" "$private_onboarding"
./pixel ops-action-pack "$private_onboarding" "$repo/deploy/ops-broker/action-packs.example.json" example-worker control-host --skip-action host.processes --confirm >/dev/null
jq -e --arg pack "$repo/deploy/ops-broker/action-packs.example.json" '(.operationsActionPackFiles == null) and any(.operationsActionPacks[]; .file == $pack and .targets["example-worker"] == ["control-host"] and .skipActions == ["host.processes"]) and any(.operationsActionPacks[]; .sourceLimbPack.id == "fixture-metrics")' "$private_onboarding" >/dev/null
test "$(find "$tmp" -maxdepth 1 -name 'private-onboarding.json.before-action-pack-*' | wc -l)" = 1
set -a
# shellcheck disable=SC1091
source .env
set +a
export PIXEL_PLUGIN_PATH="$repo/plugin" PIXEL_OPS_PLUGIN_PATH="$repo/plugin-ops" PIXEL_FRONTIER_PLUGIN_PATH="$repo/plugin-frontier"
node scripts/render-config.mjs "$tmp/modular-openclaw.json" >/dev/null
jq -e '.plugins.entries["pixel-source-broker"].enabled == true and .plugins.entries["pixel-operations-broker"].enabled == true and .plugins.entries["pixel-frontier-broker"].enabled == true' "$tmp/modular-openclaw.json" >/dev/null
jq -e '(.plugins.allow | contains(["discord","fixture-extension","pixel-source-broker","pixel-operations-broker","pixel-frontier-broker"])) and .plugins.entries.discord.enabled == true and .plugins.entries["fixture-extension"].enabled == true' "$tmp/modular-openclaw.json" >/dev/null
jq -e --arg path "$extension_dir" '(.plugins.load.paths | index($path) != null)' "$tmp/modular-openclaw.json" >/dev/null
grep -F "$extension_dir" .generated/openclaw-gateway.service >/dev/null
printf '%s\n' 'tampered' >> "$extension_dir/index.js"
if node scripts/render-config.mjs "$tmp/tampered-extension-config.json" >/dev/null 2>&1; then echo "Renderer accepted a changed gateway extension" >&2; exit 1; fi
jq -e '(.agents.list[] | select(.id=="pixel") | .tools.deny | contains(["pixel_gmail_inbox","pixel_gmail_sent","pixel_social_feed","pixel_web_browse","web_fetch","web_search"]))' "$tmp/modular-openclaw.json" >/dev/null
jq -e '(.agents.list[] | select(.id=="pixel") | .tools.deny | contains(["pixel_limb_status","pixel_calendar_list","pixel_ops_run","pixel_frontier_plan_review","pixel_frontier_failure_triage"]) | not)' "$tmp/modular-openclaw.json" >/dev/null
jq -e '(.tools.sandbox.tools.allow | contains(["pixel_limb_status"]))' "$tmp/modular-openclaw.json" >/dev/null
jq -e '(.tools.alsoAllow | contains(["pixel_limb_status","pixel_calendar_list","pixel_fixture_status","pixel_ops_run","pixel_frontier_plan_review","pixel_frontier_failure_triage","pixel_frontier_job_get","pixel_frontier_job_wait","pixel_frontier_job_events","pixel_frontier_job_cancel","pixel_frontier_usage","pixel_frontier_finalize"])) and (.tools.alsoAllow | contains(["pixel_gmail_inbox","pixel_social_feed","pixel_web_browse"]) | not)' "$tmp/modular-openclaw.json" >/dev/null
if grep -Eq '^PIXEL_OPS_(POLICY_PATH|SSH|PRIVATE|CREDENTIAL)=' .generated/gateway.env; then echo "Gateway environment received Operations authority" >&2; exit 1; fi
if grep -Eq '^PIXEL_FRONTIER_(POLICY_PATH|CREDENTIAL_PATH|CREDENTIAL_SOURCE)=' .generated/gateway.env; then echo "Gateway environment received Frontier authority or credentials" >&2; exit 1; fi
./pixel limbs | grep -E '^operations[[:space:]]+enabled' >/dev/null
./pixel limbs | grep -E '^frontier[[:space:]]+enabled' >/dev/null
echo "Clean-room plan/apply/verify test passed."
