#!/usr/bin/env bash
set -euo pipefail

[[ ${PIXEL_LIMB_LIVE_CONFIRM:-} == disposable-systemd-container ]] || {
  echo "Set PIXEL_LIMB_LIVE_CONFIRM=disposable-systemd-container" >&2
  exit 2
}
[[ -f /.dockerenv ]] || { echo "This destructive lifecycle probe is container-only" >&2; exit 2; }
[[ $(systemd-detect-virt --container) != none ]] || { echo "A disposable systemd container is required" >&2; exit 2; }
[[ $(id -u) == 0 ]] || { echo "The disposable lifecycle probe must run as root" >&2; exit 2; }

kit=${PIXEL_LIMB_KIT:-/usr/local/bin/pixel-limb-kit}
[[ -f $kit ]] || { echo "Pixel limb-kit script is unavailable" >&2; exit 2; }
pack_id=live-probe
gateway_user=pixel-gateway-probe
work=$(mktemp -d /root/pixel-limb-live.XXXXXXXX)
pack="$work/pack"
key="$work/publisher"
allowed="$work/allowed-signers"
onboarding="$work/onboarding.json"
operations_policy="$work/operations-policy.json"

cleanup() {
  systemctl disable --now "pixel-limb-$pack_id.timer" >/dev/null 2>&1 || true
  systemctl stop "pixel-limb-$pack_id.service" >/dev/null 2>&1 || true
  rm -f -- "/etc/systemd/system/pixel-limb-$pack_id.service" "/etc/systemd/system/pixel-limb-$pack_id.timer"
  systemctl daemon-reload >/dev/null 2>&1 || true
  rm -rf -- "/opt/pixel-limb-packs/$pack_id" "/var/lib/pixel-limb-packs/$pack_id" "$work"
  userdel "$gateway_user" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for path in "/opt/pixel-limb-packs/$pack_id" "/var/lib/pixel-limb-packs/$pack_id" "/etc/systemd/system/pixel-limb-$pack_id.service" "/etc/systemd/system/pixel-limb-$pack_id.timer"; do
  [[ ! -e $path && ! -L $path ]] || { echo "Disposable probe target already exists: $path" >&2; exit 2; }
done
getent passwd "pixel-limb-$pack_id" >/dev/null && { echo "Disposable worker account already exists" >&2; exit 2; }
getent passwd "$gateway_user" >/dev/null && { echo "Disposable gateway account already exists" >&2; exit 2; }
useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$gateway_user"

ssh-keygen -q -t ed25519 -N "" -f "$key"
sed "s/^/live-probe /" "$key.pub" > "$allowed"
chmod 0644 "$allowed"
printf '%s\n' '{"schemaVersion":2,"targets":{"lab-runner":{"enabled":true}}}' > "$operations_policy"
printf '{"schemaVersion":1,"gatewayExtensions":[],"operationsPolicyFile":"%s"}\n' "$operations_policy" > "$onboarding"
chmod 0600 "$onboarding"

python3 "$kit" generate "$pack_id" "$pack" --name "Live probe"
python3 "$kit" add-local "$pack" live-status --name "Live status"
python3 "$kit" add-operations "$pack" live-actions --name "Live actions" --target-placeholder private-target
python3 "$kit" add-frontier "$pack" live-review --name "Live review" --task-class plan_review
python3 "$kit" sign "$pack" --signing-key "$key" --identity live-probe --confirm
python3 "$kit" install "$pack" --allowed-signers "$allowed" --identity live-probe --confirm
python3 "$kit" enable "$pack_id" --onboarding "$onboarding" --gateway-user "$gateway_user" --confirm
python3 -c 'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8")); assert len(value["localCapabilityPacks"]) == 1 and len(value["frontierTaskPacks"]) == 1 and value.get("operationsActionPacks", []) == []' "$onboarding"
python3 "$kit" bind-operations "$pack_id" live-actions --onboarding "$onboarding" --target lab-runner --confirm
python3 -c 'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8")); item=value["operationsActionPacks"][0]; assert item["targets"] == {"private-target": ["lab-runner"]} and item["sourceLimbPack"]["id"] == "live-probe"' "$onboarding"

systemctl is-enabled --quiet "pixel-limb-$pack_id.timer"
systemctl show "pixel-limb-$pack_id.service" -p Result --value | grep -Fx success >/dev/null
projection="/var/lib/pixel-limb-packs/$pack_id/status.json"
[[ -f $projection && ! -L $projection ]] || { echo "Live worker did not publish its bounded projection" >&2; exit 1; }
python3 -c 'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8")); assert value["packId"] == "live-probe" and value["status"] == "ready"' "$projection"
runuser -u "$gateway_user" -- cat "$projection" >/dev/null

state_kind=directory
[[ -L /var/lib/pixel-limb-packs/$pack_id ]] && state_kind=symlink
state_target=$(readlink -f "/var/lib/pixel-limb-packs/$pack_id")

python3 "$kit" disable "$pack_id" --confirm
python3 -c 'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8")); assert value["gatewayExtensions"] == [] and value["localCapabilityPacks"] == [] and value["frontierTaskPacks"] == [] and value["operationsActionPacks"] == []' "$onboarding"
if runuser -u "$gateway_user" -- cat "$projection" >/dev/null 2>&1; then
  echo "Disabled gateway user retained projection access" >&2
  exit 1
fi
python3 "$kit" remove "$pack_id" --confirm
[[ ! -e /opt/pixel-limb-packs/$pack_id && ! -L /opt/pixel-limb-packs/$pack_id ]] || { echo "Pack residue remains" >&2; exit 1; }
[[ ! -e /var/lib/pixel-limb-packs/$pack_id && ! -L /var/lib/pixel-limb-packs/$pack_id ]] || { echo "Projection residue remains" >&2; exit 1; }
[[ ! -e /etc/systemd/system/pixel-limb-$pack_id.service && ! -e /etc/systemd/system/pixel-limb-$pack_id.timer ]] || { echo "Worker unit residue remains" >&2; exit 1; }
getent passwd "pixel-limb-$pack_id" >/dev/null && { echo "Worker account residue remains" >&2; exit 1; }
getent group "pixel-limb-$pack_id" >/dev/null && { echo "Worker group residue remains" >&2; exit 1; }

printf '{"schemaVersion":1,"status":"passed","statePathKind":"%s","stateTarget":"%s","residue":false}\n' "$state_kind" "$state_target"
