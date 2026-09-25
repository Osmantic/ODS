#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for command in docker git mktemp node; do command -v "$command" >/dev/null || { echo "Missing release-gate command: $command" >&2; exit 1; }; done

read_manifest() {
  node -p "require(process.argv[1])$1" "$ROOT/RELEASE-MANIFEST.json"
}

image=$(read_manifest '.baseImage')
node_url=$(read_manifest '.nodeRuntime.url')
node_sha256=$(read_manifest '.nodeRuntime.sha256')
node_version=$(read_manifest '.nodeRuntime.version')
[[ -z $(git -C "$ROOT" status --porcelain=v1 --untracked-files=no) ]] || {
  echo "Debian release gate requires a clean tracked tree" >&2
  exit 1
}
if [[ "${PIXEL_LIVE_DOCKER:-0}" == "1" ]]; then
  (cd "$ROOT" && node --test tests/work-capability-image-live.mjs)
fi
staging_root=$(mktemp -d "${TMPDIR:-/tmp}/pixel-debian-gate.XXXXXXXX")
trap 'rm -rf -- "$staging_root"' EXIT
git clone --quiet --no-local "$ROOT" "$staging_root/source"
mount_root="$staging_root/source"
if [[ -n ${MSYSTEM:-} ]]; then
  command -v cygpath >/dev/null || { echo "Git Bash path conversion requires cygpath" >&2; exit 1; }
  mount_root=$(cygpath -w "$mount_root")
fi

MSYS_NO_PATHCONV=1 docker run --rm \
  -e CI=1 \
  -e PIXEL_CI_NODE_URL="$node_url" \
  -e PIXEL_CI_NODE_SHA256="$node_sha256" \
  -e PIXEL_CI_NODE_VERSION="$node_version" \
  --mount "type=bind,source=$mount_root,target=/input,readonly" \
  "$image" \
  bash -lc '
    set -euo pipefail
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends age ca-certificates curl git jq openssh-client python3 python3-jsonschema python3-pil ripgrep shellcheck sudo systemd xz-utils >/dev/null
    curl --fail --silent --show-error --location "$PIXEL_CI_NODE_URL" --output /tmp/node.tar.xz
    printf "%s  %s\n" "$PIXEL_CI_NODE_SHA256" /tmp/node.tar.xz | sha256sum -c -
    install -d -m 755 /opt/pixel-node
    tar -xJf /tmp/node.tar.xz --strip-components=1 -C /opt/pixel-node
    [[ ! -e /usr/bin/node && ! -L /usr/bin/node ]] || { echo "Debian release gate requires /usr/bin/node to be absent before installing the verified runtime" >&2; exit 1; }
    install -o root -g root -m 0755 /opt/pixel-node/bin/node /usr/bin/node
    [[ -f /usr/bin/node && ! -L /usr/bin/node ]] || { echo "Verified Node runtime is not a regular non-symlink /usr/bin/node" >&2; exit 1; }
    [[ $(stat -c %u /usr/bin/node) == 0 && $(stat -c %g /usr/bin/node) == 0 && $(stat -c %a /usr/bin/node) == 755 && $(stat -c %h /usr/bin/node) == 1 ]] || { echo "Verified Node runtime does not satisfy the guardian ownership, mode, and link-count contract" >&2; exit 1; }
    cmp -s /opt/pixel-node/bin/node /usr/bin/node || { echo "Installed /usr/bin/node differs from the verified runtime" >&2; exit 1; }
    [[ $(/usr/bin/node --version) == "v$PIXEL_CI_NODE_VERSION" ]] || { echo "Installed /usr/bin/node version does not match the release manifest" >&2; exit 1; }
    cp -a -- /input /src
    useradd --create-home --shell /bin/bash pixel-ci
    chown -R pixel-ci:pixel-ci /src
    chmod -R go-w /src
    [[ $(sudo -u pixel-ci -H env PATH="/usr/bin:/opt/pixel-node/bin:$PATH" node -p "process.execPath") == /usr/bin/node ]] || { echo "Release tests are not executing through the production Node trust path" >&2; exit 1; }
    sudo -u pixel-ci -H env PATH="/usr/bin:/opt/pixel-node/bin:$PATH" CI=1 bash /src/scripts/ci-release-gate-inner.sh
  '
