#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Phase 11: Access Layer
# ============================================================================
# Part of: p2p-gpu/phases/
# Purpose: Cloudflare tunnel, SSH tunnel scripts, and access guidance
#
# Expects: ODS_DIR, GPU_BACKEND, log(), warn(), setup_cloudflare_tunnel(),
#          generate_ssh_tunnel_script(),
#          generate_powershell_tunnel_script(),
#          comfyui_preload_models()
# Provides: All access methods configured for Vast.ai connectivity
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

step "Phase 11/12: Setting up access layer"

# ComfyUI extra model downloads (if configured)
comfyui_preload_models "$ODS_DIR" "$GPU_BACKEND"

# Prefer SSH tunnel mode for Vast.ai reliability and Windows compatibility.
log "Using SSH tunnel mode for access (no public reverse-proxy URLs shown)"

# Optional Cloudflare Tunnel
setup_cloudflare_tunnel "$ODS_DIR"

# Auto-reconnecting SSH tunnel script
generate_ssh_tunnel_script "$ODS_DIR"
generate_powershell_tunnel_script "$ODS_DIR"
