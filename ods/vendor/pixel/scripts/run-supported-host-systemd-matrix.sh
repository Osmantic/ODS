#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

usage() {
  echo "Usage: ./pixel qualify-hosts --evidence ABSOLUTE_NEW_DIRECTORY" >&2
  exit 2
}

evidence=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --evidence) evidence=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$evidence" == /* && "$evidence" != / ]] || usage
case "$evidence/" in "$ROOT/"*) echo "Evidence must remain outside the source repository" >&2; exit 2 ;; esac
[[ ! -e "$evidence" ]] || { echo "Evidence directory must be new" >&2; exit 2; }
for command in flock git incus jq python3 sha256sum tar timeout; do
  command -v "$command" >/dev/null || { echo "Missing qualification command: $command" >&2; exit 1; }
done
[[ -z $(git -C "$ROOT" status --porcelain=v1 --untracked-files=all) ]] || {
  echo "Supported-host qualification requires a clean source tree" >&2
  exit 1
}

source_commit=$(git -C "$ROOT" rev-parse HEAD)
source_tree=$(git -C "$ROOT" rev-parse 'HEAD^{tree}')
[[ "$source_commit" =~ ^[a-f0-9]{40}$ && "$source_tree" =~ ^[a-f0-9]{40}$ ]] || {
  echo "Source identity is unavailable" >&2
  exit 1
}
manifest="$ROOT/RELEASE-MANIFEST.json"
manifest_sha256=$(sha256sum "$manifest" | awk '{print $1}')
install -d -m 700 "$evidence"

exec 9>/tmp/dream-fleet-heavy.lock
echo "[host-matrix] waiting for the shared disposable-host lock"
flock 9

stage=$(mktemp -d)
instances=()
cleanup() {
  local instance
  for instance in "${instances[@]}"; do
    [[ "$instance" =~ ^pixel-host-q-[a-z0-9-]+$ ]] || continue
    timeout 60 incus delete --force "$instance" </dev/null >/dev/null 2>&1 || true
  done
  [[ "$stage" == /tmp/* && -d "$stage" ]] && rm -rf -- "$stage"
}
trap cleanup EXIT
git -C "$ROOT" archive --format=tar HEAD > "$stage/source.tar"

ensure_image() {
  local key=$1 fingerprint metadata
  fingerprint=$(jq -er --arg key "$key" '.qualificationImages[$key].fingerprint' "$manifest")
  if ! incus image info "$fingerprint" </dev/null >/dev/null 2>&1; then
    echo "[host-matrix:image] fetching exact $key image" >&2
    timeout 900 incus image copy "images:$fingerprint" local: </dev/null >&2
  fi
  metadata=$(incus query "/1.0/images/$fingerprint" </dev/null)
  jq -e --arg key "$key" --arg fingerprint "$fingerprint" --slurpfile release "$manifest" '
    .fingerprint == $fingerprint
    and .type == "virtual-machine"
    and .type == $release[0].qualificationImages[$key].type
    and .properties.os == $release[0].qualificationImages[$key].os
    and .properties.release == $release[0].qualificationImages[$key].release
    and .architecture == $release[0].qualificationImages[$key].architecture
  ' <<< "$metadata" >/dev/null || { echo "Pinned qualification VM identity mismatch: $key" >&2; exit 1; }
  printf '%s\n' "$fingerprint"
}

launch_lane() {
  local lane=$1 key=$2 os_id=$3 os_version=$4 image instance lane_evidence status=0
  image=$(ensure_image "$key")
  instance="pixel-host-q-${lane//./-}-${source_commit:0:7}-$$"
  [[ "$instance" =~ ^pixel-host-q-[a-z0-9-]+$ ]] || { echo "Unsafe generated VM name" >&2; return 1; }
  incus info "$instance" </dev/null >/dev/null 2>&1 && { echo "Refusing to replace an existing VM" >&2; return 1; }
  instances+=("$instance")
  echo "[host-matrix:$lane] launch"
  timeout 240 incus launch "$image" "$instance" --vm -c limits.cpu=4 -c limits.memory=8GiB </dev/null || {
    echo "Incus could not launch the disposable $lane VM" >&2
    return 1
  }
  ready=0
  for _ in $(seq 1 180); do
    if incus exec "$instance" -- systemctl is-system-running --wait </dev/null >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
  done
  [[ $ready == 1 ]] || { echo "$lane VM did not finish booting" >&2; return 1; }

  echo "[host-matrix:$lane] provision"
  incus exec "$instance" -- bash -lc 'export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y --no-install-recommends acl age ca-certificates curl gcc git iproute2 jq libc6-dev openssh-client openssl python3 python3-pil python3-venv ripgrep sudo tar xz-utils docker.io' </dev/null
  incus exec "$instance" -- systemctl restart docker </dev/null
  incus exec "$instance" -- bash -lc 'id pixelqual >/dev/null 2>&1 || useradd --uid 1999 --create-home --shell /bin/bash pixelqual; usermod -aG docker pixelqual; printf "%s\n" "pixelqual ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/pixelqual; chmod 0440 /etc/sudoers.d/pixelqual; install -d -o pixelqual -g pixelqual -m 700 /home/pixelqual/pixel' </dev/null
  node_version=$(jq -er '.nodeRuntime.version' "$manifest")
  node_url=$(jq -er '.nodeRuntime.url' "$manifest")
  node_sha256=$(jq -er '.nodeRuntime.sha256' "$manifest")
  incus exec "$instance" -- bash -lc "set -euo pipefail; curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 '$node_url' -o /root/node.tar.xz; echo '$node_sha256  /root/node.tar.xz' | sha256sum -c -; mkdir -p /opt/node; tar -xJf /root/node.tar.xz --strip-components=1 -C /opt/node; ln -sf /opt/node/bin/node /usr/local/bin/node; ln -sf /opt/node/bin/npm /usr/local/bin/npm; ln -sf /opt/node/bin/npx /usr/local/bin/npx; test \"\$(node --version)\" = 'v$node_version'" </dev/null
  incus exec "$instance" -- tar -C /home/pixelqual/pixel -xf - < "$stage/source.tar"
  incus exec "$instance" -- chown -R pixelqual:pixelqual /home/pixelqual/pixel </dev/null
  incus exec "$instance" -- install -d -o root -g root -m 0755 /opt/pixel-qualification-source </dev/null
  incus exec "$instance" -- tar -C /opt/pixel-qualification-source -xf - < "$stage/source.tar"
  incus exec "$instance" -- chown -R root:root /opt/pixel-qualification-source </dev/null
  incus exec "$instance" -- chmod -R go-w /opt/pixel-qualification-source </dev/null

  lane_evidence="$evidence/$lane"
  install -d -m 700 "$lane_evidence"
  echo "[host-matrix:$lane] qualify"
  incus exec "$instance" -- runuser --user pixelqual -- env -i \
    HOME=/home/pixelqual USER=pixelqual LOGNAME=pixelqual LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/opt/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    python3 /home/pixelqual/pixel/scripts/supported-host-systemd-probe.py \
      --source /home/pixelqual/pixel \
      --immutable-source /opt/pixel-qualification-source \
      --evidence /home/pixelqual/evidence \
      --lane "$lane" \
      --source-commit "$source_commit" \
      --source-tree "$source_tree" \
      --expected-os-id "$os_id" \
      --expected-os-version "$os_version" </dev/null || status=$?
  if incus exec "$instance" -- test -d /home/pixelqual/evidence </dev/null; then
    incus exec "$instance" -- bash -lc 'if find /home/pixelqual/evidence -mindepth 1 \( -type l -o \( ! -type f ! -type d \) \) -print -quit | grep -q .; then exit 1; fi' </dev/null
    incus exec "$instance" -- tar -C /home/pixelqual/evidence -cf - . </dev/null | tar --no-same-owner --no-same-permissions -C "$lane_evidence" -xf -
    ! find "$lane_evidence" -type l -print -quit | grep -q . || { echo "Guest evidence contained a link" >&2; return 1; }
    chmod -R go-rwx "$lane_evidence"
  fi
  [[ $status == 0 ]] || { echo "$lane qualification failed" >&2; return "$status"; }
  jq -e --arg lane "$lane" --arg commit "$source_commit" --arg tree "$source_tree" '
    .status == "pass" and .lane == $lane and .sourceCommit == $commit and .sourceTree == $tree
    and .synthetic.providerCalls == 0 and .synthetic.credentialInputs == 0
    and (.checks | to_entries | all(.value == "pass"))
  ' "$lane_evidence/supported-host-systemd-lane.json" >/dev/null
  echo "[host-matrix:$lane] pass"
}

launch_lane ubuntu-24.04-systemd ubuntu2404Vm ubuntu 24.04
launch_lane debian-12-systemd debian12Vm debian 12

python3 - "$evidence" "$source_commit" "$source_tree" "$manifest_sha256" <<'PY'
import hashlib, json, pathlib, sys
root, commit, tree, manifest_sha = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
lanes = []
for path in sorted(root.glob("*/supported-host-systemd-lane.json")):
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("sourceCommit") != commit or value.get("sourceTree") != tree or value.get("releaseManifestSha256") != manifest_sha:
        raise SystemExit("supported-host evidence identity mismatch")
    lanes.append({
        "id": value["lane"],
        "status": value["status"],
        "os": value["environment"]["os"],
        "checks": value["checks"],
        "evidenceSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
if [item["id"] for item in lanes] != ["debian-12-systemd", "ubuntu-24.04-systemd"] or any(item["status"] != "pass" for item in lanes):
    raise SystemExit("supported-host systemd matrix is incomplete")
value = {
    "$schema": "./schemas/supported-host-systemd-evidence-v1.schema.json",
    "schemaVersion": 1,
    "operation": "pixel-supported-host-systemd-matrix",
    "status": "pass",
    "sourceCommit": commit,
    "sourceTree": tree,
    "releaseManifestSha256": manifest_sha,
    "lanes": lanes,
    "privacy": {"providerCalls": 0, "credentialInputs": 0, "productionDeploymentsTouched": 0},
}
output = root / "supported-host-systemd-matrix.json"
output.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
output.chmod(0o600)
print(json.dumps({"status": "pass", "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, sort_keys=True))
PY
