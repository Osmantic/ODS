# ============================================================================
# ODS Windows Installer -- UI Helpers
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Colored output, phase headers, progress, banners
#
# Matches the CRT narrator voice from installers/lib/ui.sh
# ============================================================================

function Write-ODSBanner {
    $banner = @"

   OOOOO  DDDD   SSSSS
  OO   OO DD DD SS
  OO   OO DD DD  SSS
  OO   OO DD DD    SS
   OOOOO  DDDD  SSSS

"@
    Write-Host $banner -ForegroundColor Green
    Write-Host "  ODSGATE Windows Installer v$($script:ODS_VERSION)" -ForegroundColor White
    Write-Host "  One command to a full local AI stack." -ForegroundColor DarkGray
    Write-Host ""
}

function Write-Phase {
    param(
        [int]$Phase,
        [int]$Total,
        [string]$Name,
        [string]$Estimate = ""
    )
    $elapsed = ((Get-Date) - $script:INSTALL_START).ToString("hh\:mm\:ss")
    Write-Host ""
    Write-Host "  ODSGATE SEQUENCE [$elapsed]" -ForegroundColor DarkGray -NoNewline
    Write-Host "  PHASE $Phase/$Total" -ForegroundColor White -NoNewline
    Write-Host " -- $Name" -ForegroundColor Green
    if ($Estimate) {
        Write-Host "  Estimated: $Estimate" -ForegroundColor DarkGray
    }
    Write-Host ("  " + ("-" * 60)) -ForegroundColor DarkGray
}

function Write-AI {
    param([string]$Message)
    Write-Host "  > $Message" -ForegroundColor Green
}

function Write-AISuccess {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-AIWarn {
    param([string]$Message)
    Write-Host "  [!!] $Message" -ForegroundColor Yellow
}

function Write-AIError {
    param([string]$Message)
    Write-Host "  [XX] $Message" -ForegroundColor Red
}

function Write-Chapter {
    param([string]$Title)
    Write-Host ""
    Write-Host ("  " + ("=" * 60)) -ForegroundColor DarkGray
    Write-Host "  $Title" -ForegroundColor White
    Write-Host ("  " + ("=" * 60)) -ForegroundColor DarkGray
}

function Write-InfoBox {
    param(
        [string]$Label,
        [string]$Value
    )
    Write-Host "  $Label" -ForegroundColor DarkGray -NoNewline
    Write-Host " $Value" -ForegroundColor White
}

function Get-ODSPositiveIntEnv {
    param(
        [string]$Name,
        [int]$Default
    )

    $raw = [Environment]::GetEnvironmentVariable($Name)
    $parsed = 0
    if ([int]::TryParse($raw, [ref]$parsed) -and $parsed -gt 0) {
        return $parsed
    }
    return $Default
}

function Get-ODSCurlDownloadHttpArgs {
    $httpVersion = [Environment]::GetEnvironmentVariable("ODS_DOWNLOAD_HTTP_VERSION")
    if ([string]::IsNullOrWhiteSpace($httpVersion)) {
        $httpVersion = [Environment]::GetEnvironmentVariable("ODS_BOOTSTRAP_DOWNLOAD_HTTP_VERSION")
    }
    if ([string]::IsNullOrWhiteSpace($httpVersion)) {
        $httpVersion = "http1.1"
    }

    switch -Regex ($httpVersion) {
        "^(auto)$" { return @() }
        "^(1|1\.1|http1|http1\.1)$" { return @("--http1.1") }
        "^(2|http2)$" { return @("--http2") }
        default {
            Write-AIWarn "Unknown ODS_DOWNLOAD_HTTP_VERSION=$httpVersion; using http1.1."
            return @("--http1.1")
        }
    }
}

function Test-ODSHuggingFaceResolveUrl {
    param([string]$Url)
    return ($Url -match '^https://(www\.)?(huggingface\.co|hf\.co)/')
}

function Get-ODSHuggingFaceDownloadHelper {
    $roots = @()
    $_sourceRoot = Get-Variable -Name SourceRoot -Scope Script -ValueOnly -ErrorAction SilentlyContinue
    if (-not [string]::IsNullOrWhiteSpace($_sourceRoot)) { $roots += $_sourceRoot }
    $_installDir = Get-Variable -Name installDir -Scope Script -ValueOnly -ErrorAction SilentlyContinue
    if (-not [string]::IsNullOrWhiteSpace($_installDir)) { $roots += $_installDir }
    if (-not [string]::IsNullOrWhiteSpace($env:ODS_HOME)) { $roots += $env:ODS_HOME }

    foreach ($root in $roots) {
        $candidate = Join-Path $root "scripts\download-hf-artifact.py"
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

function Invoke-ODSNativeQuiet {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    $prevEAP = $ErrorActionPreference
    $exitCode = 1
    try {
        # Expected failed native probes must return an exit code under
        # Windows PowerShell 5.1 strict installer semantics.
        $ErrorActionPreference = "SilentlyContinue"
        & $FilePath @Arguments 2>&1 | Out-Null
        if ($null -ne $LASTEXITCODE) { $exitCode = $LASTEXITCODE }
    } catch {
        $exitCode = 1
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    return $exitCode
}

function Get-ODSPythonDownloadCommand {
    $candidates = @(
        @{ FilePath = "python3"; PrefixArgs = @() },
        @{ FilePath = "python"; PrefixArgs = @() },
        @{ FilePath = "py"; PrefixArgs = @("-3") }
    )

    foreach ($candidate in $candidates) {
        $filePath = $candidate.FilePath
        $prefixArgs = @($candidate.PrefixArgs)
        $probeArgs = @($prefixArgs) + @("-c", "import sys")
        if ((Invoke-ODSNativeQuiet -FilePath $filePath -Arguments $probeArgs) -eq 0) {
            return [pscustomobject]@{
                FilePath = $filePath
                PrefixArgs = $prefixArgs
            }
        }
    }
    return $null
}

function Invoke-ODSHuggingFaceDownloadFallback {
    param(
        [string]$Url,
        [string]$Destination
    )

    if (-not (Test-ODSHuggingFaceResolveUrl -Url $Url)) {
        return $false
    }

    $helper = Get-ODSHuggingFaceDownloadHelper
    if (-not $helper) {
        return $false
    }

    $python = Get-ODSPythonDownloadCommand
    if (-not $python) {
        return $false
    }

    $checkArgs = @($python.PrefixArgs) + @("-c", "import huggingface_hub, hf_xet")
    if ((Invoke-ODSNativeQuiet -FilePath $python.FilePath -Arguments $checkArgs) -ne 0) {
        $installArgs = @($python.PrefixArgs) + @("-m", "pip", "install", "--user", "-q", "huggingface_hub[hf_xet]>=0.27")
        Invoke-ODSNativeQuiet -FilePath $python.FilePath -Arguments $installArgs | Out-Null
    }

    Write-AI "Retrying with Hugging Face client..."
    $downloadArgs = @($python.PrefixArgs) + @($helper, $Url, $Destination)
    & $python.FilePath @downloadArgs
    if ($LASTEXITCODE -eq 0 -and (Test-Path $Destination) -and ((Get-Item $Destination).Length -gt 0)) {
        return $true
    }
    return $false
}

function Show-ProgressDownload {
    param(
        [string]$Url,
        [string]$Destination,
        [string]$Label = "Downloading"
    )
    Write-AI "$Label..."
    # Use curl.exe (ships with Windows 10+) for resume-capable download with progress
    # Direct invocation (&) instead of Start-Process so the progress bar is visible
    $partFile = "$Destination.part"
    $connectTimeout = Get-ODSPositiveIntEnv -Name "ODS_DOWNLOAD_CONNECT_TIMEOUT" -Default 30
    $lowSpeedTime = Get-ODSPositiveIntEnv -Name "ODS_DOWNLOAD_LOW_SPEED_TIME" -Default 120
    $lowSpeedLimit = Get-ODSPositiveIntEnv -Name "ODS_DOWNLOAD_LOW_SPEED_LIMIT" -Default 262144
    $curlArgs = @(
        "-C", "-",
        "-L",
        "--progress-bar",
        "--connect-timeout", "$connectTimeout",
        "--speed-time", "$lowSpeedTime",
        "--speed-limit", "$lowSpeedLimit"
    )
    $curlArgs += Get-ODSCurlDownloadHttpArgs
    $curlArgs += @("-o", $partFile, $Url)
    & curl.exe @curlArgs
    $curlExit = $LASTEXITCODE
    if ($curlExit -eq 0 -and (Test-Path $partFile)) {
        Move-Item -Path $partFile -Destination $Destination -Force
        Write-AISuccess "$Label complete"
        return $true
    } else {
        if (Invoke-ODSHuggingFaceDownloadFallback -Url $Url -Destination $partFile) {
            Move-Item -Path $partFile -Destination $Destination -Force
            Write-AISuccess "$Label complete"
            return $true
        }
        $curlErrors = @{ 6="Could not resolve host"; 7="Connection refused"; 18="Partial transfer"; 28="Timeout"; 35="SSL error"; 56="Network failure" }
        $hint = $(if ($curlErrors.ContainsKey($curlExit)) { " ($($curlErrors[$curlExit]))" } else { "" })
        Write-AIError "$Label failed (curl exit code: $curlExit$hint)"
        Write-AI "Re-run the installer to resume the download."
        return $false
    }
}

function Invoke-DownloadWithRetry {
    <#
    .SYNOPSIS
        Download a file with automatic retry on failure.
    .DESCRIPTION
        Wraps Show-ProgressDownload with exponential backoff retry logic.
        Retries up to MaxRetries times with increasing delays (2s, 5s, 10s).
    .PARAMETER Url
        URL to download from.
    .PARAMETER Destination
        Local file path to save to.
    .PARAMETER Label
        Display label for progress messages (default: "Downloading").
    .PARAMETER MaxRetries
        Maximum number of retry attempts (default: 3).
    .OUTPUTS
        $true on success, $false on final failure.
    #>
    param(
        [string]$Url,
        [string]$Destination,
        [string]$Label = "Downloading",
        [int]$MaxRetries = 3
    )

    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        if ($attempt -gt 1) {
            $delay = @(2, 5, 10)[$attempt - 2]
            Write-AI "Retry attempt $attempt of $MaxRetries (waiting ${delay}s)..."
            Start-Sleep -Seconds $delay
        }

        $success = Show-ProgressDownload -Url $Url -Destination $Destination -Label $Label

        if ($success) {
            # Verify file exists and has content
            if ((Test-Path $Destination) -and ((Get-Item $Destination).Length -gt 0)) {
                return $true
            } else {
                Write-AIWarn "Download reported success but file is missing or empty"
            }
        }
    }

    Write-AIError "Download failed after $MaxRetries attempts"
    return $false
}

function Invoke-ExtractionWithRetry {
    <#
    .SYNOPSIS
        Extract a zip file with validation and retry on failure.
    .DESCRIPTION
        Validates zip integrity before extraction, retries on failure,
        and cleans up partial extractions. Uses Test-ZipIntegrity to
        catch corrupted downloads before attempting extraction.
    .PARAMETER ZipPath
        Path to the zip file to extract.
    .PARAMETER DestinationPath
        Directory to extract files into.
    .PARAMETER MaxRetries
        Maximum number of extraction attempts (default: 3).
    .OUTPUTS
        $true on success, $false on final failure.
    #>
    param(
        [string]$ZipPath,
        [string]$DestinationPath,
        [int]$MaxRetries = 3
    )

    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        if ($attempt -gt 1) {
            Write-AI "Extraction retry attempt $attempt of $MaxRetries..."
        }

        # Validate zip integrity before attempting extraction
        $zipValid = Test-ZipIntegrity -Path $ZipPath
        if (-not $zipValid.Valid) {
            Write-AIWarn "Zip validation failed: $($zipValid.ErrorMessage)"
            if ($attempt -lt $MaxRetries) {
                Write-AI "Zip file may be corrupted, will retry..."
                Start-Sleep -Seconds 2
                continue
            } else {
                Write-AIError "Zip file is corrupt after $MaxRetries attempts"
                return $false
            }
        }

        # Attempt extraction
        try {
            # Remove partial extraction if it exists
            if (Test-Path $DestinationPath) {
                Write-AI "Cleaning up previous extraction attempt..."
                Remove-Item -Path $DestinationPath -Recurse -Force -ErrorAction Stop
            }

            # Create parent directory if needed
            $parentDir = Split-Path -Parent $DestinationPath
            if (-not (Test-Path $parentDir)) {
                New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
            }

            # Extract
            Expand-Archive -Path $ZipPath -DestinationPath $DestinationPath -Force -ErrorAction Stop

            # Verify extraction succeeded (check if directory exists and has content)
            if ((Test-Path $DestinationPath) -and ((Get-ChildItem $DestinationPath).Count -gt 0)) {
                return $true
            } else {
                Write-AIWarn "Extraction completed but destination is empty"
            }
        }
        catch {
            Write-AIWarn "Extraction failed: $($_.Exception.Message)"
            if ($attempt -lt $MaxRetries) {
                Start-Sleep -Seconds 2
            }
        }
    }

    Write-AIError "Extraction failed after $MaxRetries attempts"
    return $false
}

function Write-SuccessCard {
    param(
        [string]$WebUIPort = "3000",
        [string]$DashboardPort = "3001"
    )
    # Detect local IP for network access (DHCP, static, or manual -- exclude loopback + APIPA)
    $localIP = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.InterfaceAlias -notlike "*Loopback*" -and
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.PrefixOrigin -in @("Dhcp", "Manual")
        } | Select-Object -First 1).IPAddress
    if (-not $localIP) { $localIP = "your-ip" }

    Write-Host ""
    Write-Host ("  " + ("=" * 60)) -ForegroundColor Green
    Write-Host ""
    Write-Host "       THE GATEWAY IS OPEN" -ForegroundColor White
    Write-Host ""
    Write-Host "       Chat UI:    " -ForegroundColor DarkGray -NoNewline
    Write-Host "http://localhost:$WebUIPort" -ForegroundColor White
    Write-Host "       Dashboard:  " -ForegroundColor DarkGray -NoNewline
    Write-Host "http://localhost:$DashboardPort" -ForegroundColor White
    $_bindAddr = ""
    $_envPath = Join-Path $script:ODS_INSTALL_DIR ".env"
    if (Test-Path $_envPath) {
        Get-Content $_envPath | ForEach-Object {
            if ($_ -match "^BIND_ADDRESS=(.*)$") { $_bindAddr = $Matches[1].Trim() }
        }
    }
    if ($_bindAddr -eq "0.0.0.0") {
        Write-Host "       Network:    " -ForegroundColor DarkGray -NoNewline
        Write-Host "http://${localIP}:$WebUIPort" -ForegroundColor White
    } else {
        Write-Host "       LAN access: " -ForegroundColor DarkGray -NoNewline
        Write-Host "Set BIND_ADDRESS=0.0.0.0 in .env or reinstall with -Lan" -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "       Manage:     " -ForegroundColor DarkGray -NoNewline
    Write-Host ".\ods.ps1 status" -ForegroundColor Cyan
    Write-Host "       Logs:       " -ForegroundColor DarkGray -NoNewline
    Write-Host ".\ods.ps1 logs llama-server" -ForegroundColor Cyan
    Write-Host "       Stop:       " -ForegroundColor DarkGray -NoNewline
    Write-Host ".\ods.ps1 stop" -ForegroundColor Cyan
    Write-Host ""
    $elapsed = ((Get-Date) - $script:INSTALL_START).ToString("mm\:ss")
    Write-Host "       Install completed in $elapsed" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host ("  " + ("=" * 60)) -ForegroundColor Green
    Write-Host ""
}
