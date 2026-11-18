#!/usr/bin/env bash
# On a GPU the image's PyTorch build has no kernels for (GB300, sm_103), the
# CUDA driver compiles them on first use and caches the result. In the
# container layer that cache was lost on every recreate, so the first image
# after each update took 52 s instead of 7 s (Workstation06, 2026-10-27). This
# asserts the cache is bind-mounted, sized for that GPU, and created and
# prepared for the container user like the other NVIDIA ComfyUI folders.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${ODS_COMFYUI_COMPOSE_UNDER_TEST:-$ROOT_DIR/extensions/services/comfyui/compose.nvidia.yaml}"
PHASE="${ODS_PHASE11_UNDER_TEST:-$ROOT_DIR/installers/phases/11-services.sh}"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

for f in "$COMPOSE" "$PHASE"; do [[ -f "$f" ]] || fail "missing $f"; done

# 1. The cache is a read-write bind mount, and CUDA is pointed at it.
mount_line="$(grep -E '^\s*-\s*\./data/comfyui/cuda-cache:' "$COMPOSE" || true)"
[[ -n "$mount_line" ]] \
    || fail "compose.nvidia.yaml does not bind-mount ./data/comfyui/cuda-cache; compiled kernels stay in the container layer"
grep -qE ':ro(,|$|[[:space:]])' <<<"$mount_line" \
    && fail "the CUDA cache is mounted read-only: $mount_line"
target="$(sed -E 's/^\s*-\s*\.\/data\/comfyui\/cuda-cache:([^:]+).*/\1/' <<<"$mount_line")"
grep -qE "^\s*CUDA_CACHE_PATH:\s*\"?${target}\"?\s*$" "$COMPOSE" \
    || fail "CUDA_CACHE_PATH does not name the mounted cache folder $target"
pass "the CUDA cache is bind-mounted read-write at CUDA_CACHE_PATH ($target)"

# 2. One sm_103 kernel set nearly fills the driver's 1 GiB default.
grep -qE '^\s*CUDA_CACHE_MAXSIZE:\s*"4294967296"\s*$' "$COMPOSE" \
    || fail "CUDA_CACHE_MAXSIZE is not raised to the driver's 4 GiB maximum"
pass "the CUDA cache may grow to 4 GiB"

# 3. The installer creates it and prepares it for UID 1000 with the rest.
grep -qE 'mkdir -p "\$INSTALL_DIR/data/comfyui"/\{[^}]*\bcuda-cache\b[^}]*\}' "$PHASE" \
    || fail "phase 11 does not create data/comfyui/cuda-cache, so Docker would auto-create it as root"
grep -qE '"\$INSTALL_DIR/data/comfyui" models output input user cuda-cache; then' "$PHASE" \
    || fail "phase 11 does not prepare data/comfyui/cuda-cache for the container's UID 1000"
pass "the installer creates the CUDA cache and prepares it for UID 1000"
