#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Phase 03: Repository Setup
# ============================================================================
# Part of: p2p-gpu/phases/
# Purpose: Clone ODS repo or locate existing checkout
#
# Expects: ODS_USER, ODS_HOME, REPO_URL, REPO_BRANCH,
#          log(), warn(), fix_ownership()
# Provides: REPO_DIR (path to cloned repository)
#
# Fixes covered: #09 (dual directory confusion)
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

step "Phase 3/12: Setting up ODS repository"

REPO_DIR="${ODS_HOME}/ODS"

if [[ -d "${REPO_DIR}/.git" ]]; then
  log "Repository already exists at ${REPO_DIR}"
  su - "$ODS_USER" -c "cd ${REPO_DIR} && git pull --ff-only" 2>&1 || \
    warn "Could not pull latest (non-fatal — using existing checkout)"
else
  # Check alternate locations (some Vast.ai onstart scripts pre-clone)
  found_repo=""
  for candidate in /root/ODS /workspace/ODS /opt/ODS; do
    if [[ -d "${candidate}/.git" ]]; then
      found_repo="$candidate"
      break
    fi
  done

  if [[ -n "$found_repo" ]]; then
    mv "$found_repo" "$REPO_DIR"
    log "Moved repository from ${found_repo}"
  else
    su - "$ODS_USER" -c "git clone --depth 1 --branch ${REPO_BRANCH} ${REPO_URL} ${REPO_DIR}"
    log "Cloned ODS (shallow, branch: ${REPO_BRANCH})"
  fi
fi

fix_ownership "$REPO_DIR" "$ODS_USER"
