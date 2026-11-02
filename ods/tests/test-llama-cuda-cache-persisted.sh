#!/usr/bin/env bash
# The pinned llama.cpp CUDA image has native kernels for compute capability
# 8.6, 8.9 and 12.0a only. On Hopper, datacenter Blackwell and GB10 the CUDA
# driver compiles the rest at load and on first use and caches the result. In
# the container layer that cache was lost on every recreate: on Workstation06's
# GB300 (2026-10-27) each restart took 25 s to ready and 42 s for the first long
# prompt, against 3 s and 1.3 s with the cache kept. This asserts the cache is
# bind-mounted read-write, sized for those GPUs, and created by the installer.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${ODS_NVIDIA_COMPOSE_UNDER_TEST:-$ROOT_DIR/docker-compose.nvidia.yml}"
PHASE="${ODS_PHASE06_UNDER_TEST:-$ROOT_DIR/installers/phases/06-directories.sh}"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

for f in "$COMPOSE" "$PHASE"; do [[ -f "$f" ]] || fail "missing $f"; done

# 1. The cache is a read-write bind mount on llama-server, and CUDA is pointed at it.
service="$(awk '/^  llama-server:/{f=1; next} f && /^  [a-z]/{f=0} f' "$COMPOSE")"
mount_line="$(grep -E '^\s*-\s*\./data/llama-server/cuda-cache:' <<<"$service" || true)"
[[ -n "$mount_line" ]] \
    || fail "docker-compose.nvidia.yml does not bind-mount ./data/llama-server/cuda-cache into llama-server; compiled kernels stay in the container layer"
grep -qE ':ro(,|$|[[:space:]])' <<<"$mount_line" \
    && fail "the CUDA cache is mounted read-only: $mount_line"
target="$(sed -E 's/^\s*-\s*\.\/data\/llama-server\/cuda-cache:([^:]+).*/\1/' <<<"$mount_line")"
grep -qE "^\s*-\s*CUDA_CACHE_PATH=${target}\s*$" <<<"$service" \
    || fail "llama-server's CUDA_CACHE_PATH does not name the mounted cache folder $target"
pass "llama-server's CUDA cache is bind-mounted read-write at CUDA_CACHE_PATH ($target)"

# 2. The driver's 1 GiB default is too small for a full compiled kernel set.
grep -qE '^\s*-\s*CUDA_CACHE_MAXSIZE=4294967296\s*$' <<<"$service" \
    || fail "llama-server's CUDA_CACHE_MAXSIZE is not raised to the driver's 4 GiB maximum"
pass "llama-server's CUDA cache may grow to 4 GiB"

# 3. The installer creates the folder with the other core data folders.
grep -qE '^\s*mkdir -p "\$INSTALL_DIR"/data/llama-server/cuda-cache\s*$' "$PHASE" \
    || fail "phase 06 does not create data/llama-server/cuda-cache"
pass "the installer creates llama-server's CUDA cache folder"
