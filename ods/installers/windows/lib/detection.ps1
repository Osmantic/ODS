# ============================================================================
# ODS Windows Installer -- Hardware Detection
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: GPU detection (NVIDIA via nvidia-smi, AMD via WMI), Docker Desktop
#          validation, system RAM detection
#
# Canonical source: installers/lib/detection.sh (keep tier thresholds in sync)
#
# Modder notes:
#   Add new GPU vendors or APU detection logic here.
#   Strix Halo detection: small dedicated VRAM + large system RAM = unified memory.
# ============================================================================

function Get-GpuInfo {
    <#
    .SYNOPSIS
        Detect GPU hardware and return a structured info hashtable.
    .OUTPUTS
        @{ Backend; Name; VramMB; Count; MemoryType; DeviceId; DriverVersion }
    #>

    # ── Try NVIDIA first (nvidia-smi ships with NVIDIA drivers) ──
    $nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($nvidiaSmi) {
        try {
            $raw = & nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader 2>$null
            if ($LASTEXITCODE -eq 0 -and $raw) {
                $lines = @($raw -split "`n" | Where-Object { $_.Trim() })
                $first = $lines[0] -split ","
                $gpuName = $first[0].Trim()
                $vramStr = $first[1].Trim() -replace "[^\d]", ""
                $vramMB  = [int]$vramStr
                $driverVer = $first[2].Trim()
                $computeCap = $first[3].Trim()
                $gpuCount = $lines.Count

                # Extract major driver version for minimum check
                $driverMajor = 0
                if ($driverVer -match "^(\d+)") { $driverMajor = [int]$Matches[1] }

                # Blackwell detection: compute capability 12.0+ (sm_120)
                $isBlackwell = $false
                if ($computeCap -match "^(\d+)") {
                    $ccMajor = [int]$Matches[1]
                    if ($ccMajor -ge 12) { $isBlackwell = $true }
                }

                return @{
                    Backend       = "nvidia"
                    Name          = $gpuName
                    VramMB        = $vramMB
                    Count         = $gpuCount
                    MemoryType    = "discrete"
                    DeviceId      = ""
                    DriverVersion = $driverVer
                    DriverMajor   = $driverMajor
                    ComputeCap    = $computeCap
                    IsBlackwell   = $isBlackwell
                }
            }
        } catch {
            # nvidia-smi exists but failed -- fall through to AMD
        }
    }

    # ── Try AMD via WMI (Win32_VideoController) ──
    try {
        $gpus = Get-CimInstance Win32_VideoController -ErrorAction Stop |
            Where-Object { $_.Name -match "AMD|Radeon" }

        if ($gpus) {
            $primary = @($gpus)[0]
            $gpuName = $primary.Name
            $deviceId = $primary.PNPDeviceID

            # WMI AdapterRAM is a 32-bit field (maxes at 4 GB for discrete GPUs)
            # For APUs with unified memory, this is typically small (512MB–2GB)
            $adapterRamMB = 0
            if ($primary.AdapterRAM) {
                $adapterRamMB = [math]::Floor([uint64]$primary.AdapterRAM / 1048576)
            }

            # System RAM for unified memory calculation
            $systemRamGB = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1073741824)

            # Strix Halo detection heuristic:
            #   - Small AdapterRAM (WMI caps at 4GB) + large system RAM (>= 64GB) = unified memory APU
            #   - Marketing name often contains "Ryzen AI" or specific model patterns
            $isUnified = $false
            $effectiveVramMB = $adapterRamMB

            if ($adapterRamMB -le 4096 -and $systemRamGB -ge 32) {
                # Likely an APU with unified memory
                $isUnified = $true
                # Effective VRAM: ~75% of system RAM is usable for GPU on Strix Halo
                $effectiveVramMB = [math]::Floor($systemRamGB * 0.75 * 1024)
            }

            # Check for Strix Halo specific identifiers
            if ($gpuName -match "Strix|AI MAX|AI 300|AI 395") {
                $isUnified = $true
                $effectiveVramMB = [math]::Floor($systemRamGB * 0.75 * 1024)
            }

            $driverVer = $primary.DriverVersion
            if (-not $driverVer) { $driverVer = "unknown" }

            # Detect AMD NPU (Ryzen AI) for Lemonade hybrid NPU+GPU mode
            $hasNpu = $false
            try {
                $npuDevices = Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match "AMD IPU|Ryzen AI|NPU|Neural Processing" }
                if ($npuDevices) { $hasNpu = $true }
            } catch { }

            return @{
                Backend       = "amd"
                Name          = $gpuName
                VramMB        = $effectiveVramMB
                Count         = @($gpus).Count
                MemoryType    = $(if ($isUnified) { "unified" } else { "discrete" })
                DeviceId      = $deviceId
                DriverVersion = $driverVer
                DriverMajor   = 0
                ComputeCap    = ""
                IsBlackwell   = $false
                SystemRamGB   = $systemRamGB
                HasNpu        = $hasNpu
            }
        }
    } catch {
        # WMI query failed -- fall through to no GPU
    }

    # ── No GPU detected ──
    return @{
        Backend       = "none"
        Name          = "None"
        VramMB        = 0
        Count         = 0
        MemoryType    = "none"
        DeviceId      = ""
        DriverVersion = ""
        DriverMajor   = 0
        ComputeCap    = ""
        IsBlackwell   = $false
    }
}

function Get-SystemRamGB {
    <#
    .SYNOPSIS
        Return total physical RAM in GB (rounded down).
    #>
    try {
        $totalBytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
        return [math]::Floor($totalBytes / 1073741824)
    } catch {
        return 0
    }
}

function Test-DockerDesktop {
    <#
    .SYNOPSIS
        Verify Docker Desktop is installed, running, and using the WSL2 backend.
    .OUTPUTS
        @{ Installed; Running; Version; WSL2Backend; GpuSupport }
    #>
    $result = @{
        Installed   = $false
        Running     = $false
        Version     = ""
        WSL2Backend = $false
        GpuSupport  = $false
    }

    # Check if docker CLI is available
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) { return $result }
    $result.Installed = $true

    # Check if Docker daemon is responsive
    try {
        $versionJson = docker version --format "{{json .}}" 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
        if ($LASTEXITCODE -eq 0 -and $versionJson) {
            $result.Running = $true
            if ($versionJson.Server) {
                $result.Version = $versionJson.Server.Version
            } elseif ($versionJson.Client) {
                $result.Version = $versionJson.Client.Version
            }
        }
    } catch {
        # Docker not responding
        return $result
    }

    # Check for WSL2 backend via docker info
    try {
        $infoRaw = docker info --format "{{json .}}" 2>$null
        if ($infoRaw) {
            $info = $infoRaw | ConvertFrom-Json -ErrorAction SilentlyContinue
            if ($info) {
                # Docker Desktop on Windows with WSL2 shows "wsl" in the isolation mode
                # or the kernel version contains "microsoft" or "WSL"
                $kernelVersion = $info.KernelVersion
                if ($kernelVersion -match "microsoft|WSL") {
                    $result.WSL2Backend = $true
                }
                # Check for GPU support in Docker
                # On Windows Docker Desktop with WSL2 backend, GPU passthrough is
                # handled automatically -- there is no separate "nvidia" runtime like
                # on Linux. If WSL2 backend is detected + NVIDIA driver is present,
                # GPU support is available via --gpus flag / compose deploy.resources.
                if ($result.WSL2Backend) {
                    $nvsmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
                    if ($nvsmi) { $result.GpuSupport = $true }
                }
                # Also check Linux-style runtime (in case running Docker Engine directly)
                if ($info.Runtimes -and $info.Runtimes.nvidia) {
                    $result.GpuSupport = $true
                }
            }
        }
    } catch {
        # info parse failed, still functional
    }

    return $result
}

function Test-WslcRuntime {
    <#
    .SYNOPSIS
        Verify wslc.exe is installed and its daemon is responsive.
    .DESCRIPTION
        wslc is a Docker-compatible OCI container runtime shipping with WSL
        pre-release builds for Windows 11. It requires no Docker Desktop.

        GPU support is inferred from host nvidia-smi presence (CDI wires the
        GPU into the container without a separate runtime registration step).
        The actual CDI container smoke test runs later in phase 05, matching
        the Docker path where the container GPU test is also deferred.
    .OUTPUTS
        @{ Installed; Running; Version; GpuSupport; WslPreRelease }
    #>
    $result = @{
        Installed     = $false
        Running       = $false
        Version       = ""
        GpuSupport    = $false
        WslPreRelease = $false
    }

    # ── CLI presence ─────────────────────────────────────────────────────────
    $wslcCmd = Get-Command wslc -ErrorAction SilentlyContinue
    if (-not $wslcCmd) { return $result }
    $result.Installed = $true

    # ── Version (best-effort; pre-release format may vary) ───────────────────
    try {
        $versionLines = & wslc --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $versionLines) {
            $result.Version = (@($versionLines) | Select-Object -First 1).Trim()
        }
    } catch {
        # --version flag behavior undefined in early pre-release; non-fatal
    }

    # ── WSL pre-release confirmation ─────────────────────────────────────────
    # wslc ships exclusively with WSL pre-release builds, so its presence on
    # PATH already implies pre-release is active. We additionally confirm with
    # `wsl --version` as a sanity check, but treat any failure as non-fatal
    # because some pre-release environments suppress that subcommand.
    try {
        $null = & wsl --version 2>$null
        $result.WslPreRelease = ($LASTEXITCODE -eq 0)
    } catch {
        # wslc is present, so pre-release is implied even if wsl --version fails
        $result.WslPreRelease = $true
    }

    # ── Daemon responsiveness ────────────────────────────────────────────────
    # Mirrors Test-DockerDesktop: SilentlyContinue around the native call so
    # PS 5.1 does not wrap stderr as a terminating error, and finally always
    # restores ErrorActionPreference even if the call throws.
    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $null = & wslc info 2>$null
        if ($LASTEXITCODE -eq 0) {
            $result.Running = $true
        }
    } catch {
        # Daemon not responsive
    } finally {
        $ErrorActionPreference = $prevEAP
    }

    if (-not $result.Running) { return $result }

    # ── GPU support ──────────────────────────────────────────────────────────
    # CDI exposes NVIDIA GPUs to wslc containers via --device=nvidia.com/gpu=*.
    # No separate runtime registration is required (unlike Linux Docker Engine).
    # We mirror Test-DockerDesktop: check host nvidia-smi; the full container
    # smoke test (actually running nvidia-smi inside a wslc container) happens
    # in the dedicated Docker/wslc validation phase.
    $nvsmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($nvsmi) { $result.GpuSupport = $true }

    return $result
}

function Get-HostLogicalCpuCount {
    <#
    .SYNOPSIS
        Return the host logical CPU count with a safe fallback.
    #>
    try {
        $count = [int][Environment]::ProcessorCount
        if ($count -gt 0) { return $count }
    } catch { }
    return 1
}

function Get-DockerAvailableCpuCount {
    <#
    .SYNOPSIS
        Return the number of CPUs exposed to the Docker daemon.
    .DESCRIPTION
        Uses `docker info` first because Docker Desktop can expose fewer CPUs
        than the host actually has. Falls back to the host CPU count if Docker
        is unavailable or not yet running.
    #>
    try {
        $cpuRaw = docker info --format "{{.NCPU}}" 2>$null
        if ($LASTEXITCODE -eq 0 -and $cpuRaw -match "(\d+)") {
            $count = [int]$Matches[1]
            if ($count -gt 0) { return $count }
        }
    } catch { }
    return Get-HostLogicalCpuCount
}

function Get-LlamaCpuBudget {
    <#
    .SYNOPSIS
        Calculate an auto-capped CPU limit/reservation for llama-server.
    .PARAMETER GpuBackend
        Backend name used to choose default targets before capping to Docker.
    .OUTPUTS
        @{ Available; Limit; Reservation }
    #>
    param(
        [string]$GpuBackend = "cpu"
    )

    $available = Get-DockerAvailableCpuCount
    $desiredLimit = 8
    $desiredReservation = 1

    switch ($GpuBackend) {
        "amd" {
            $desiredLimit = 16
            $desiredReservation = 4
        }
        "nvidia" {
            $desiredLimit = 16
            $desiredReservation = 2
        }
        "intel" {
            $desiredLimit = 16
            $desiredReservation = 2
        }
        "sycl" {
            $desiredLimit = 16
            $desiredReservation = 2
        }
        "apple" {
            $desiredLimit = 8
            $desiredReservation = 2
        }
    }

    if ($available -lt 1) { $available = 1 }
    $limit = [Math]::Min($desiredLimit, $available)
    if ($limit -lt 1) { $limit = 1 }
    $reservation = [Math]::Min($desiredReservation, $limit)

    return @{
        Available   = $available
        Limit       = ("{0}.0" -f $limit)
        Reservation = ("{0}.0" -f $reservation)
    }
}

function Test-ModelIntegrity {
    <#
    .SYNOPSIS
        Verify a downloaded model file against its expected SHA256 hash.
    .PARAMETER Path
        Full path to the model file.
    .PARAMETER ExpectedHash
        Expected SHA256 hex string (lowercase).
    .OUTPUTS
        @{ Valid; ActualHash; ExpectedHash; SizeBytes }
    #>
    param(
        [string]$Path,
        [string]$ExpectedHash
    )

    if (-not (Test-Path $Path)) {
        return @{
            Valid        = $false
            ActualHash   = ""
            ExpectedHash = $ExpectedHash
            SizeBytes    = 0
        }
    }

    $fileInfo = Get-Item $Path
    $sizeBytes = $fileInfo.Length

    # Skip verification if no expected hash provided
    if (-not $ExpectedHash) {
        return @{
            Valid        = $true
            ActualHash   = "(skipped)"
            ExpectedHash = ""
            SizeBytes    = $sizeBytes
        }
    }

    # Compute SHA256 (streams the file, works for multi-GB files)
    $hash = (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLower()

    return @{
        Valid        = ($hash -eq $ExpectedHash.ToLower())
        ActualHash   = $hash
        ExpectedHash = $ExpectedHash.ToLower()
        SizeBytes    = $sizeBytes
    }
}

function Test-ZipIntegrity {
    <#
    .SYNOPSIS
        Validate a zip file's structure without extracting it.
    .DESCRIPTION
        Uses System.IO.Compression.ZipFile to verify the zip file can be opened
        and has a valid central directory. Catches the "Central Directory corrupt"
        error that occurs with incomplete or corrupted downloads.
    .PARAMETER Path
        Full path to the zip file to validate.
    .OUTPUTS
        @{ Valid; ErrorMessage; SizeBytes }
    #>
    param(
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return @{
            Valid        = $false
            ErrorMessage = "File not found"
            SizeBytes    = 0
        }
    }

    $fileInfo = Get-Item $Path
    $sizeBytes = $fileInfo.Length

    # Check for empty or suspiciously small files
    if ($sizeBytes -lt 100) {
        return @{
            Valid        = $false
            ErrorMessage = "File is too small to be a valid zip archive ($sizeBytes bytes)"
            SizeBytes    = $sizeBytes
        }
    }

    # Load System.IO.Compression.FileSystem if not already loaded
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue

    try {
        # Attempt to open the zip file (validates central directory)
        $zip = [System.IO.Compression.ZipFile]::OpenRead($Path)
        $entryCount = $zip.Entries.Count
        $zip.Dispose()

        return @{
            Valid        = $true
            ErrorMessage = ""
            SizeBytes    = $sizeBytes
        }
    }
    catch [System.IO.InvalidDataException] {
        # This is the "Central Directory corrupt" error from issue #209
        return @{
            Valid        = $false
            ErrorMessage = "Central Directory is invalid or corrupt"
            SizeBytes    = $sizeBytes
        }
    }
    catch {
        # Other errors (permissions, file locked, etc.)
        return @{
            Valid        = $false
            ErrorMessage = $_.Exception.Message
            SizeBytes    = $sizeBytes
        }
    }
}

function Test-DiskSpace {
    <#
    .SYNOPSIS
        Check if the target drive has enough free space.
    .PARAMETER Path
        Path on the drive to check (defaults to $env:USERPROFILE).
    .PARAMETER RequiredGB
        Minimum free GB needed (defaults to 20).
    .OUTPUTS
        @{ Drive; FreeGB; RequiredGB; Sufficient }
    #>
    param(
        [string]$Path = $env:USERPROFILE,
        [int]$RequiredGB = 20
    )

    $drive = (Resolve-Path $Path -ErrorAction SilentlyContinue).Drive
    if (-not $drive) {
        $driveLetter = $Path.Substring(0, 1)
        $drive = Get-PSDrive -Name $driveLetter -ErrorAction SilentlyContinue
    }

    $freeGB = 0
    if ($drive -and $drive.Free) {
        $freeGB = [math]::Floor($drive.Free / 1073741824)
    } else {
        # Fallback: use WMI
        try {
            $driveLetter = (Split-Path -Qualifier $Path).TrimEnd(":")
            $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='${driveLetter}:'" -ErrorAction Stop
            $freeGB = [math]::Floor($disk.FreeSpace / 1073741824)
        } catch {
            $freeGB = 0
        }
    }

    return @{
        Drive      = (Split-Path -Qualifier $Path)
        FreeGB     = $freeGB
        RequiredGB = $RequiredGB
        Sufficient = ($freeGB -ge $RequiredGB)
    }
}

function Test-PowerShellVersion {
    <#
    .SYNOPSIS
        Check if PowerShell version meets minimum requirement (5.1).
    #>
    $ver = $PSVersionTable.PSVersion
    return @{
        Version   = "$($ver.Major).$($ver.Minor)"
        Sufficient = ($ver.Major -ge 5 -and $ver.Minor -ge 1) -or ($ver.Major -ge 6)
    }
}
