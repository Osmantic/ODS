#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

usage() {
  echo "Usage: scripts/run-upstream-runtime-matrix.sh --quarantine ABSOLUTE_PATH --evidence ABSOLUTE_PATH [--containers-only|--systemd-only] [--observation-seconds N]" >&2
  exit 2
}

quarantine=""
evidence=""
containers_only=0
systemd_only=0
observation_seconds=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quarantine) quarantine=${2:-}; shift 2 ;;
    --evidence) evidence=${2:-}; shift 2 ;;
    --containers-only) containers_only=1; shift ;;
    --systemd-only) systemd_only=1; shift ;;
    --observation-seconds) observation_seconds=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[[ $containers_only != 1 || $systemd_only != 1 ]] || usage
[[ "$observation_seconds" =~ ^[0-9]+$ && $observation_seconds -le 7200 ]] || usage
[[ $observation_seconds == 0 || $systemd_only == 1 ]] || usage
[[ "$quarantine" == /* && "$evidence" == /* ]] || usage
[[ "$quarantine" != / && "$evidence" != / ]] || { echo "Matrix paths cannot be filesystem roots" >&2; exit 1; }
case "$quarantine/" in "$ROOT/"*) echo "Quarantine must be outside the source repository" >&2; exit 1 ;; esac
case "$evidence/" in "$ROOT/"*) echo "Evidence must be outside the source repository" >&2; exit 1 ;; esac
for command in incus git jq python3 sha256sum tar timeout; do command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }; done

manifest="$ROOT/RELEASE-MANIFEST.json"
intake_source_commit=$(jq -er '.upstreamIntake.sourceCommit' "$manifest")
qualification_source_commit=$(git -C "$ROOT" rev-parse HEAD)
channel=$(jq -er '.upstreamIntake.channel' "$manifest")
candidate_version=$(jq -er '.openclaw' "$manifest")
node_version=$(jq -er '.nodeRuntime.version' "$manifest")
node_url=$(jq -er '.nodeRuntime.url' "$manifest")
node_sha256=$(jq -er '.nodeRuntime.sha256' "$manifest")
git -C "$ROOT" merge-base --is-ancestor "$intake_source_commit" HEAD || { echo "Prepared source commit is not an ancestor of HEAD" >&2; exit 1; }

stage=$(mktemp -d)
instances=()
cleanup() {
  local instance
  for instance in "${instances[@]}"; do
    [[ "$instance" =~ ^pixel-q-[a-z0-9-]+$ ]] || continue
    timeout 60 incus delete --force "$instance" >/dev/null 2>&1 || true
  done
  [[ "$stage" == /tmp/* && -d "$stage" ]] && rm -rf -- "$stage"
}
trap cleanup EXIT

git -C "$ROOT" show "$intake_source_commit:RELEASE-MANIFEST.json" > "$stage/supported-manifest.json"
jq -e --slurpfile supported "$stage/supported-manifest.json" '.qualificationImages == $supported[0].qualificationImages' "$manifest" >/dev/null || {
  echo "Candidate changed the pinned qualification guests outside the upstream package intake" >&2
  exit 1
}
supported_version=$(jq -er '.openclaw' "$stage/supported-manifest.json")
[[ -d "$quarantine/$channel/$candidate_version" && -d "$quarantine/supported/$supported_version" ]] || {
  echo "Verified candidate and supported quarantine directories are required; run ./pixel upstream diff first" >&2
  exit 1
}

mkdir -p "$evidence"
chmod 700 "$evidence"
run_id=$(git -C "$ROOT" rev-parse --short=7 HEAD)-$$

ensure_image() {
  local key=$1 fingerprint metadata
  fingerprint=$(jq -er --arg key "$key" '.qualificationImages[$key].fingerprint' "$manifest")
  if ! incus image info "$fingerprint" >/dev/null 2>&1; then
    echo "[matrix:image] fetch exact $key $fingerprint" >&2
    timeout 600 incus image copy "images:$fingerprint" local:
  fi
  metadata=$(incus query "/1.0/images/$fingerprint")
  jq -e --arg key "$key" --arg fingerprint "$fingerprint" --slurpfile release "$manifest" '
    .fingerprint == $fingerprint
    and .type == $release[0].qualificationImages[$key].type
    and .properties.os == $release[0].qualificationImages[$key].os
    and .properties.release == $release[0].qualificationImages[$key].release
    and .architecture == $release[0].qualificationImages[$key].architecture
  ' <<< "$metadata" >/dev/null || { echo "Pinned Incus image identity mismatch: $key" >&2; exit 1; }
  printf '%s\n' "$fingerprint"
}

ubuntu_container_image=$(ensure_image ubuntu2404Container)
debian_container_image=$(ensure_image debian12Container)
ubuntu_vm_image=$(ensure_image ubuntu2404Vm)

launch_guest() {
  local lane=$1 image=$2 mode=$3 instance
  echo "[matrix:$lane] launch ($mode)"
  instance="pixel-q-${lane}-${run_id}"
  [[ "$instance" =~ ^pixel-q-[a-z0-9-]+$ ]] || { echo "Unsafe generated instance name" >&2; return 1; }
  if incus info "$instance" >/dev/null 2>&1; then
    echo "Refusing to replace an existing Incus instance: $instance" >&2
    return 1
  fi
  instances+=("$instance")
  if [[ "$mode" == systemd ]]; then
    timeout 180 incus launch "$image" "$instance" --vm -c limits.cpu=4 -c limits.memory=8GiB || {
      echo "Incus could not create $instance within 180 seconds; qualification infrastructure is unhealthy" >&2
      return 1
    }
  else
    timeout 180 incus launch "$image" "$instance" -c security.nesting=true -c security.syscalls.intercept.mknod=true -c security.syscalls.intercept.setxattr=true || {
      echo "Incus could not create $instance within 180 seconds; qualification infrastructure is unhealthy" >&2
      return 1
    }
  fi

  local ready=0
  for _ in $(seq 1 90); do
    if incus exec "$instance" -- systemctl is-system-running --wait >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
  done
  [[ $ready == 1 ]] || { echo "$instance did not finish booting" >&2; return 1; }

  echo "[matrix:$lane] provision"
  incus exec "$instance" -- bash -lc 'export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y --no-install-recommends ca-certificates curl xz-utils git jq python3 openssl docker.io'
  if [[ "$mode" != systemd ]]; then
    incus exec "$instance" -- bash -lc 'install -d -m 700 /etc/docker; printf "%s\n" "{\"storage-driver\":\"vfs\"}" > /etc/docker/daemon.json; systemctl restart docker'
  else
    incus exec "$instance" -- systemctl restart docker
  fi
  incus exec "$instance" -- bash -lc "set -euo pipefail; curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 '$node_url' -o /root/node.tar.xz; echo '$node_sha256  /root/node.tar.xz' | sha256sum -c -; mkdir -p /opt/node; tar -xJf /root/node.tar.xz --strip-components=1 -C /opt/node; test \"\$(/opt/node/bin/node --version)\" = 'v$node_version'"
  echo "[matrix:$lane] transfer verified source and packages"
  incus exec "$instance" -- mkdir -p /root/pixel /root/quarantine
  tar -C "$ROOT" \
    --exclude=.git --exclude=.env --exclude=.generated --exclude=.runtime --exclude=dist \
    --exclude=node_modules --exclude=__pycache__ --exclude='*.pyc' -cf - . \
    | incus exec "$instance" -- tar -C /root/pixel -xf -
  incus file push "$stage/supported-manifest.json" "$instance/root/supported-manifest.json"
  tar -C "$quarantine" -cf - "$channel/$candidate_version" "supported/$supported_version" \
    | incus exec "$instance" -- tar -C /root/quarantine -xf -

  local lane_evidence="$evidence/$lane"
  mkdir -p "$lane_evidence"
  chmod 700 "$lane_evidence"
  local status=0
  echo "[matrix:$lane] qualify"
  incus exec "$instance" -- env PATH=/opt/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    python3 /root/pixel/scripts/upstream-runtime-probe.py \
      --source /root/pixel \
      --candidate-manifest /root/pixel/RELEASE-MANIFEST.json \
      --supported-manifest /root/supported-manifest.json \
      --quarantine /root/quarantine \
      --work /var/lib/pixel-qualification-work \
      --evidence /root/evidence \
      --qualification-source-commit "$qualification_source_commit" \
      --observation-seconds "$observation_seconds" \
      --mode "$mode" || status=$?
  if incus exec "$instance" -- test -d /root/evidence; then
    incus exec "$instance" -- tar -C /root/evidence -cf - . | tar -C "$lane_evidence" -xf -
  fi
  [[ $status == 0 ]] || { echo "$lane qualification failed" >&2; return "$status"; }
  jq -e --arg lane "$lane" --arg mode "$mode" '.status == "pass" and .mode == $mode and .environment.os.id != null' "$lane_evidence/runtime-qualification.json" >/dev/null
  echo "[matrix:$lane] pass"
}

if [[ $systemd_only != 1 ]]; then
  launch_guest ubuntu-24-container "$ubuntu_container_image" quick
  launch_guest debian-12-container "$debian_container_image" quick
fi
if [[ $containers_only != 1 ]]; then
  launch_guest ubuntu-24-vm "$ubuntu_vm_image" systemd
fi

expected=3
[[ $containers_only == 1 ]] && expected=2
[[ $systemd_only == 1 ]] && expected=1
python3 - "$evidence" "$qualification_source_commit" "$intake_source_commit" "$candidate_version" "$expected" <<'PY'
import hashlib, json, pathlib, sys
root, source_commit, intake_source_commit, candidate, expected = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
lanes = []
for path in sorted(root.glob("*/runtime-qualification.json")):
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("sourceCommit") != source_commit:
        raise SystemExit(f"runtime qualification source mismatch: {path}")
    lanes.append({
        "lane": path.parent.name,
        "mode": value["mode"],
        "os": value["environment"]["os"],
        "node": value["environment"]["node"],
        "status": value["status"],
        "candidateManifestSha256": value["candidateManifestSha256"],
        "evidenceBindingSha256": value["evidenceBindingSha256"],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
if len(lanes) != expected or any(item["status"] != "pass" for item in lanes):
    raise SystemExit("runtime qualification matrix is incomplete")
if len({item["candidateManifestSha256"] for item in lanes}) != 1:
    raise SystemExit("runtime qualification lanes disagree on candidate identity")
report = {"schemaVersion": 1, "operation": "upstream-runtime-matrix", "status": "pass", "sourceCommit": source_commit, "intakeSourceCommit": intake_source_commit, "candidate": candidate, "lanes": lanes}
output = root / "runtime-matrix.json"
output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
output.chmod(0o600)
print(json.dumps({"status": "pass", "evidence": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, sort_keys=True))
PY
