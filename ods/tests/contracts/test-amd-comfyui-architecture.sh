#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

AMD_COMPOSE="extensions/services/comfyui/compose.amd.yaml"
BASE_COMPOSE="extensions/services/comfyui/compose.yaml"
NVIDIA_COMPOSE="extensions/services/comfyui/compose.nvidia.yaml"
INSTALL_PHASE="installers/phases/11-services.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

for file in "$AMD_COMPOSE" "$BASE_COMPOSE" "$NVIDIA_COMPOSE" "$INSTALL_PHASE"; do
    [[ -f "$file" ]] || fail "missing contract input: $file"
done

# Both supported AMD architectures must run their native ISA. In particular,
# a global installer HSA override for the llama/Lemonade path must not leak into
# ComfyUI through Compose interpolation.
if grep -Eq '^[[:space:]]*-[[:space:]]*HSA_OVERRIDE_GFX_VERSION(=|:|\$)' \
    "$AMD_COMPOSE"; then
    fail "AMD ComfyUI must not define or interpolate HSA_OVERRIDE_GFX_VERSION"
fi
if grep -q 'PYTORCH_TUNABLEOP' "$AMD_COMPOSE"; then
    fail "AMD ComfyUI must not enable exhaustive TunableOp startup tuning"
fi
grep -qF -- '- MIOPEN_FIND_MODE=FAST' "$AMD_COMPOSE" \
    || fail "AMD ComfyUI must use bounded MIOpen kernel selection"
grep -qF -- './data/comfyui/miopen:/root/.config/miopen:z' "$AMD_COMPOSE" \
    || fail "AMD ComfyUI must persist its MIOpen cache across recreations"
grep -qF 'restart: unless-stopped' "$AMD_COMPOSE" \
    || fail "AMD ComfyUI must retain its restart policy"
grep -qF "COMFYUI_MIOPEN_CACHE=\"\$INSTALL_DIR/data/comfyui/miopen\"" "$INSTALL_PHASE" \
    || fail "the installer must select the persistent AMD MIOpen cache"
grep -qF "mkdir -p \"\$COMFYUI_MIOPEN_CACHE\"" "$INSTALL_PHASE" \
    || fail "the installer must pre-create the AMD MIOpen cache"
grep -qF "chmod u+rwx,go+rx \"\$COMFYUI_MIOPEN_CACHE\"" "$INSTALL_PHASE" \
    || fail "the installer must keep the AMD MIOpen cache writable and traversable"
pass "cold-start and recreation safeguards are present"

# Preserve the current registry/switchboard overlay contract and keep unrelated
# backends free of AMD-only runtime controls.
grep -qF 'compose.multigpu-amd.yaml' "$BASE_COMPOSE" \
    || fail "the ComfyUI stub lost its AMD multi-GPU overlay documentation"
grep -qF 'compose.multigpu-nvidia.yaml' "$BASE_COMPOSE" \
    || fail "the ComfyUI stub lost its NVIDIA multi-GPU overlay documentation"
if grep -Eq 'HSA_OVERRIDE_GFX_VERSION|MIOPEN_FIND_MODE|PYTORCH_TUNABLEOP' \
    "$BASE_COMPOSE" "$NVIDIA_COMPOSE"; then
    fail "AMD-only runtime controls leaked into base or NVIDIA ComfyUI"
fi
pass "base, NVIDIA, CPU, and Apple paths remain AMD-control no-ops"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

# Exercise the install-time permission behavior with a restrictive ambient
# umask. A marker simulates a compiled MIOpen entry and must survive recreation.
cache_dir="$tmp_dir/ODS install/data/comfyui/miopen"
(
    umask 077
    mkdir -p "$cache_dir"
    chmod u+rwx,go+rx "$cache_dir"
)
[[ -w "$cache_dir" && -x "$cache_dir" ]] \
    || fail "install owner cannot write/traverse the MIOpen cache"
cache_mode="$(stat -c '%a' "$cache_dir" 2>/dev/null || stat -f '%Lp' "$cache_dir")"
(( 8#$cache_mode & 0111 )) \
    || fail "MIOpen cache is not traversable: mode $cache_mode"
printf 'cached-kernel\n' > "$cache_dir/find-db.marker"
mkdir -p "$cache_dir"
chmod u+rwx,go+rx "$cache_dir"
[[ "$(cat "$cache_dir/find-db.marker")" == "cached-kernel" ]] \
    || fail "container recreation preparation discarded the MIOpen cache"
pass "MIOpen cache permissions and recreation persistence are executable"

if command -v docker >/dev/null 2>&1 \
    && docker compose version >/dev/null 2>&1 \
    && command -v jq >/dev/null 2>&1; then
    base_rendered="$(docker compose -f "$BASE_COMPOSE" config --format json)"
    jq -e '(.services // {}) | length == 0' >/dev/null <<<"$base_rendered" \
        || fail "base ComfyUI stub unexpectedly defines a runtime service"

    nvidia_rendered="$(docker compose -f "$BASE_COMPOSE" -f "$NVIDIA_COMPOSE" \
        config --format json)"
    jq -e '.services.comfyui.environment
        | has("HSA_OVERRIDE_GFX_VERSION") | not' >/dev/null <<<"$nvidia_rendered" \
        || fail "NVIDIA ComfyUI inherited the AMD HSA override"
    jq -e '[.services.comfyui.volumes[]?.target]
        | index("/root/.config/miopen") | not' >/dev/null <<<"$nvidia_rendered" \
        || fail "NVIDIA ComfyUI inherited the AMD MIOpen cache"
    pass "rendered base/CPU/Apple and NVIDIA paths remain AMD-control no-ops"

    for gfx in gfx1151 gfx1201; do
        env_file="$tmp_dir/$gfx.env"
        {
            printf 'AMDGPU_TARGET=%s\n' "$gfx"
            printf 'COMFYUI_PORT=18188\n'
            printf 'VIDEO_GID=44\n'
            printf 'RENDER_GID=992\n'
            if [[ "$gfx" == "gfx1151" ]]; then
                # This remains valid for llama/Lemonade, but must not be
                # inherited by ComfyUI.
                printf 'HSA_OVERRIDE_GFX_VERSION=11.5.1\n'
            fi
        } > "$env_file"

        docker_env_file="$env_file"
        if command -v cygpath >/dev/null 2>&1; then
            docker_env_file="$(cygpath -w "$env_file")"
        fi

        rendered="$(docker compose --env-file "$docker_env_file" \
            -f "$BASE_COMPOSE" -f "$AMD_COMPOSE" config --format json)"
        jq -e '.services.comfyui.environment.MIOPEN_FIND_MODE == "FAST"' \
            >/dev/null <<<"$rendered" \
            || fail "$gfx render lost MIOPEN_FIND_MODE=FAST"
        jq -e '.services.comfyui.environment
            | has("HSA_OVERRIDE_GFX_VERSION") | not' >/dev/null <<<"$rendered" \
            || fail "$gfx render leaked HSA_OVERRIDE_GFX_VERSION into ComfyUI"
        jq -e '.services.comfyui.environment
            | has("PYTORCH_TUNABLEOP_ENABLED") | not' >/dev/null <<<"$rendered" \
            || fail "$gfx render re-enabled TunableOp"
        jq -e '.services.comfyui.volumes[]
            | select(.target == "/root/.config/miopen" and .type == "bind")' \
            >/dev/null <<<"$rendered" \
            || fail "$gfx render lost the persistent MIOpen bind mount"
        pass "$gfx Compose render uses native architecture controls"
    done
else
    echo "[SKIP] docker compose and jq are required for rendered gfx1151/gfx1201 checks"
fi

echo "AMD ComfyUI architecture contracts passed"
