#!/bin/bash
# ============================================================================
# Dream Server macOS Installer -- Constants
# ============================================================================
# Part of: installers/macos/lib/
# Purpose: Version, paths, colors, configuration defaults
#
# Canonical source: installers/lib/constants.sh (keep VERSION in sync)
#
# Modder notes:
#   Change DS_VERSION for custom builds. Must match constants.sh VERSION.
# ============================================================================

DS_VERSION="2.5.0"

# Install location - use shared path resolution if available.
# constants.sh lives at two different depths depending on layout:
#   source tree: dream-server/installers/macos/lib/constants.sh
#   installed  : <install>/lib/constants.sh
# so try both relative locations for path-utils.sh and pick whichever exists.
_constants_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_path_utils=""
for _candidate in \
    "${_constants_dir}/../../lib/path-utils.sh" \
    "${_constants_dir}/../installers/lib/path-utils.sh"; do
    if [[ -f "$_candidate" ]]; then
        _path_utils="$_candidate"
        break
    fi
done
if [[ -n "$_path_utils" ]]; then
    . "$_path_utils"
    DS_INSTALL_DIR="$(resolve_install_dir)"
else
    # Fallback to legacy behavior
    DS_INSTALL_DIR="${DREAM_HOME:-$HOME/dream-server}"
fi
unset _constants_dir _path_utils _candidate

# Logging
DS_LOG_FILE="/tmp/dream-server-install-macos.log"

# Native llama-server paths (Metal acceleration on Apple Silicon)
LLAMA_SERVER_DIR="${DS_INSTALL_DIR}/bin"
LLAMA_SERVER_BIN="${LLAMA_SERVER_DIR}/llama-server"
LLAMA_SERVER_PID_FILE="${DS_INSTALL_DIR}/data/.llama-server.pid"
LLAMA_SERVER_LOG="${DS_INSTALL_DIR}/data/llama-server.log"

# llama.cpp release for macOS Metal build (update when new releases ship)
LLAMA_CPP_RELEASE_TAG="b8210"
LLAMA_CPP_MACOS_ASSET="llama-${LLAMA_CPP_RELEASE_TAG}-bin-macos-arm64.tar.gz"
LLAMA_CPP_MACOS_URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_CPP_RELEASE_TAG}/${LLAMA_CPP_MACOS_ASSET}"

# OpenCode (host-level AI coding IDE, not a Docker service)
OPENCODE_VERSION="1.2.18"
OPENCODE_DIR="$HOME/.opencode"
OPENCODE_BIN="$HOME/.opencode/bin/opencode"
OPENCODE_CONFIG_DIR="$HOME/.config/opencode"
OPENCODE_PORT=3003
OPENCODE_PLIST_LABEL="com.dreamserver.opencode-web"
OPENCODE_PLIST="$HOME/Library/LaunchAgents/${OPENCODE_PLIST_LABEL}.plist"

# Docker
DOCKER_COMPOSE_CMD="docker compose"
MIN_DOCKER_VERSION="4.20.0"

# Minimum macOS version (Ventura 13.0 for Metal 3)
MIN_MACOS_MAJOR=13

# Timing
INSTALL_START_EPOCH=$(date +%s)

# ============================================================================
# Colors -- green phosphor CRT theme (ANSI)
# ============================================================================
RED='\033[0;31m'
GRN='\033[0;32m'         # Standard green -- body text
BGRN='\033[1;32m'        # Bright green -- emphasis, success, headings
DGRN='\033[2;32m'        # Dim green -- secondary text, lore
AMB='\033[0;33m'         # Amber -- warnings, ETA labels
WHT='\033[1;37m'         # White -- key URLs
DIM='\033[2;37m'         # Dim white -- subdued hints, lore
NC='\033[0m'             # Reset
CURSOR='█'               # Block cursor for typing

# Dream Host Agent
DREAM_AGENT_PORT=7710
DREAM_AGENT_PLIST_LABEL="com.dreamserver.host-agent"
DREAM_AGENT_PLIST="$HOME/Library/LaunchAgents/${DREAM_AGENT_PLIST_LABEL}.plist"
