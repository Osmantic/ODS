#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Phase 01: System Dependencies
# ============================================================================
# Part of: p2p-gpu/phases/
# Purpose: Install missing packages (git, curl, jq, aria2, acl, python3-yaml)
#
# Expects: LOGFILE, log()
# Provides: All required CLI tools available in PATH
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

step "Phase 1/12: Installing system dependencies"

pkgs_needed=()
for pkg in sudo git curl jq wget openssl aria2 procps iproute2 acl python3-yaml; do
  # python3-yaml is a library, check via python3 import
  if [[ "$pkg" == "python3-yaml" ]]; then
    python3 -c "import yaml" 2>&1 || pkgs_needed+=("$pkg")
    continue
  fi
  command -v "$pkg" &>/dev/null || pkgs_needed+=("$pkg")
done
# ss is part of iproute2
command -v ss &>/dev/null || pkgs_needed+=("iproute2")

# Vast.ai instances often ship with stale PPAs (e.g. graphics-drivers) that
# timeout during apt-get update and cause hard failures under set -e.
# The GPU driver is already installed — these PPAs are not needed.
for stale_ppa in graphics-drivers; do
  if ls /etc/apt/sources.list.d/${stale_ppa}* &>/dev/null; then
    rm -f /etc/apt/sources.list.d/${stale_ppa}*
    log "Removed stale PPA: ${stale_ppa} (not needed — driver already installed)"
  fi
done

# unattended-upgrades can hold the dpkg lock for minutes on fresh Vast.ai
# instances. We rely on DPk::Lock::Timeout below, but if the lock is clearly
# stuck, kill only unattended-upgrades (the typical culprit).
# [NON-FATAL: dpkg] apt will still enforce DPkg::Lock::Timeout.
_wait_for_dpkg_lock 90 || warn "dpkg lock not released in time — DPkg::Lock::Timeout will handle"

# Disable unattended-upgrades permanently — it causes NVML mismatches
# and dpkg lock contention on GPU instances
if systemctl is-enabled unattended-upgrades &>/dev/null; then  # stderr expected: service check
  # [NON-FATAL: systemd] Unattended-upgrades may not be managed on this host.
  systemctl disable unattended-upgrades 2>>"$LOGFILE" || warn "Could not disable unattended-upgrades (non-fatal)"
  # [NON-FATAL: systemd] Unattended-upgrades may not be managed on this host.
  systemctl mask unattended-upgrades 2>>"$LOGFILE" || warn "Could not mask unattended-upgrades (non-fatal)"
  log "Disabled unattended-upgrades (prevents NVIDIA driver/library mismatches)"
fi

if [[ ${#pkgs_needed[@]} -gt 0 ]]; then
  # unattended-upgrades may briefly hold dpkg lock on fresh hosts.
  apt-get -o DPkg::Lock::Timeout="${APT_LOCK_TIMEOUT:-300}" update -qq 2>>"$LOGFILE"
  apt-get -o DPkg::Lock::Timeout="${APT_LOCK_TIMEOUT:-300}" install -y -qq "${pkgs_needed[@]}" 2>>"$LOGFILE"
  log "Installed: ${pkgs_needed[*]}"
else
  log "All dependencies already present"
fi
