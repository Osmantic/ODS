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

$script:ODS_VERSION = "2.5.3"

# Install location (override via $env:ODS_HOME)
# NOTE: $(if ...) syntax required for PS 5.1 compatibility (bare if-as-expression is PS 7+ only)
$script:ODS_INSTALL_DIR = $(if ($env:ODS_HOME) { $env:ODS_HOME } else { Join-Path $env:USERPROFILE "ods" })

# Logging
$script:ODS_LOG_FILE = Join-Path $env:TEMP "ods-install.log"
$script:ODS_PREFLIGHT_REPORT = Join-Path $env:TEMP "ods-windows-preflight.json"

# Native inference server paths (AMD path)
# PID file is shared -- only one native inference server runs at a time
$script:INFERENCE_PID_FILE = Join-Path (Join-Path $script:ODS_INSTALL_DIR "data") "llama-server.pid"

# AMD Lemonade (preferred AMD backend: Vulkan + NPU + ROCm)
$script:LEMONADE_VERSION     = "10.0.0"
$script:LEMONADE_MSI_FILE    = "lemonade-server-minimal.msi"
$script:LEMONADE_MSI_URL     = "https://github.com/lemonade-sdk/lemonade/releases/download/v$($script:LEMONADE_VERSION)/$($script:LEMONADE_MSI_FILE)"
# Default path; install-windows.ps1 resolves both Program Files roots at runtime.
$script:LEMONADE_INSTALL_DIR = Join-Path $env:ProgramFiles "Lemonade Server"
$script:LEMONADE_EXE         = Join-Path (Join-Path $script:LEMONADE_INSTALL_DIR "bin") "lemonade-server.exe"
$script:LEMONADE_PORT        = 8080
$script:LEMONADE_API_KEY     = "lemonade"
$script:LEMONADE_HEALTH_URL  = "http://127.0.0.1:8080/api/v1/health"

# llama-server fallback (Vulkan build, used if Lemonade install is declined/fails)
$script:LLAMA_SERVER_DIR = Join-Path $script:ODS_INSTALL_DIR "llama-server"
$script:LLAMA_SERVER_EXE = Join-Path $script:LLAMA_SERVER_DIR "llama-server.exe"
$script:LLAMA_CPP_RELEASE_TAG = "b8248"
$script:LLAMA_CPP_VULKAN_ASSET = "llama-$($script:LLAMA_CPP_RELEASE_TAG)-bin-win-vulkan-x64.zip"
$script:LLAMA_CPP_VULKAN_URL = "https://github.com/ggml-org/llama.cpp/releases/download/$($script:LLAMA_CPP_RELEASE_TAG)/$($script:LLAMA_CPP_VULKAN_ASSET)"

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
$script:OPENCODE_VERSION = "1.2.18"
$script:OPENCODE_ZIP = "opencode-windows-x64.zip"
$script:OPENCODE_URL = "https://github.com/anomalyco/opencode/releases/download/v$($script:OPENCODE_VERSION)/$($script:OPENCODE_ZIP)"
$script:OPENCODE_DIR = Join-Path $env:USERPROFILE ".opencode"
$script:OPENCODE_BIN = Join-Path (Join-Path $env:USERPROFILE ".opencode") "bin"
$script:OPENCODE_EXE = Join-Path (Join-Path $env:USERPROFILE ".opencode") "bin\opencode.exe"
$script:OPENCODE_CONFIG_DIR = Join-Path (Join-Path $env:USERPROFILE ".config") "opencode"
$script:OPENCODE_PORT = 3003

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

function Get-ODSModelsDir {
    <#
    .SYNOPSIS
        Resolve the ODS models directory on Windows.
    .DESCRIPTION
        Precedence:
          1. $ModelsDirOverride parameter
          2. $env:ODS_MODELS_DIR
          3. $env:MODELS_DIR
          4. ODS_WIN_MODELS_DIR or MODELS_DIR in .env file (if present)
          5. Default fallback: $InstallDir\data\models
    #>
    [CmdletBinding()]
    param(
        [string]$InstallDir = $script:ODS_INSTALL_DIR,
        [string]$ModelsDirOverride
    )

    if (-not [string]::IsNullOrWhiteSpace($ModelsDirOverride)) {
        return [System.IO.Path]::GetFullPath($ModelsDirOverride)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ODS_MODELS_DIR)) {
        return [System.IO.Path]::GetFullPath($env:ODS_MODELS_DIR)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:MODELS_DIR)) {
        return [System.IO.Path]::GetFullPath($env:MODELS_DIR)
    }
    if (-not [string]::IsNullOrWhiteSpace($InstallDir)) {
        $envPath = Join-Path $InstallDir ".env"
        if (Test-Path -LiteralPath $envPath -PathType Leaf) {
            $lines = Get-Content -LiteralPath $envPath -ErrorAction SilentlyContinue
            foreach ($line in $lines) {
                if ($line -match "^(?:ODS_WIN_MODELS_DIR|MODELS_DIR)=\s*(.+)\s*$") {
                    $val = $Matches[1].Trim().Trim('"').Trim("'")
                    if (-not [string]::IsNullOrWhiteSpace($val)) {
                        return [System.IO.Path]::GetFullPath($val)
                    }
                }
            }
        }
        return Join-Path (Join-Path $InstallDir "data") "models"
    }
    return $null
}

