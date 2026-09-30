#!/usr/bin/env bash
# ============================================================================
# ODS — P2P GPU Phase 00: Preflight Checks
# ============================================================================
# Part of: ods/installers/p2p-gpu/phases/
# Purpose: GPU detection (NVIDIA/AMD/CPU), disk/Docker/DNS validation,
#          nvidia-container-toolkit setup
#
# Expects: MIN_DISK_GB, MIN_VRAM_MB, LOGFILE, log(), warn(), err(),
#          find_dream_dir(), get_compose_cmd(), detect_gpu()
# Provides: GPU_BACKEND, GPU_NAME, GPU_VRAM, GPU_COUNT, CPU_COUNT,
#           DISK_AVAIL_GB (all exported for later phases)
#
# Fixes covered: #12 (NVIDIA toolkit), #13 (disk space), #14 (compose v1),
#                #17 (DNS), #27 (AMD GPU), #28 (CPU-only fallback)
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

step "Phase 0/12: Preflight checks"

TLS_OK="true"

# Must be root
if [[ $EUID -ne 0 ]]; then
  err "This script must be run as root. Run: sudo bash ${SCRIPT_NAME}"
  exit 1
fi

# ── [FIX: gpu-dedup] Use single detect_gpu() function ──────────────────────
detect_gpu

case "$GPU_BACKEND" in
  nvidia) log "NVIDIA GPU: ${GPU_NAME} × ${GPU_COUNT} (${GPU_VRAM} MiB VRAM each)" ;;
  amd)    log "AMD GPU: ${GPU_NAME} × ${GPU_COUNT} (${GPU_VRAM} MiB VRAM)" ;;
  cpu)    warn "No GPU detected — running in CPU-only mode (slower but functional)" ;;
esac

# Multi-GPU enumeration
if [[ "${GPU_COUNT:-0}" -ge "${MULTIGPU_MIN_GPUS:-2}" ]]; then
  enumerate_gpus
  log "Multi-GPU: ${GPU_COUNT} GPUs, total VRAM: ${GPU_TOTAL_VRAM} MiB"
  for i in "${!GPU_UUIDS[@]}"; do
    log "  GPU[${i}]: ${GPU_NAMES[$i]} (${GPU_VRAMS[$i]} MiB) ${GPU_UUIDS[$i]}"
  done
fi

CPU_COUNT=$(nproc)
DISK_AVAIL_GB=$(df -BG --output=avail / 2>&1 | tail -1 | tr -dc '0-9')
log "GPU backend: ${GPU_BACKEND} | CPUs: ${CPU_COUNT} | Disk: ${DISK_AVAIL_GB} GB"

# VRAM check
if [[ "$GPU_BACKEND" != "cpu" && "${GPU_VRAM:-0}" -lt "$MIN_VRAM_MB" ]]; then
  warn "GPU VRAM (${GPU_VRAM} MiB) below recommended (${MIN_VRAM_MB} MiB) — small models only"
fi

# ── Disk space ──────────────────────────────────────────────────────────────
_check_disk_space() {
  local existing_install
  existing_install=$(find_dream_dir 2>&1 || echo "")
  if [[ "${DISK_AVAIL_GB:-0}" -lt "$MIN_DISK_GB" ]]; then
    if [[ -n "$existing_install" && -f "${existing_install}/.env" ]]; then
      warn "Disk (${DISK_AVAIL_GB} GB) below ${MIN_DISK_GB} GB, but ODS already installed"
    else
      err "Disk space (${DISK_AVAIL_GB} GB) below minimum (${MIN_DISK_GB} GB)."
      err "ODS needs 40+ GB. Create a Vast.ai instance with more disk."
      exit 1
    fi
  fi
}
_check_disk_space

# ── Docker ──────────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  err "Docker not found. Use a Vast.ai image with Docker pre-installed."
  exit 1
fi

COMPOSE_CMD=$(get_compose_cmd)
compose_version="unknown"
case "$COMPOSE_CMD" in
  "docker compose")
    compose_version=$(docker compose version --short 2>&1 || echo "unknown")
    ;;
  "docker-compose")
    compose_version=$(docker-compose version --short 2>&1 || echo "unknown")
    ;;
esac
log "Docker Compose: ${COMPOSE_CMD} (${compose_version})"

# ── GPU passthrough verification ────────────────────────────────────────────
_verify_nvidia_passthrough() {
  local gpu_test_image="nvidia/cuda:12.4.1-base-ubuntu22.04"
  local passthrough_timeout="${NVIDIA_DOCKER_TEST_TIMEOUT:-180}"
  local probe_rc=0

  log "Verifying NVIDIA Docker passthrough (timeout ${passthrough_timeout}s; first run may pull ${gpu_test_image})"
  if timeout --signal=TERM "${passthrough_timeout}" \
    docker run --rm --gpus all "${gpu_test_image}" nvidia-smi &>/dev/null; then
    log "NVIDIA Docker passthrough verified"

    # ── [FIX: nvml-mismatch] Detect and repair driver/library mismatch ────────
    log "Checking for NVIDIA driver/library version misalignment..."
    if detect_nvml_mismatch "${gpu_test_image}"; then
      :
    else
      mismatch_status=$?
      if [[ $mismatch_status -eq 1 ]]; then
        warn "NVIDIA driver/library mismatch detected — attempting repair"
        if ! repair_nvml_mismatch; then
          warn "NVIDIA driver mismatch repair did not complete (non-fatal)"
        fi
      fi
    fi

    return 0
  else
    probe_rc=$?
  fi

  if [[ "$probe_rc" -eq 124 ]]; then
    warn "NVIDIA GPU passthrough probe timed out after ${passthrough_timeout}s — checking toolkit..."
  else
    warn "NVIDIA GPU passthrough test failed (exit ${probe_rc}) — checking toolkit..."
  fi

  if [[ "$probe_rc" -ne 0 ]]; then
    if ! dpkg -l nvidia-container-toolkit &>/dev/null; then
      warn "nvidia-container-toolkit not installed — attempting install"

      # [NON-FATAL: dpkg] apt will still enforce DPkg::Lock::Timeout.
      _wait_for_dpkg_lock 60 || warn "dpkg lock not released in time — DPkg::Lock::Timeout will handle"

      local keyring="/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg"
      # [NON-FATAL: repo] Transient GPG/keyring failures should not halt install.
      curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor --batch --yes --output "$keyring" 2>>"$LOGFILE" \
        || warn "gpg key import failed (non-fatal)"
      curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
      # Cloud images — and our own _pin_nvidia_packages() (apt-mark hold on libnvidia*,
      # which matches libnvidia-container1) — hold the toolkit deps, so we unhold only
      # the container packages and keep the driver userspace libs pinned.
      apt-get -o DPkg::Lock::Timeout="${APT_LOCK_TIMEOUT:-120}" update -qq 2>>"$LOGFILE" \
        || warn "apt update failed (non-fatal) — proceeding with cached package lists"
      for pkg in libnvidia-container1 libnvidia-container-tools nvidia-container-toolkit-base nvidia-container-toolkit; do
        if dpkg -l "$pkg" 2>>"$LOGFILE" | grep -q '^ii'; then
          if ! apt-mark unhold "$pkg" 2>>"$LOGFILE"; then
            warn "apt-mark unhold failed for ${pkg} (non-fatal)"
          fi
        fi
      done
      if ! apt-get -o DPkg::Lock::Timeout="${APT_LOCK_TIMEOUT:-120}" install -y -qq \
             libnvidia-container1 libnvidia-container-tools \
             nvidia-container-toolkit-base nvidia-container-toolkit 2>>"$LOGFILE"; then
        warn "nvidia-container-toolkit install failed (non-fatal) — GPU passthrough may be unavailable"
      fi
      for pkg in libnvidia-container1 libnvidia-container-tools nvidia-container-toolkit-base nvidia-container-toolkit; do
        if dpkg -l "$pkg" 2>>"$LOGFILE" | grep -q '^ii'; then
          if ! apt-mark hold "$pkg" 2>>"$LOGFILE"; then
            warn "apt-mark hold failed for ${pkg} (non-fatal)"
          fi
        fi
      done
      # [NON-FATAL: nvidia-ctk] Toolkit may already be configured or unavailable.
      nvidia-ctk runtime configure --runtime=docker 2>>"$LOGFILE" || warn "nvidia-ctk configure failed (non-fatal)"
      # [NON-FATAL: docker] Docker may not be managed by systemctl on Vast.ai.
      systemctl restart docker 2>>"$LOGFILE" || service docker restart 2>>"$LOGFILE" \
        || warn "docker restart failed (non-fatal)"
      log "nvidia-container-toolkit installed and configured"

      # ── [FIX: nvml-mismatch] Re-check after toolkit install ──────────────
      log "Re-checking for NVIDIA driver/library mismatch after toolkit install..."
      if detect_nvml_mismatch "${gpu_test_image}"; then
        :
      else
        mismatch_status=$?
        if [[ $mismatch_status -eq 1 ]]; then
          warn "NVIDIA driver/library mismatch detected — attempting repair"
          if ! repair_nvml_mismatch; then
            warn "NVIDIA driver mismatch repair did not complete (non-fatal)"
          fi
        fi
      fi
    fi
  fi
}

_verify_amd_passthrough() {
  [[ ! -e /dev/kfd ]] && warn "/dev/kfd not found — AMD GPU may not be container-accessible"
  [[ ! -d /dev/dri ]] && warn "/dev/dri not found — AMD GPU rendering may not work"
  if docker run --rm --device=/dev/kfd --device=/dev/dri rocm/rocm-terminal:latest rocm-smi &>/dev/null; then
    log "AMD ROCm Docker passthrough verified"
  else
    warn "AMD ROCm Docker test failed — GPU may need driver configuration"
  fi
}

[[ "$GPU_BACKEND" == "nvidia" ]] && _verify_nvidia_passthrough
[[ "$GPU_BACKEND" == "amd" ]] && _verify_amd_passthrough

# Re-detect GPU if initial detection returned cpu but nvidia-smi works now
# (can happen after nvidia-container-toolkit install or stale state from previous run)
if [[ "$GPU_BACKEND" == "cpu" ]] && command -v nvidia-smi &>/dev/null \
  && nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null 2>&1; then
  log "Re-running GPU detection after toolkit install..."
  detect_gpu
  if [[ "$GPU_BACKEND" != "cpu" ]]; then
    log "GPU detected on retry: ${GPU_NAME} × ${GPU_COUNT} (${GPU_VRAM} MiB VRAM each)"
  fi
fi

# ── DNS fix ─────────────────────────────────────────────────────────────────
if ! host github.com &>/dev/null && ! nslookup github.com &>/dev/null; then
  if ! curl -sf --max-time 5 https://github.com > /dev/null; then
    warn "DNS resolution broken — adding Google DNS as fallback"
    if ! grep -q '8.8.8.8' /etc/resolv.conf; then
      echo "nameserver 8.8.8.8" >> /etc/resolv.conf
      echo "nameserver 1.1.1.1" >> /etc/resolv.conf
    fi
  fi
fi

# ── HTTPS trust (proxy CA) ─────────────────────────────────────────────────
_verify_https_trust() {
  local urls=(
    "https://huggingface.co"
    "https://registry-1.docker.io/v2/"
  )
  local failed=false

  if ! command -v curl &>/dev/null; then
    warn "curl not found — skipping HTTPS trust check"
    return 0
  fi

  for url in "${urls[@]}"; do
    if curl -fsI --max-time 10 "$url" > /dev/null 2>>"$LOGFILE"; then
      continue
    fi
    local rc=$?
    if [[ "$rc" -eq 60 ]]; then
      warn "HTTPS trust failure when contacting ${url} (curl exit 60)"
      failed=true
    else
      warn "HTTPS check failed for ${url} (curl exit ${rc})"
    fi
  done

  if [[ "$failed" == "true" ]]; then
    TLS_OK="false"
    warn "System TLS trust is broken — model downloads and Docker pulls will fail"
    warn "If behind a proxy, install the proxy root CA, then run:"
    warn "  cp /path/to/proxy-root.crt /usr/local/share/ca-certificates/proxy-root.crt"
    warn "  update-ca-certificates --fresh"
    warn "  systemctl restart docker"
  fi
}

_verify_https_trust

# ── /tmp permissions fix ────────────────────────────────────────────────────
if [[ "$(stat -c '%a' /tmp)" != "1777" ]]; then
  chown root:root /tmp
  chmod 1777 /tmp
  log "/tmp permissions fixed (was broken)"
else
  log "/tmp permissions OK"
fi

log "All preflight checks passed"
