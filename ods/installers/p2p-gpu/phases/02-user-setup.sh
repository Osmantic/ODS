#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Phase 02: User Setup
# ============================================================================
# Part of: p2p-gpu/phases/
# Purpose: Create dream user, configure sudo/docker group, copy SSH keys
#
# Expects: ODS_USER, ODS_HOME, log(), warn()
# Provides: Non-root 'dream' user ready for ODS install
#
# Fixes covered: #01 (root user rejection), #02 (Docker socket denied)
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

step "Phase 2/12: Creating user '${ODS_USER}'"

if id -u "$ODS_USER" &>/dev/null; then
  log "User '${ODS_USER}' already exists"
else
  useradd -m -s /bin/bash -u 1000 "$ODS_USER" 2>&1 || \
    useradd -m -s /bin/bash "$ODS_USER"
  log "User '${ODS_USER}' created"
fi

# Sudo access
# [NON-FATAL: permissions] Sudo group add is convenience; install can proceed.
usermod -aG sudo "$ODS_USER" || warn "sudo group add failed (non-fatal)"
echo "${ODS_USER} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-dream
chmod 440 /etc/sudoers.d/90-dream

# Docker group
if getent group docker &>/dev/null; then
  usermod -aG docker "$ODS_USER"
  log "Added ${ODS_USER} to docker group"
fi

# Copy SSH keys for direct user access
if [[ -d /root/.ssh && ! -d "${ODS_HOME}/.ssh" ]]; then
  cp -r /root/.ssh "${ODS_HOME}/.ssh"
  chown -R "${ODS_USER}:${ODS_USER}" "${ODS_HOME}/.ssh"
  chmod 700 "${ODS_HOME}/.ssh"
  find "${ODS_HOME}/.ssh" -type f -exec chmod 600 {} +
fi

log "User configured"
