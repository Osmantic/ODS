#!/usr/bin/env bash
set -euo pipefail

# Phase 05 may choose sudo docker until a fresh docker-group membership takes
# effect. Phase 06 must inspect that same daemon without weakening the Docker
# Desktop shared-mount requirement or accepting a remote Docker endpoint.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
function_source="$(sed -n '/^_phase06_pixel_runtime_layout() {/,/^}/p' \
    "$ROOT/installers/phases/06-directories.sh")"
[[ -n "$function_source" ]] || { echo 'Missing Pixel runtime layout helper' >&2; exit 1; }
eval "$function_source"

scratch="$(mktemp -d /tmp/ods-pixel-wsl-layout.XXXXXX)"
trap '[[ "$scratch" == /tmp/ods-pixel-wsl-layout.* ]] && rm -rf -- "$scratch"' EXIT
mkdir -p "$scratch/bin" "$scratch/wsl"
printf '5.15.0-microsoft-standard-WSL2\n' > "$scratch/osrelease-wsl"
printf '6.8.0-generic\n' > "$scratch/osrelease-linux"

cat > "$scratch/bin/docker" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s:%s\n' "${MOCK_VIA_SUDO:-direct}" "${1:-}" >> "$MOCK_CALLS"
if [[ "${MOCK_DIRECT_DENY:-false}" == true && "${MOCK_VIA_SUDO:-direct}" != sudo ]]; then
    echo 'permission denied while trying to connect to the Docker daemon socket' >&2
    exit 1
fi
[[ "${MOCK_DAEMON_AVAILABLE:-true}" == true ]] || exit 1
case "${1:-}" in
    context)
        [[ "${2:-}" == inspect ]] || exit 2
        printf '%s\n' "${MOCK_ENDPOINT:-unix:///var/run/docker.sock}"
        ;;
    info)
        [[ "${MOCK_INFO_FAIL:-false}" != true ]] || exit 1
        printf '%s\n' "${MOCK_DOCKER_OS:-Docker Engine - Community}"
        ;;
    *) exit 2 ;;
esac
MOCK
cat > "$scratch/bin/sudo" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == docker ]] || exit 2
shift
MOCK_VIA_SUDO=sudo exec docker "$@"
MOCK
cat > "$scratch/bin/findmnt" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "${MOCK_PROPAGATION:-shared}"
MOCK
chmod 0700 "$scratch/bin/docker" "$scratch/bin/sudo" "$scratch/bin/findmnt"
export PATH="$scratch/bin:$PATH" MOCK_CALLS="$scratch/calls"

pass_count=0
pass() { pass_count=$((pass_count + 1)); printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

expect_layout() {
    local label="$1" ingress="$2" preview="$3" propagation="$4"
    : > "$MOCK_CALLS"
    _phase06_pixel_runtime_layout "$scratch/osrelease-wsl" "$scratch/wsl" \
        || fail "$label: unexpected preflight failure"
    [[ "$PIXEL_INGRESS_RUNTIME_DIR_VALUE" == "$ingress" \
        && "$PIXEL_PREVIEW_RUNTIME_DIR_VALUE" == "$preview" \
        && "$PIXEL_RUNTIME_BIND_PROPAGATION_VALUE" == "$propagation" ]] \
        || fail "$label: incorrect runtime mount layout"
    pass "$label"
}

expect_rejection() {
    local label="$1"
    : > "$MOCK_CALLS"
    if _phase06_pixel_runtime_layout "$scratch/osrelease-wsl" "$scratch/wsl"; then
        fail "$label: unsafe runtime accepted"
    fi
    pass "$label"
}

export MOCK_DIRECT_DENY=true MOCK_DAEMON_AVAILABLE=true
export MOCK_DOCKER_OS='Docker Engine - Community' MOCK_ENDPOINT=unix:///var/run/docker.sock
export MOCK_PROPAGATION=private
DOCKER_CMD='sudo docker'
expect_layout 'native WSL Engine uses resolved sudo docker after group lag' \
    /run/ods-pixel /run/ods-pixel-preview rprivate
grep -Fxq 'sudo:context' "$MOCK_CALLS" && grep -Fxq 'sudo:info' "$MOCK_CALLS" \
    || fail 'resolved sudo Docker command was not used for both probes'
pass 'resolved command used for context and daemon probes'

DOCKER_CMD=docker
expect_rejection 'unprivileged Docker socket denial fails closed'

export MOCK_DIRECT_DENY=false DOCKER_HOST=unix:///var/run/docker.sock
expect_layout 'explicit local Unix Docker host uses native WSL mounts' \
    /run/ods-pixel /run/ods-pixel-preview rprivate
unset DOCKER_HOST

export MOCK_DIRECT_DENY=false MOCK_DOCKER_OS='Docker Desktop' MOCK_PROPAGATION=shared
expect_layout 'Docker Desktop uses shared WSL bridge' \
    /mnt/wsl/ods-portal-runtime/ingress \
    /mnt/wsl/ods-portal-runtime/preview rshared

DOCKER_CMD='sudo docker'
export MOCK_DIRECT_DENY=true
expect_layout 'Docker Desktop via resolved sudo command still uses shared bridge' \
    /mnt/wsl/ods-portal-runtime/ingress \
    /mnt/wsl/ods-portal-runtime/preview rshared
DOCKER_CMD=docker
export MOCK_DIRECT_DENY=false

export MOCK_PROPAGATION=private
expect_rejection 'Docker Desktop non-shared WSL mount fails closed'

export MOCK_DOCKER_OS='Docker Engine - Community' MOCK_ENDPOINT=tcp://10.0.0.2:2376
expect_rejection 'remote Docker context fails closed'

export DOCKER_HOST=unix:///var/run/docker.sock
expect_rejection 'local Unix override cannot mask a remote Docker context'
unset DOCKER_HOST

export MOCK_ENDPOINT=unix:///var/run/docker.sock DOCKER_HOST=tcp://10.0.0.2:2376
expect_rejection 'remote Docker host override fails closed'
unset DOCKER_HOST

export MOCK_INFO_FAIL=true
expect_rejection 'Docker info failure after local context probe fails closed'
unset MOCK_INFO_FAIL

export MOCK_DAEMON_AVAILABLE=false
expect_rejection 'unavailable local daemon fails closed'

export MOCK_DAEMON_AVAILABLE=true
DOCKER_CMD='other docker'
expect_rejection 'unrecognized resolved Docker command fails closed'

# Non-WSL Linux keeps the ordinary local paths and does not need a Docker
# Desktop bridge probe; the installer already validates Docker in Phase 05.
DOCKER_CMD=docker
: > "$MOCK_CALLS"
_phase06_pixel_runtime_layout "$scratch/osrelease-linux" "$scratch/wsl" \
    || fail 'ordinary Linux layout rejected'
[[ "$PIXEL_INGRESS_RUNTIME_DIR_VALUE" == /run/ods-pixel \
    && "$PIXEL_PREVIEW_RUNTIME_DIR_VALUE" == /run/ods-pixel-preview \
    && "$PIXEL_RUNTIME_BIND_PROPAGATION_VALUE" == rprivate \
    && ! -s "$MOCK_CALLS" ]] || fail 'ordinary Linux unexpectedly probed Docker'
pass 'ordinary Linux layout unchanged'

grep -Fq 'Pixel could not verify the local WSL Docker daemon or Docker Desktop shared runtime mount' \
    "$ROOT/installers/phases/06-directories.sh" \
    || fail 'Phase 06 user-facing error omits daemon or Desktop mount failure'
pass 'Phase 06 error names both possible WSL preflight causes'

printf 'Results: %s passed, 0 failed\n' "$pass_count"
