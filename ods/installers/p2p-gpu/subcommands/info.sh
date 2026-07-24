#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Subcommand: info
# ============================================================================
# Part of: p2p-gpu/subcommands/
# Purpose: Print connection details only (no modifications)
#
# Expects: err(), find_dream_dir(), print_access_info()
# Provides: Display of all access methods and URLs
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

cmd_info() {
  local ds_dir
  ds_dir=$(find_dream_dir) || { err "ODS directory not found. Run full install first."; exit 1; }
  print_access_info "$ds_dir"
}
