#!/usr/bin/env bash
# The core multi-GPU overlays scope the in-stack llama-server to the GPUs that
# phase 03 assigned. Phase 03 skips that assignment when no in-stack
# llama-server runs (external API, cloud), so the overlay's required
# LLAMA_SERVER_GPU_* values are empty and Compose refuses the whole stack.
# The resolver must select the overlay only for managed local inference.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOLVER="$ROOT_DIR/scripts/resolve-compose-stack.sh"

resolve() {
    local backend="$1" mode="$2"
    shift 2
    env -u NATIVE_LLM_BASE_URL -u EXTERNAL_LLM_URL -u ODS_EXTERNAL_LLM_SELECTED "$@" \
        bash "$RESOLVER" --script-dir "$ROOT_DIR" --tier 3 --gpu-backend "$backend" \
        --gpu-count 2 --ods-mode "$mode" 2>/dev/null | tr '\134' '/'
}

expect() {
    local want="$1" label="$2" backend="$3" mode="$4"
    shift 4
    local overlay="docker-compose.multigpu-${backend}.yml" flags
    flags="$(resolve "$backend" "$mode" "$@")"
    if [[ " $flags " == *" $overlay "* ]]; then
        [[ "$want" == present ]] || { echo "[FAIL] $label: $overlay selected" >&2; exit 1; }
    else
        [[ "$want" == absent ]] || { echo "[FAIL] $label: $overlay missing" >&2; exit 1; }
    fi
    echo "[PASS] $label"
}

for backend in nvidia amd; do
    expect present "$backend local multi-GPU keeps the overlay" "$backend" local
    expect absent "$backend external API drops the overlay" "$backend" local \
        EXTERNAL_LLM_URL=http://host.docker.internal:11434
    expect absent "$backend cloud mode drops the overlay" "$backend" cloud
done

echo "test-resolver-multigpu-managed-inference: ok"
