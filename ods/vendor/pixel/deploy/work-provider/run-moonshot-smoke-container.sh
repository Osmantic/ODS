#!/usr/bin/env bash
set -euo pipefail
umask 077
export LC_ALL=C

fail() { printf '%s\n' 'Pixel isolated Moonshot smoke launcher failed closed.' >&2; exit 70; }
[[ $# -eq 6 ]] || fail
policy_path=$1
policy_sha256=$2
credential_directory=${3%/}
ledger_root=${4%/}
proxy_image=$5
worker_image=$6

for command in docker realpath stat; do command -v "$command" >/dev/null || fail; done
for path in "$policy_path" "$credential_directory" "$ledger_root"; do
  [[ "$path" == /* && "$path" != / && "$path" != *','* && "$path" != *$'\n'* && "$path" != *$'\r'* ]] || fail
  [[ "$(realpath -e -- "$path")" == "$path" ]] || fail
done
[[ "$policy_sha256" =~ ^[a-f0-9]{64}$ ]] || fail
[[ "$proxy_image" =~ ^sha256:[a-f0-9]{64}$ && "$worker_image" =~ ^sha256:[a-f0-9]{64}$ ]] || fail
[[ "$(stat -c '%u:%a:%h:%F' -- "$policy_path")" == "$EUID:600:1:regular file" ]] || fail
[[ "$(stat -c '%u:%a:%F' -- "$credential_directory")" == "$EUID:700:directory" ]] || fail
credential_file=
for candidate in moonshot-kimi-key provider-key; do
  candidate_path="$credential_directory/$candidate"
  if [[ -e "$candidate_path" || -L "$candidate_path" ]]; then
    [[ -z "$credential_file" ]] || fail
    [[ "$(stat -c '%u:%a:%h:%F' -- "$candidate_path")" == "$EUID:600:1:regular file" ]] || fail
    credential_file=$candidate
  fi
done
[[ -n "$credential_file" ]] || fail
[[ "$(stat -c '%u:%a:%F' -- "$ledger_root")" == "$EUID:700:directory" ]] || fail
[[ "$(docker image inspect --format '{{.Id}}' "$proxy_image")" == "$proxy_image" ]] || fail
[[ "$(docker image inspect --format '{{.Id}}' "$worker_image")" == "$worker_image" ]] || fail

suffix="$PPID-$$"
internal_network="pixel-provider-internal-$suffix"
egress_network="pixel-provider-egress-$suffix"
proxy_container="pixel-provider-proxy-$suffix"
worker_container="pixel-provider-worker-$suffix"
created_internal=0
created_egress=0
created_proxy=0
cleanup() {
  case "$worker_container" in pixel-provider-worker-*) docker rm --force "$worker_container" >/dev/null 2>&1 || true ;; esac
  if [[ $created_proxy -eq 1 ]]; then case "$proxy_container" in pixel-provider-proxy-*) docker rm --force "$proxy_container" >/dev/null 2>&1 || true ;; esac; fi
  if [[ $created_egress -eq 1 ]]; then case "$egress_network" in pixel-provider-egress-*) docker network rm "$egress_network" >/dev/null 2>&1 || true ;; esac; fi
  if [[ $created_internal -eq 1 ]]; then case "$internal_network" in pixel-provider-internal-*) docker network rm "$internal_network" >/dev/null 2>&1 || true ;; esac; fi
}
trap cleanup EXIT HUP INT TERM

docker network inspect "$internal_network" >/dev/null 2>&1 && fail
docker network inspect "$egress_network" >/dev/null 2>&1 && fail
docker network create --internal --driver bridge "$internal_network" >/dev/null
created_internal=1
docker network create --driver bridge "$egress_network" >/dev/null
created_egress=1

docker create --name "$proxy_container" --network "$internal_network" --network-alias pixel-provider-egress \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 64 --memory 128m --cpus 0.5 \
  --mount "type=bind,src=$policy_path,dst=/etc/pixel-provider/policy.json,readonly" \
  --env "PIXEL_WORK_PROVIDER_POLICY_SHA256=$policy_sha256" "$proxy_image" >/dev/null
created_proxy=1
docker network connect "$egress_network" "$proxy_container"
docker start "$proxy_container" >/dev/null

ready=0
for _ in $(seq 1 20); do
  if docker exec "$proxy_container" /opt/node/bin/node -e 'const n=require("node:net"),s=n.connect(3128,"127.0.0.1");s.once("connect",()=>{s.destroy();process.exit(0)});s.once("error",()=>process.exit(1));setTimeout(()=>process.exit(1),100).unref()' >/dev/null 2>&1; then ready=1; break; fi
  sleep 0.1
done
[[ $ready -eq 1 ]] || fail

docker run --rm --name "$worker_container" --network "$internal_network" \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 64 --memory 256m --cpus 1 \
  --mount "type=bind,src=$policy_path,dst=/run/pixel-policy/policy.json,readonly" \
  --mount "type=bind,src=$credential_directory,dst=/run/pixel-credential,readonly" \
  --mount "type=bind,src=$ledger_root,dst=/state/ledger" \
  --env "PIXEL_WORK_PROVIDER_POLICY_SHA256=$policy_sha256" \
  --env "PIXEL_WORK_PROVIDER_CREDENTIAL_FILE=$credential_file" "$worker_image"
