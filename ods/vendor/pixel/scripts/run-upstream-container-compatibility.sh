#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

usage() {
  echo "Usage: scripts/run-upstream-container-compatibility.sh --quarantine ABSOLUTE_PATH --evidence ABSOLUTE_PATH --trusted-base-commit COMMIT" >&2
  exit 2
}

quarantine=""
evidence=""
trusted_base_commit=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quarantine) quarantine=${2:-}; shift 2 ;;
    --evidence) evidence=${2:-}; shift 2 ;;
    --trusted-base-commit) trusted_base_commit=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$quarantine" == /* && "$evidence" == /* && "$quarantine" != / && "$evidence" != / ]] || usage
[[ "$trusted_base_commit" =~ ^[0-9a-f]{40}$ ]] || usage
case "$quarantine/" in "$ROOT/"*) echo "Quarantine must be outside the source repository" >&2; exit 2 ;; esac
case "$evidence/" in "$ROOT/"*) echo "Evidence must be outside the source repository" >&2; exit 2 ;; esac
for command in docker git jq; do command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }; done

manifest="$ROOT/RELEASE-MANIFEST.json"
intake_commit=$(jq -er '.upstreamIntake.sourceCommit' "$manifest")
git -C "$ROOT" merge-base --is-ancestor "$intake_commit" HEAD
git -C "$ROOT" cat-file -e "$trusted_base_commit^{commit}"
git -C "$ROOT" merge-base --is-ancestor "$intake_commit" "$trusted_base_commit" || {
  echo "Intake source commit is not part of the trusted base history" >&2
  exit 1
}
supported_manifest=$(mktemp)
cleanup() {
  [[ "$supported_manifest" == /tmp/* && -f "$supported_manifest" ]] && rm -f -- "$supported_manifest"
}
trap cleanup EXIT
git -C "$ROOT" show "$intake_commit:RELEASE-MANIFEST.json" > "$supported_manifest"
jq -e --slurpfile supported "$supported_manifest" '.nodeRuntime == $supported[0].nodeRuntime' "$manifest" >/dev/null
image=$(jq -er '.baseImage' "$supported_manifest")
[[ "$image" =~ ^debian:bookworm-slim@sha256:[0-9a-f]{64}$ ]] || { echo "Supported Debian qualification image is not immutable" >&2; exit 1; }
node_version=$(jq -er '.nodeRuntime.version' "$supported_manifest")
node_url=$(jq -er '.nodeRuntime.url' "$supported_manifest")
node_sha256=$(jq -er '.nodeRuntime.sha256' "$supported_manifest")

install -d -m 700 "$quarantine" "$evidence" "$evidence/work"
docker pull "$image" >/dev/null
docker run --rm \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777 \
  --tmpfs /var/tmp:rw,nosuid,nodev,noexec,mode=1777 \
  --tmpfs /var/lib/apt/lists:rw,nosuid,nodev,noexec,mode=0755 \
  --tmpfs /var/cache/apt:rw,nosuid,nodev,noexec,mode=0755 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  -e PIXEL_NODE_VERSION="$node_version" \
  -e PIXEL_NODE_URL="$node_url" \
  -e PIXEL_NODE_SHA256="$node_sha256" \
  -v "$ROOT:/src:ro" \
  -v "$quarantine:/qualification/quarantine:rw" \
  -v "$evidence:/qualification/evidence:rw" \
  "$image" bash -euo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends ca-certificates curl git jq python3 xz-utils
    curl --fail --silent --show-error --location --proto "=https" --tlsv1.2 "$PIXEL_NODE_URL" -o /tmp/node.tar.xz
    echo "$PIXEL_NODE_SHA256  /tmp/node.tar.xz" | sha256sum -c -
    install -d -m 755 /opt/node
    tar -xJf /tmp/node.tar.xz --strip-components=1 -C /opt/node
    test "$(/opt/node/bin/node --version)" = "v$PIXEL_NODE_VERSION"
    export PATH=/opt/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    git config --global --add safe.directory /src
    cd /src
    ./pixel upstream diff --quarantine /qualification/quarantine --evidence /qualification/evidence/contract
    jq -e '\''.status == "compatible" and (.blockers | length) == 0'\'' /qualification/evidence/contract/contract-diff.json >/dev/null
    ./pixel upstream qualify --mode quick --quarantine /qualification/quarantine --evidence /qualification/evidence/runtime --work /qualification/evidence/work/runtime
  '
jq -e '.status == "pass" and .environment.os.id == "debian" and .environment.os.version == "12"' "$evidence/runtime/runtime-qualification.json" >/dev/null
