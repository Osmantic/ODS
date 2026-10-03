#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
for command in docker node; do command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }; done
image=$(node -p 'require(process.argv[1]).baseImage' "$ROOT/RELEASE-MANIFEST.json")

docker run --rm -i --network=bridge -v "$ROOT:/repo:ro" "$image" bash -s <<'CONTAINER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends sudo openssh-client passwd python3 >/dev/null
key_dir=$(mktemp -d)
trap 'rm -rf -- "$key_dir"' EXIT
ssh-keygen -q -t ed25519 -N '' -C pixel-clean-room -f "$key_dir/id_ed25519"
encoded=$(base64 -w0 < "$key_dir/id_ed25519.pub")
bash /repo/deploy/ops-runner/provision-target.sh pixel-ops-transport pixel-runner "$encoded" >/dev/null
install -o root -g root -m 0755 /repo/deploy/ops-runner/dispatch.py /usr/local/libexec/pixel-ops-dispatch
install -o root -g root -m 0755 /repo/deploy/ops-runner/managed.py /usr/local/libexec/pixel-ops-managed
install -o root -g root -m 0755 /repo/deploy/ops-runner/action.py /usr/local/libexec/pixel-ops-action
install -d -o root -g root -m 0755 /etc/pixel-ops-runner
printf '%s\n' '{"schemaVersion":1}' > /etc/pixel-ops-runner/managed.json
chmod 0600 /etc/pixel-ops-runner/managed.json
printf '%s\n' '{"schemaVersion":1,"jobRoot":"/var/lib/pixel-runner/jobs"}' > /etc/pixel-ops-runner/actions.json
chmod 0644 /etc/pixel-ops-runner/actions.json

[[ $(getent passwd pixel-runner | cut -d: -f7) == /usr/sbin/nologin ]]
[[ ! -s /var/lib/pixel-runner/.ssh/authorized_keys ]]
[[ $(wc -l < /var/lib/pixel-ops-transport/.ssh/authorized_keys) == 1 ]]
grep -Eq '^restrict,command="/usr/local/libexec/pixel-ops-dispatch --job-root /var/lib/pixel-runner/jobs --workload-user pixel-runner" ssh-ed25519 ' /var/lib/pixel-ops-transport/.ssh/authorized_keys

if sudo -u pixel-runner sudo --non-interactive --user root -- /usr/local/libexec/pixel-ops-managed --validate-config >/dev/null 2>&1; then
  echo "workload user unexpectedly has managed sudo" >&2
  exit 1
fi
if sudo -u pixel-runner /usr/local/libexec/pixel-ops-managed --validate-config >/dev/null 2>&1; then
  echo "workload user unexpectedly ran the root helper directly" >&2
  exit 1
fi
sudo -u pixel-ops-transport sudo --non-interactive --user root -- /usr/local/libexec/pixel-ops-managed --validate-config >/dev/null
observed=$(sudo -u pixel-ops-transport sudo --non-interactive --user pixel-runner -- /usr/local/libexec/pixel-ops-dispatch --workload --job-root /var/lib/pixel-runner/jobs --workload-user pixel-runner --cwd /var/lib/pixel-runner/jobs -- /bin/hostname)
[[ "$observed" == "$(hostname)" ]]
if sudo -u pixel-ops-transport sudo --non-interactive --user pixel-runner -- /usr/local/libexec/pixel-ops-dispatch --workload --job-root /var/lib/pixel-runner/jobs --workload-user pixel-runner --cwd /var/lib/pixel-runner/jobs -- /bin/sh -c id >/dev/null 2>&1; then
  echo "workload dispatcher unexpectedly accepted a shell" >&2
  exit 1
fi
transport_probe=$(sudo -u pixel-ops-transport env SSH_ORIGINAL_COMMAND=hostname PIXEL_OPS_DISPATCH_TESTING=1 /usr/local/libexec/pixel-ops-dispatch --job-root /var/lib/pixel-runner/jobs --workload-user pixel-runner)
python3 -c 'import json,sys; v=json.loads(sys.argv[1]); assert v["transportIdentitySeparated"] and v["executionIdentity"] == "pixel-runner"' "$transport_probe"
printf '%s\n' '{"status":"pass","environment":"clean-room","transportWorkloadSeparation":"pass","workloadSudo":"denied","managedRoute":"pass"}'
CONTAINER
