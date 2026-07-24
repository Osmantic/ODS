#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Phase 04: Run Upstream Installer
# ============================================================================
# Part of: p2p-gpu/phases/
# Purpose: Execute ODS's install.sh with timeout protection
#
# Expects: REPO_DIR, ODS_USER, INSTALLER_TIMEOUT, GPU_BACKEND, GPU_VRAM,
#          GPU_COUNT, log(), warn(), err()
# Provides: ODS installed (may be partial if timeout hit)
#
# Fixes covered: #25 (ComfyUI infinite hang), #26 (installer timeout)
#
# Modder notes:
#   Timeout is non-fatal. Heavy services (ComfyUI, Whisper) download in
#   background and are handled by later phases. We only cap the installer
#   wait loop, not the actual containers.
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

step "Phase 4/12: Running ODS installer"

warn "Running installer (${INSTALLER_TIMEOUT}s timeout)..."
warn "Heavy services (ComfyUI, Whisper, etc.) will continue after timeout."

install_exit=0
installer_pid=""

# Map detected VRAM to upstream installer tier system so non-interactive
# installs on GPU hosts don't fall through to CPU-tier model selection.
# Hard-fail philosophy: if GPU_BACKEND is nvidia but VRAM is unknown/zero,
# we let the installer auto-detect rather than passing a wrong tier.
installer_tier_arg=""
if [[ "$GPU_BACKEND" == "nvidia" && "${GPU_VRAM:-0}" -gt 0 ]]; then
  if   [[ "$GPU_VRAM" -ge 90000 ]]; then installer_tier_arg="--tier NV_ULTRA"
  elif [[ "$GPU_VRAM" -ge 40000 ]]; then installer_tier_arg="--tier 4"
  elif [[ "$GPU_VRAM" -ge 20000 ]]; then installer_tier_arg="--tier 3"
  elif [[ "$GPU_VRAM" -ge 12000 ]]; then installer_tier_arg="--tier 2"
  else                                   installer_tier_arg="--tier 1"
  fi
  log "Passing ${installer_tier_arg} to installer (GPU_VRAM=${GPU_VRAM} MiB)"
elif [[ "$GPU_BACKEND" == "amd" && "${GPU_VRAM:-0}" -gt 0 ]]; then
  if   [[ "$GPU_VRAM" -ge 20000 ]]; then installer_tier_arg="--tier 3"
  elif [[ "$GPU_VRAM" -ge 12000 ]]; then installer_tier_arg="--tier 2"
  else                                   installer_tier_arg="--tier 1"
  fi
  log "Passing ${installer_tier_arg} to installer (GPU_VRAM=${GPU_VRAM} MiB)"
fi

# CDI containers can expose /dev/nvidia* without DRM vendor sysfs. Provide a
# minimal sysfs override for the installer's detection phase when needed.
drm_sys_override=""
if [[ "$GPU_BACKEND" == "nvidia" && ( -e /dev/nvidiactl || -e /dev/nvidia0 ) ]]; then
  has_drm_vendor=false
  for vendor_path in /sys/class/drm/card*/device/vendor; do
    if [[ -e "$vendor_path" ]]; then
      has_drm_vendor=true
      break
    fi
  done
  if [[ "$has_drm_vendor" == "false" ]]; then
    drm_sys_override="${TMPDIR:-/tmp}/ods-drm-sys"
    mkdir -p "${drm_sys_override}/card0/device"
    printf '0x10de\n' > "${drm_sys_override}/card0/device/vendor"
    log "Providing DRM sysfs override at ${drm_sys_override} for containerized NVIDIA detection"
  fi
fi

# sudo -E -u preserves GPU_BACKEND/GPU_VRAM/GPU_COUNT for the installer's
# detection phase. The previous `su -` was a login shell and stripped them,
# causing the installer to re-run its own (sysfs-based) detection which
# fails on Vast.ai / RunPod / any CDI-based GPU container.
sudo -E -u "$ODS_USER" \
  env HOME="${ODS_HOME}" \
    GPU_BACKEND="$GPU_BACKEND" \
    GPU_VRAM="${GPU_VRAM:-0}" \
    GPU_COUNT="${GPU_COUNT:-1}" \
    GPU_NAME="${GPU_NAME:-unknown}" \
    ODS_DRM_SYS="${drm_sys_override:-}" \
    bash -c "cd ${REPO_DIR} && ./install.sh --non-interactive ${installer_tier_arg}" &
installer_pid=$!

waited=0
while kill -0 "$installer_pid" 2>/dev/null; do  # stderr expected: process may exit between checks
  if [[ $waited -ge $INSTALLER_TIMEOUT ]]; then
    warn "Installer reached ${INSTALLER_TIMEOUT}s limit — proceeding with setup"
    # [NON-FATAL: cleanup] Installer may have exited before TERM.
    kill -TERM "$installer_pid" 2>>"$LOGFILE" || warn "could not TERM installer (non-fatal)"
    sleep 2
    if kill -0 "$installer_pid" 2>>"$LOGFILE"; then
      # [NON-FATAL: cleanup] Installer may have exited before KILL.
      kill -9 "$installer_pid" 2>>"$LOGFILE" || warn "could not KILL installer (non-fatal)"
    fi
    # Child processes of the installer should die with their parent.
    # No pkill -f needed — TERM/KILL on the parent suffices.
    install_exit=124
    break
  fi
  sleep 5
  waited=$((waited + 5))
  (( waited % 60 == 0 )) && log "Installer running... (${waited}s / ${INSTALLER_TIMEOUT}s max)"
done

if [[ $install_exit -ne 124 ]]; then
  wait "$installer_pid" 2>>"$LOGFILE" || install_exit=$?
fi

if [[ $install_exit -eq 0 ]]; then
  log "ODS installer completed successfully"
elif [[ $install_exit -eq 124 ]]; then
  log "Installer timed out (normal for heavy services) — continuing"
else
  warn "Installer exited with code ${install_exit} — applying fixes and continuing"
fi
