# ============================================================================
# Dream Server Windows Installer -- Phase 05: Docker Validation
# ============================================================================
# Part of: installers/windows/phases/
# Purpose: Deep Docker health check -- daemon responsiveness, Compose v1/v2
#          detection, NVIDIA GPU passthrough smoke test, compose file syntax
#          validation. On Windows, Docker Desktop is a prerequisite (installed
#          by the user); this phase does NOT install Docker.
#
# Reads:
#   $gpuInfo     -- from phase 02, for GPU passthrough test
#   $sourceRoot  -- from orchestrator, for compose syntax validation
#   $dryRun      -- skip live checks
#   $script:DOCKER_COMPOSE_CMD  -- from constants.ps1 (default: "docker compose")
#
# Writes:
#   $dockerComposeCmd  -- string: resolved compose command
#                         ("docker compose" or "docker-compose")
#
# Modder notes:
#   To add Podman support, add a Podman detection branch after the Docker
#   Compose v1 fallback.
# ============================================================================

Write-Phase -Phase 5 -Total 13 -Name "DOCKER VALIDATION" -Estimate "~15 seconds"
Write-AI "Validating container runtime..."

# ── Docker daemon health ──────────────────────────────────────────────────────
if ($dryRun) {
    Write-AI "[DRY RUN] Would verify Docker daemon is responsive (docker info)"
    Write-AI "[DRY RUN] Would detect Docker Compose v1 vs v2"
    $dockerComposeCmd = $script:DOCKER_COMPOSE_CMD
} else {
    # Suppress stderr -- `docker info` emits warnings to stderr on first run
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $null = & docker info 2>&1
    $dockerInfoExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP

    if ($dockerInfoExit -ne 0) {
        Write-AIError "Docker daemon is not responding (docker info exit code: $dockerInfoExit)."
        Write-AI "  Make sure Docker Desktop is running and the WSL2 backend is active."
        Write-AI "  Start Docker Desktop from the Start Menu, wait for it to fully initialize,"
        Write-AI "  then re-run this installer."
        throw "Docker daemon is not responding"
    }
    Write-AISuccess "Docker daemon healthy"

    # ── Docker Compose detection (prefer v2, fall back to v1) ─────────────────
    $dockerComposeCmd = ""
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"

    # Try v2: `docker compose version`
    $null = & docker compose version 2>&1
    if ($LASTEXITCODE -eq 0) {
        $dockerComposeCmd = "docker compose"
    } else {
        # Try v1: standalone `docker-compose`
        $dcCmd = Get-Command docker-compose -ErrorAction SilentlyContinue
        if ($dcCmd) {
            $dockerComposeCmd = "docker-compose"
            Write-AIWarn "Docker Compose v1 (docker-compose) detected. Upgrade to v2 is recommended."
        }
    }
    $ErrorActionPreference = $prevEAP

    if (-not $dockerComposeCmd) {
        Write-AIError "Docker Compose not found (tried: 'docker compose' and 'docker-compose')."
        Write-AI "  Install Docker Desktop, which bundles Compose v2:"
        Write-AI "  https://docs.docker.com/desktop/install/windows-install/"
        throw "Docker Compose not found"
    }
    Write-AISuccess "Docker Compose available: $dockerComposeCmd"

    # A reinstall can begin while Docker Desktop is still starting. If old Dream
    # containers come back after the install tree was removed, file bind mounts
    # may recreate missing host-side files as directories. Remove stale Dream
    # containers while Docker is definitely available, before Phase 06 rewrites
    # the bind-mounted install tree.
    try {
        $_dreamContainerNames = @(& docker ps -a --format "{{.Names}}" 2>$null | Where-Object { $_ -like "dream-*" })
        if ($_dreamContainerNames.Count -gt 0) {
            Write-AI "Stopping existing Dream containers before reinstall..."
            $null = & docker rm -f @_dreamContainerNames 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-AISuccess "Stopped existing Dream containers"
            } else {
                Write-AIWarn "Could not remove all existing Dream containers; continuing with file-path repair."
            }
        }
    } catch {
        Write-AIWarn "Could not inspect existing Dream containers: $($_.Exception.Message)"
    }

    # ── Compose file syntax validation ────────────────────────────────────────
    # Quick config check on the base compose file to catch syntax errors early.
    $_baseCompose = Join-Path $sourceRoot "docker-compose.base.yml"
    if (Test-Path $_baseCompose) {
        Push-Location $sourceRoot
        try {
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = "SilentlyContinue"
            $null = & docker compose -f "docker-compose.base.yml" config 2>&1
            $composeConfigExit = $LASTEXITCODE
            $ErrorActionPreference = $prevEAP

            if ($composeConfigExit -ne 0) {
                Write-AIWarn "Compose stack syntax check returned non-zero (may be OK if overlays are missing at this stage)."
            } else {
                Write-AISuccess "docker-compose.base.yml syntax OK"
            }
        } finally {
            Pop-Location
        }
    }

    # ── NVIDIA GPU passthrough smoke test ─────────────────────────────────────
    # Only run if NVIDIA GPU detected AND WSL2 backend is confirmed.
    # This test starts a minimal container with --gpus all and checks that
    # nvidia-smi is accessible. If it fails, Phase 08 falls back to CPU-only
    # inference (docker-compose.cpu.yml) instead of crashing docker compose up.
    $script:gpuPassthroughFailed = $false
    if ($gpuInfo.Backend -eq "nvidia" -and $preflight_docker -and $preflight_docker.WSL2Backend) {
        Write-AI "Testing NVIDIA GPU passthrough in Docker (non-fatal)..."
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        $gpuTestOutput = & docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi 2>&1
        $gpuTestExit = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP

        if ($gpuTestExit -eq 0) {
            Write-AISuccess "NVIDIA GPU passthrough confirmed in Docker"
            $script:gpuPassthroughFailed = $false
        } else {
            # Attempt automatic recovery before falling back to CPU
            Write-AIWarn "GPU passthrough test failed. Attempting automatic fix..."

            # Step 1: WSL kernel refresh (fixes post-driver-update staleness)
            Write-AI "  Restarting WSL2 kernel..."
            & wsl --shutdown 2>$null
            Start-Sleep -Seconds 5

            $ErrorActionPreference = "SilentlyContinue"
            $retryOutput = & docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi 2>&1
            $retryExit = $LASTEXITCODE
            $ErrorActionPreference = $prevEAP

            if ($retryExit -eq 0) {
                Write-AISuccess "GPU passthrough recovered after WSL restart"
                $script:gpuPassthroughFailed = $false
            } else {
                # Step 2: Install NVIDIA Container Toolkit in WSL2
                Write-AI "  Installing NVIDIA Container Toolkit in WSL2..."
                $toolkitTmp = Join-Path $env:TEMP "dream-nvidia-toolkit-install.sh"
                $toolkitScript = @'
#!/bin/bash
set -e
if command -v nvidia-ctk &>/dev/null; then
    echo "NVIDIA Container Toolkit already installed"
    exit 0
fi
distribution=$(. /etc/os-release; echo ${ID}${VERSION_ID})
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
    sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg 2>/dev/null
curl -s -L "https://nvidia.github.io/libnvidia-container/${distribution}/libnvidia-container.list" | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
sudo apt-get update -qq
sudo apt-get install -y -qq nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
'@
                [System.IO.File]::WriteAllText($toolkitTmp, $toolkitScript.Replace("`r`n", "`n"))
                $wslPath = & wsl wslpath -u ($toolkitTmp.Replace('\', '\\')) 2>$null
                & wsl bash $wslPath 2>&1 | ForEach-Object { Write-Host "    $_" }
                Remove-Item -Path $toolkitTmp -Force -ErrorAction SilentlyContinue

                # Restart WSL to pick up the new runtime config
                & wsl --shutdown 2>$null
                Start-Sleep -Seconds 5

                # Step 3: Final retry
                $ErrorActionPreference = "SilentlyContinue"
                $finalOutput = & docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi 2>&1
                $finalExit = $LASTEXITCODE
                $ErrorActionPreference = $prevEAP

                if ($finalExit -eq 0) {
                    Write-AISuccess "GPU passthrough working after toolkit installation"
                    $script:gpuPassthroughFailed = $false
                } else {
                    Write-AIWarn "GPU passthrough still failing after auto-fix attempts."
                    Write-AI "  Continuing with CPU-only inference (slower)."
                    Write-AI "  Manual fix: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
                    $script:gpuPassthroughFailed = $true
                }
            }
        }
    }
}
