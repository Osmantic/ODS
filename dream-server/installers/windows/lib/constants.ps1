# ============================================================================
# Dream Server Windows Installer -- Constants
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Version, paths, colors, configuration defaults
#
# Canonical source: installers/lib/constants.sh (keep VERSION in sync)
#
# Modder notes:
#   Change DS_VERSION for custom builds. Must match constants.sh VERSION.
# ============================================================================

$script:DS_VERSION = "2.5.0"

# Install location (override via $env:DREAM_HOME)
# NOTE: $(if ...) syntax required for PS 5.1 compatibility (bare if-as-expression is PS 7+ only)
$script:DS_INSTALL_DIR = $(if ($env:DREAM_HOME) { $env:DREAM_HOME } else { Join-Path $env:USERPROFILE "dream-server" })

# Logging
$script:DS_LOG_FILE = Join-Path $env:TEMP "dream-server-install.log"
$script:DS_PREFLIGHT_REPORT = Join-Path $env:TEMP "dream-server-windows-preflight.json"

# Native inference server paths (AMD path)
# PID file is shared -- only one native inference server runs at a time
$script:INFERENCE_PID_FILE = Join-Path (Join-Path $script:DS_INSTALL_DIR "data") "llama-server.pid"

# AMD Lemonade (preferred AMD backend: Vulkan + NPU + ROCm)
$script:LEMONADE_VERSION     = "10.0.0"
$script:LEMONADE_MSI_FILE    = "lemonade-server-minimal.msi"
$script:LEMONADE_MSI_URL     = "https://github.com/lemonade-sdk/lemonade/releases/download/v$($script:LEMONADE_VERSION)/$($script:LEMONADE_MSI_FILE)"
# NOTE: ALLUSERS=1 installs to "Program Files\Lemonade Server" (with space)
$script:LEMONADE_INSTALL_DIR = Join-Path $env:ProgramFiles "Lemonade Server"
$script:LEMONADE_EXE         = Join-Path (Join-Path $script:LEMONADE_INSTALL_DIR "bin") "lemonade-server.exe"
$script:LEMONADE_PORT        = 8080
$script:LEMONADE_API_KEY     = "lemonade"
$script:LEMONADE_HEALTH_URL  = "http://localhost:8080/api/v1/health"

# llama-server fallback (Vulkan build, used if Lemonade install is declined/fails)
$script:LLAMA_SERVER_DIR = Join-Path $script:DS_INSTALL_DIR "llama-server"
$script:LLAMA_SERVER_EXE = Join-Path $script:LLAMA_SERVER_DIR "llama-server.exe"
$script:LLAMA_CPP_RELEASE_TAG = "b8248"
$script:LLAMA_CPP_VULKAN_ASSET = "llama-$($script:LLAMA_CPP_RELEASE_TAG)-bin-win-vulkan-x64.zip"
$script:LLAMA_CPP_VULKAN_URL = "https://github.com/ggml-org/llama.cpp/releases/download/$($script:LLAMA_CPP_RELEASE_TAG)/$($script:LLAMA_CPP_VULKAN_ASSET)"

# Docker
$script:DOCKER_COMPOSE_CMD = "docker compose"
$script:MIN_DOCKER_VERSION = "4.20.0"

# Minimum NVIDIA driver version for CUDA in Docker Desktop
$script:MIN_NVIDIA_DRIVER = 570

# OpenCode (host-level AI coding IDE, not a Docker service)
$script:OPENCODE_VERSION = "1.2.18"
$script:OPENCODE_ZIP = "opencode-windows-x64.zip"
$script:OPENCODE_URL = "https://github.com/anomalyco/opencode/releases/download/v$($script:OPENCODE_VERSION)/$($script:OPENCODE_ZIP)"
$script:OPENCODE_DIR = Join-Path $env:USERPROFILE ".opencode"
$script:OPENCODE_BIN = Join-Path (Join-Path $env:USERPROFILE ".opencode") "bin"
$script:OPENCODE_EXE = Join-Path (Join-Path $env:USERPROFILE ".opencode") "bin\opencode.exe"
$script:OPENCODE_CONFIG_DIR = Join-Path (Join-Path $env:USERPROFILE ".config") "opencode"
$script:OPENCODE_PORT = 3003

# Dream Host Agent (host-level extension lifecycle manager)
$script:DREAM_AGENT_PORT       = 7710
$script:DREAM_AGENT_PID_FILE   = Join-Path (Join-Path $script:DS_INSTALL_DIR "data") "dream-host-agent.pid"
$script:DREAM_AGENT_LOG_FILE   = Join-Path (Join-Path $script:DS_INSTALL_DIR "data") "dream-host-agent.log"
$script:DREAM_AGENT_HEALTH_URL = "http://127.0.0.1:7710/health"
$script:DREAM_AGENT_TASK_NAME  = "DreamServerHostAgent"

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
