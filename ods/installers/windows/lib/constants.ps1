# ============================================================================
# ODS Windows Installer -- Constants
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Version, paths, colors, configuration defaults
#
# Canonical source: installers/lib/constants.sh (keep VERSION in sync)
#
# Modder notes:
#   Change ODS_VERSION for custom builds. Must match constants.sh VERSION.
# ============================================================================

$script:ODS_VERSION = "3.0.0"

# Install location (override via $env:ODS_HOME)
# NOTE: $(if ...) syntax required for PS 5.1 compatibility (bare if-as-expression is PS 7+ only)
$script:ODS_INSTALL_DIR = $(if ($env:ODS_HOME) { $env:ODS_HOME } else { Join-Path $env:USERPROFILE "ods" })

# Logging
$script:ODS_LOG_FILE = Join-Path $env:TEMP "ods-install.log"
$script:ODS_PREFLIGHT_REPORT = Join-Path $env:TEMP "ods-windows-preflight.json"

# Native inference server paths (AMD path)
# PID file is shared -- only one native inference server runs at a time
$script:INFERENCE_PID_FILE = Join-Path (Join-Path $script:ODS_INSTALL_DIR "data") "llama-server.pid"

# Native llama-server (ggml-org llama.cpp, Vulkan) for AMD GPUs. Default host
# port; .env AMD_INFERENCE_PORT is authoritative once written.
$script:NATIVE_LLM_PORT = 8080

# The qualified runtime from %LOCALAPPDATA%\ODS\llama.cpp\<tag>-win-vulkan-x64
# is published here, where ods.ps1, bootstrap-upgrade.sh and the host agent
# launch it. pin.json inside is verified before every launch.
$script:LLAMA_SERVER_DIR = Join-Path $script:ODS_INSTALL_DIR "llama-server"
$script:LLAMA_SERVER_EXE = Join-Path $script:LLAMA_SERVER_DIR "llama-server.exe"
$script:LLAMA_CPP_RELEASE_TAG = "b9014"
$script:LLAMA_CPP_VULKAN_ASSET = "llama-$($script:LLAMA_CPP_RELEASE_TAG)-bin-win-vulkan-x64.zip"
$script:LLAMA_CPP_VULKAN_URL = "https://github.com/ggml-org/llama.cpp/releases/download/$($script:LLAMA_CPP_RELEASE_TAG)/$($script:LLAMA_CPP_VULKAN_ASSET)"
# SHA-256 of each Windows Vulkan archive ODS may install, keyed by release
# tag (the default above and any tier-map LlamaCppReleaseTag). Values are
# GitHub's per-asset digests for the official ggml-org/llama.cpp release.
# The installer refuses an archive whose tag is missing here.
$script:LLAMA_CPP_VULKAN_SHA256 = @{
    "b9014" = "6cd4bc7a44256e674458b0c5ea2ae3461dca29ee87876c8d410ecc78652a3b0f"
}
# Exact byte size of each archive above (GitHub's asset size), checked with
# the SHA-256 before extraction. config/backends/amd.json
# runtime.llama_server.windows is the primary pin; native-llama-runtime.ps1
# falls back to these two tables only while amd.json does not carry it.
$script:LLAMA_CPP_VULKAN_SIZE = @{
    "b9014" = 33541404
}

# Docker
$script:DOCKER_COMPOSE_CMD = "docker compose"
$script:MIN_DOCKER_VERSION = "4.20.0"

# Minimum NVIDIA driver version for CUDA in Docker Desktop
$script:MIN_NVIDIA_DRIVER = 570

# Speaches CUDA images can require a newer driver than llama.cpp's CUDA image.
# Keep this separate so NVIDIA LLM installs can remain available on R570 drivers
# while Whisper falls back to its CPU image.
$script:MIN_WINDOWS_WHISPER_CUDA_DRIVER = 575

# OpenCode (host-level AI coding IDE, not a Docker service)
$script:OPENCODE_VERSION = "1.18.32"
# Architecture-specific archives and SHA256 values: ../../lib/opencode-release.tsv
$script:OPENCODE_DIR = Join-Path $env:USERPROFILE ".opencode"
$script:OPENCODE_BIN = Join-Path (Join-Path $env:USERPROFILE ".opencode") "bin"
$script:OPENCODE_EXE = Join-Path (Join-Path $env:USERPROFILE ".opencode") "bin\opencode.exe"
$script:OPENCODE_CONFIG_DIR = Join-Path (Join-Path $env:USERPROFILE ".config") "opencode"
$script:OPENCODE_PORT = 3003
$script:OPENCODE_TASK_NAME = "ODSOpenCodeWeb"

# ODS Host Agent (host-level extension lifecycle manager)
$script:ODS_AGENT_PORT       = 7710
$script:ODS_AGENT_PID_FILE   = Join-Path (Join-Path $script:ODS_INSTALL_DIR "data") "ods-host-agent.pid"
$script:ODS_AGENT_LOG_FILE   = Join-Path (Join-Path $script:ODS_INSTALL_DIR "data") "ods-host-agent.log"
$script:ODS_AGENT_HEALTH_URL = "http://127.0.0.1:7710/health"
$script:ODS_AGENT_TASK_NAME  = "ODSHostAgent"

# Timing
$script:INSTALL_START = Get-Date

# ============================================================================
# Colors -- green phosphor CRT theme (PowerShell console colors)
# ============================================================================
$script:C = @{
    Red       = "Red"
    Green     = "Green"
    BrightGrn = "DarkGreen"
    DimGrn    = "DarkGray"
    Amber     = "Yellow"
    White     = "White"
    Cyan      = "Cyan"
    Reset     = "Gray"
}
