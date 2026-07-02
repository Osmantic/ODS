#!/usr/bin/env bash
# ============================================================================
# ODS — P2P GPU Deploy Constants
# ============================================================================
# Part of: ods/installers/p2p-gpu/lib/
# Purpose: Readonly variables, colors, paths, thresholds
#
# Expects: (nothing — first file sourced)
# Provides: P2P_GPU_VERSION, PROVIDER_NAME, ODS_USER, ODS_HOME,
#           REPO_URL, REPO_BRANCH, MIN_DISK_GB, MIN_VRAM_MB,
#           LOCKFILE, LOGFILE, PIDFILE_DIR, color codes
#
# Modder notes:
#   All constants are readonly. Override via env vars BEFORE sourcing.
#   Variables are consumed by other files sourced after this one.
#   To add a new provider: create providers/<name>.sh, set PROVIDER_NAME.
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

# shellcheck disable=SC2034  # Variables used by sourcing scripts
set -euo pipefail

readonly P2P_GPU_VERSION="6.1.0"
# Back-compat alias for phases that reference the old name
readonly VASTAI_VERSION="$P2P_GPU_VERSION"
readonly PROVIDER_NAME="${P2P_GPU_PROVIDER:-vastai}"
readonly LOCKFILE="/tmp/ods-p2p-gpu-setup.lock"
readonly LOGFILE="/var/log/ods-p2p-gpu-setup.log"
readonly PIDFILE_DIR="/var/run/ods-p2p-gpu"

readonly ODS_USER="dream"
readonly ODS_HOME="/home/${ODS_USER}"
readonly REPO_URL="https://github.com/Light-Heart-Labs/ODS.git"
readonly REPO_BRANCH="main"
readonly MIN_DISK_GB=40
readonly MIN_VRAM_MB=8000
readonly INSTALLER_TIMEOUT="${INSTALLER_TIMEOUT:-600}"
readonly MULTIGPU_MIN_GPUS=2

# ── Colors ──────────────────────────────────────────────────────────────────
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly DIM='\033[2m'
readonly NC='\033[0m'
