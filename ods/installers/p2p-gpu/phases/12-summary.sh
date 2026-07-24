#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Phase 12: Summary
# ============================================================================
# Part of: p2p-gpu/phases/
# Purpose: Print access info, connection methods, final success message
#
# Expects: ODS_DIR, LOGFILE, log(), print_access_info(), _ts()
# Provides: User-facing summary of all access methods
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

step "Phase 12/12: Setup complete"

print_access_info "$ODS_DIR"

# [NON-FATAL: logging] Summary logging should not block completion.
echo "=== Setup completed at $(_ts) ===" >> "$LOGFILE" || warn "logfile write failed (non-fatal)"
log "Setup complete! Core services ready. Heavy services downloading in background."
