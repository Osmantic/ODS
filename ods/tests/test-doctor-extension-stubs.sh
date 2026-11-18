#!/usr/bin/env bash
# Exercise production diagnostics, including the shipped GPU-only ComfyUI stub.
# shellcheck disable=SC2034
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if (( BASH_VERSINFO[0] < 4 )); then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$candidate" ]]; then exec "$candidate" "$0" "$@"; fi
    done
    echo 'Bash 4+ is required' >&2
    exit 1
fi
extract() {
    awk -v name="$1" '$0 ~ "^" name "[(][)]" { active=1 }
        active {print} active && /^}/ {exit}' "$root/scripts/ods-doctor.sh"
}
eval "$(extract _doctor_unsupported_extension_stub)"
eval "$(extract collect_extension_diagnostics)"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
ROOT_DIR="$fixture"
PYTHON_CMD=python3
DOCKER_DAEMON=true
GPU_BACKEND=apple
declare -a SERVICE_IDS=(comfyui)
declare -A SERVICE_CATEGORIES=([comfyui]=optional)
declare -A SERVICE_COMPOSE=([comfyui]="$fixture/compose.yaml")
declare -A SERVICE_CONTAINERS=([comfyui]=ods-comfyui)
declare -A SERVICE_PORTS=([comfyui]=8188) SERVICE_HEALTH=([comfyui]=/)
declare -A SERVICE_GPU_BACKENDS=([comfyui]='amd nvidia')
declare -A SERVICE_DEPENDS=() SERVICE_STARTUP_CHECKS=() SERVICE_SOCKET_ONLY=()
state=missing
docker() {
    [[ "$1" == inspect && "$state" != missing ]] || return 1
    printf '%s\n' "$state"
}
curl() { return 0; }
assert_diag() {
    local label="$1" predicate="$2" result
    result=$(collect_extension_diagnostics)
    if ! jq -e "$predicate" <<<"$result" >/dev/null; then
        printf '[FAIL] %s: %s\n' "$label" "$result" >&2
        exit 1
    fi
    printf '[PASS] %s\n' "$label"
}
not_applicable='.[0] | .health_status == "not_applicable" and .container_state == "not_found" and .applicability_reason == "unsupported_backend_stub" and .issues == []'
missing='.[0] | .health_status != "not_applicable" and (.issues | index("container_not_running") != null)'
cp "$root/extensions/services/comfyui/"compose*.yaml "$fixture/"
assert_diag 'shipped ComfyUI scaffold is unavailable on Apple' "$not_applicable"
GPU_BACKEND=cpu
assert_diag 'GPU-only scaffold is unavailable on CPU' "$not_applicable"
GPU_BACKEND=nvidia
assert_diag 'supported NVIDIA service still requires its container' "$missing"
rm "$fixture/compose.nvidia.yaml"
assert_diag 'missing NVIDIA overlay does not hide a broken supported service' "$missing"
GPU_BACKEND=apple
for overlay in compose.apple.yaml compose.arm64.yaml compose.override.yaml compose.local.yml compose.multigpu-apple.yaml compose.standalone.apple.yaml; do
    printf 'services: {}\n' >"$fixture/$overlay"
    assert_diag "possible runnable overlay remains checked: $overlay" "$missing"
    rm "$fixture/$overlay"
done
ln -s "$fixture/absent" "$fixture/compose.custom.yaml"
assert_diag 'dangling custom overlay fails closed' "$missing"
rm "$fixture/compose.custom.yaml"
printf 'services:\n  comfyui:\n    image: busybox\n' >"$fixture/compose.yaml"
assert_diag 'CPU service definition is checked despite GPU manifest' "$missing"
for document in 'services: [invalid' 'services: {}\ninclude: other.yaml' 'services: null' 'services: {tool: {image: busybox}}\nservices: {}' 'services: {}\n---\nservices: {tool: {image: busybox}}' ''; do
    printf '%b\n' "$document" >"$fixture/compose.yaml"
    assert_diag 'unknown or nonempty configuration fails closed' "$missing"
done
printf 'services: {}\n' >"$fixture/compose.yaml"
for backends in '' all none 'apple nvidia' unknown; do
    SERVICE_GPU_BACKENDS[comfyui]="$backends"
    assert_diag "backend support is not excluded: $backends" "$missing"
done
SERVICE_GPU_BACKENDS[comfyui]='amd nvidia'
GPU_BACKEND=tpu
assert_diag 'unknown host backend fails closed' "$missing"
GPU_BACKEND=apple
PYTHON_CMD=false
assert_diag 'unavailable YAML parser fails closed' "$missing"
PYTHON_CMD=python3
state=running
assert_diag 'existing running container is inspected normally' '.[0] | .container_state == "running" and .health_status == "healthy"'
state=exited
assert_diag 'existing stopped container retains its failure' "$missing"
state=missing
DOCKER_DAEMON=false
assert_diag 'unavailable Docker cannot establish absence' '.[0] | .container_state == "unknown" and .health_status == "unknown"'
