# ============================================================================
# ODS Windows Installer -- Phase 07: Developer Tools
# ============================================================================
# Part of: installers/windows/phases/
# Purpose: Install OpenCode (AI coding IDE), Claude Code CLI, and Codex CLI.
#          Configures OpenCode to point at the local llama-server and creates
#          a manual launcher instead of auto-starting it at login.
#
# Reads:
#   $dryRun, $cloudMode         -- from orchestrator context
#   $installDir                 -- from orchestrator context
#   $tierConfig                 -- from phase 02 (LlmModel, MaxContext, GgufFile)
#   $script:OPENCODE_*          -- from lib/constants.ps1
#
# Writes:
#   (none -- tools installed to $env:USERPROFILE\.opencode)
#
# Modder notes:
#   Add new developer tools as separate helper blocks following the OpenCode
#   pattern (check → download → validate zip → extract → configure).
#   Node.js is checked and optionally installed for npm-based tools.
# ============================================================================

Write-Phase -Phase 7 -Total 13 -Name "DEVELOPER TOOLS" -Estimate "~2-5 minutes"

if ($dryRun) {
    Write-AI "[DRY RUN] Would install OpenCode v$($script:OPENCODE_VERSION) to $($script:OPENCODE_EXE)"
    Write-AI "[DRY RUN] Would configure OpenCode for local llama-server (model: $($tierConfig.LlmModel))"
    Write-AI "[DRY RUN] Would create a manual OpenCode launcher"
    if (-not $cloudMode) {
        Write-AI "[DRY RUN] Would check for Node.js and install Claude Code + Codex CLI via npm"
    }
    Write-AI "[DRY RUN] Would start ODS Host Agent on port $($script:ODS_AGENT_PORT)"
    Write-AI "[DRY RUN] Would register $($script:ODS_AGENT_TASK_NAME) scheduled task for login persistence"
    return
}

# ── OpenCode ──────────────────────────────────────────────────────────────────
# Config helpers are sourced from installers/windows/lib/opencode-config.ps1.
Write-AI "Setting up OpenCode AI coding assistant..."

if (-not (Test-Path $script:OPENCODE_EXE)) {
    Write-AI "Downloading OpenCode v$($script:OPENCODE_VERSION)..."
    $_ocZip = Join-Path $env:TEMP $script:OPENCODE_ZIP

    # Download with retry (resume-capable via curl.exe -C -)
    if (-not (Test-Path $_ocZip)) {
        $dlOk = Invoke-DownloadWithRetry `
            -Url         $script:OPENCODE_URL `
            -Destination $_ocZip `
            -Label       "OpenCode v$($script:OPENCODE_VERSION)"
        if (-not $dlOk) {
            Write-AIWarn "OpenCode download failed after retries -- skipping (install manually later)."
            Write-AI "  Manual: https://github.com/anomalyco/opencode/releases"
        }
    }

    if (Test-Path $_ocZip) {
        # Validate zip before extraction
        $_zipCheck = Test-ZipIntegrity -Path $_ocZip
        if (-not $_zipCheck.Valid) {
            Write-AIWarn "OpenCode archive is corrupt: $($_zipCheck.ErrorMessage)"
            Remove-Item $_ocZip -Force -ErrorAction SilentlyContinue
            Write-AIWarn "Skipping OpenCode (re-run installer to retry)"
        } else {
            # Extract to ~/.opencode/bin/
            New-Item -ItemType Directory -Path $script:OPENCODE_BIN -Force | Out-Null
            if (Invoke-ExtractionWithRetry -ZipPath $_ocZip -DestinationPath $script:OPENCODE_BIN) {
                # Zip may contain a subdirectory -- locate opencode.exe
                $_ocExeFound = Get-ChildItem -Path $script:OPENCODE_BIN -Recurse -Filter "opencode.exe" |
                    Select-Object -First 1
                if ($_ocExeFound -and $_ocExeFound.FullName -ne $script:OPENCODE_EXE) {
                    Move-Item -Path $_ocExeFound.FullName -Destination $script:OPENCODE_EXE -Force
                }
                if (Test-Path $script:OPENCODE_EXE) {
                    Write-AISuccess "OpenCode v$($script:OPENCODE_VERSION) installed"
                } else {
                    Write-AIWarn "opencode.exe not found after extraction -- skipping"
                }
            } else {
                Write-AIWarn "OpenCode extraction failed -- skipping"
            }
        }
    }
} else {
    Write-AISuccess "OpenCode already installed ($($script:OPENCODE_EXE))"
}

# ── OpenCode configuration ────────────────────────────────────────────────────
if (Test-Path $script:OPENCODE_EXE) {
    $_ocSync = Sync-WindowsOpenCodeConfigFromEnv `
        -InstallDir $installDir `
        -GpuBackend $gpuInfo.Backend `
        -CloudMode:$cloudMode `
        -DefaultModelId $tierConfig.GgufFile `
        -DefaultModelName $tierConfig.LlmModel `
        -DefaultContextLimit ([int]$tierConfig.MaxContext)

    switch ($_ocSync.Status) {
        "created" {
            Write-AISuccess "OpenCode configured for local llama-server (model: $($_ocSync.ModelName))"
        }
        "updated" {
            Write-AISuccess "OpenCode config updated for local llama-server (model: $($_ocSync.ModelName))"
        }
        default {
            Write-AISuccess "OpenCode config regenerated for local llama-server (model: $($_ocSync.ModelName))"
        }
    }

    # ── VBS launcher (available for manual startup) ──────────────────────────
    # Creates a VBS script users can run to start OpenCode without a console
    # window. NOT added to Windows Startup -- OpenCode is a developer tool,
    # not a core service, so it should be opt-in.
    $_vbsContent = @"
' ODS -- OpenCode Web Server (silent launcher)
' Run this script to start OpenCode without a visible console window.
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = WshShell.ExpandEnvironmentStrings("%USERPROFILE%\.opencode")
WshShell.Run """%USERPROFILE%\.opencode\bin\opencode.exe"" web --port $($script:OPENCODE_PORT) --hostname 127.0.0.1", 0, False
"@
    $_vbsPath = Join-Path $script:OPENCODE_DIR "start-opencode.vbs"
    Write-Utf8NoBom -Path $_vbsPath -Content $_vbsContent
    Write-AISuccess "OpenCode ready -- start manually: $($script:OPENCODE_EXE) web --port $($script:OPENCODE_PORT)"
    Write-AI "  Or run: $($_vbsPath) (silent, no console window)"
}

# ── Node.js / npm tools (Claude Code + Codex CLI) ────────────────────────────
# These are optional developer tools that require Node.js and npm.
# Installation is best-effort: failures are non-fatal and clearly reported.

Write-AI "Checking for Node.js (needed for Claude Code + Codex CLI)..."
$_npmCmd  = Get-Command npm  -ErrorAction SilentlyContinue
$_nodeCmd = Get-Command node -ErrorAction SilentlyContinue

if (-not $_npmCmd -or -not $_nodeCmd) {
    # Attempt to install Node.js LTS silently via winget (Windows 10 1809+ built-in)
    Write-AIWarn "Node.js not found. Attempting to install via winget..."
    $_winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($_winget) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        & winget install OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP

        # Refresh PATH so npm/node are visible in this session without a new shell
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "User")
        $_npmCmd  = Get-Command npm  -ErrorAction SilentlyContinue
        $_nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    }

    if (-not $_npmCmd) {
        Write-AIWarn "Node.js not installed. Claude Code and Codex CLI will be skipped."
        Write-AI "  Install manually: https://nodejs.org/en/download"
        Write-AI "  Then run: npm install -g @anthropic-ai/claude-code @openai/codex"
    }
}

if ($_npmCmd) {
    $_npmVer = & npm --version 2>$null
    Write-AISuccess "Node.js / npm $_npmVer available"

    # Install Claude Code (Anthropic's terminal agent)
    $_claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $_claudeCmd) {
        Write-AI "Installing Claude Code (@anthropic-ai/claude-code)..."
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        & npm install -g "@anthropic-ai/claude-code" 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        if (Get-Command claude -ErrorAction SilentlyContinue) {
            Write-AISuccess "Claude Code installed (run: claude)"
        } else {
            Write-AIWarn "Claude Code install failed -- install later: npm install -g @anthropic-ai/claude-code"
        }
    } else {
        Write-AISuccess "Claude Code already installed"
    }

    # Install Codex CLI (OpenAI's terminal agent)
    $_codexCmd = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $_codexCmd) {
        Write-AI "Installing Codex CLI (@openai/codex)..."
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        & npm install -g "@openai/codex" 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        if (Get-Command codex -ErrorAction SilentlyContinue) {
            Write-AISuccess "Codex CLI installed (run: codex)"
        } else {
            Write-AIWarn "Codex CLI install failed -- install later: npm install -g @openai/codex"
        }
    } else {
        Write-AISuccess "Codex CLI already installed"
    }
}

function Test-ODSHostAgentPythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$PrefixArgs = @()
    )

    if ([string]::IsNullOrWhiteSpace($FilePath) -or -not (Test-Path $FilePath)) {
        return $false
    }

    # Windows can expose python.exe/python3.exe aliases that only print the
    # Microsoft Store install hint. They are CommandInfo objects, but not a
    # runnable Python interpreter for ods-host-agent.
    if ($FilePath -match '\\WindowsApps\\python3?\.exe$') {
        return $false
    }

    try {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        $version = & $FilePath @PrefixArgs --version 2>&1
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        return ($exitCode -eq 0 -and (($version | Out-String) -match 'Python 3\.'))
    } catch {
        $ErrorActionPreference = $prevEAP
        return $false
    }
}

function New-ODSHostAgentPythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$PrefixArgs = @()
    )

    [pscustomobject]@{
        FilePath   = $FilePath
        PrefixArgs = @($PrefixArgs)
    }
}

function Resolve-ODSHostAgentPython {
    $seen = @{}
    $candidateFiles = New-Object System.Collections.Generic.List[string]

    foreach ($root in @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)}
    )) {
        if ([string]::IsNullOrWhiteSpace($root) -or -not (Test-Path $root)) { continue }
        Get-ChildItem -Path $root -Directory -Filter "Python*" -ErrorAction SilentlyContinue |
            ForEach-Object {
                $exe = Join-Path $_.FullName "python.exe"
                if (Test-Path $exe) { $candidateFiles.Add($exe) }
            }
    }

    foreach ($name in @("python3", "python")) {
        $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) {
            $candidateFiles.Add($cmd.Source)
        }
    }

    foreach ($file in $candidateFiles) {
        $key = $file.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if (Test-ODSHostAgentPythonCandidate -FilePath $file) {
            return (New-ODSHostAgentPythonCandidate -FilePath $file)
        }
    }

    $pyLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
    if ($pyLauncher -and $pyLauncher.Source -and
        (Test-ODSHostAgentPythonCandidate -FilePath $pyLauncher.Source -PrefixArgs @("-3"))) {
        return (New-ODSHostAgentPythonCandidate -FilePath $pyLauncher.Source -PrefixArgs @("-3"))
    }

    return $null
}

function Install-ODSHostAgentPython {
    $winget = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-AIWarn "Python 3 not found and winget is unavailable -- ODS host agent cannot start."
        return $null
    }

    Write-AIWarn "Python 3 not found. Installing Python 3.12 via winget for ODS host agent..."
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & winget install --exact --id Python.Python.3.12 --silent --disable-interactivity --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
    $ErrorActionPreference = $prevEAP

    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    return (Resolve-ODSHostAgentPython)
}

# ── ODS Host Agent (extension lifecycle management) ────────────────────────
$_agentScript = Join-Path (Join-Path $installDir "bin") "ods-host-agent.py"
if (Test-Path $_agentScript) {
    $_python3 = Resolve-ODSHostAgentPython
    if (-not $_python3) { $_python3 = Install-ODSHostAgentPython }

    if ($_python3) {
        # Kill existing agent on reinstall (matches Linux force-restart pattern)
        if (Test-Path $script:ODS_AGENT_PID_FILE) {
            $_oldPid = $null
            try {
                $_oldPid = [int](Get-Content $script:ODS_AGENT_PID_FILE -Raw).Trim()
                Stop-Process -Id $_oldPid -Force -ErrorAction SilentlyContinue
            } catch { }
            Remove-Item $script:ODS_AGENT_PID_FILE -Force -ErrorAction SilentlyContinue
        }

        # Ensure data directory exists for PID and log files
        $pidDir = Split-Path $script:ODS_AGENT_PID_FILE
        New-Item -ItemType Directory -Path $pidDir -Force -ErrorAction SilentlyContinue | Out-Null

        # Run the agent through Task Scheduler immediately so SSH-launched
        # installs keep the host agent alive after the session exits. Prepend
        # Docker to PATH so the agent can find docker.exe (Docker Desktop may
        # not be in the system PATH yet after fresh install).
        $_dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
        $_psQuote = {
            param([string]$Value)
            "'" + ($Value -replace "'", "''") + "'"
        }
        $_dockerPathLiteral = & $_psQuote "$_dockerBin;"
        $_pythonLiteral = & $_psQuote $_python3.FilePath
        $_pythonPrefixArgsLiteral = "@(" + (($_python3.PrefixArgs | ForEach-Object { & $_psQuote $_ }) -join ", ") + ")"
        $_agentScriptLiteral = & $_psQuote $_agentScript
        $_pidFileLiteral = & $_psQuote $script:ODS_AGENT_PID_FILE
        $_installDirLiteral = & $_psQuote $installDir
        $_logFileLiteral = & $_psQuote $script:ODS_AGENT_LOG_FILE
        $_agentCommand = @"
`$env:PATH = $_dockerPathLiteral + `$env:PATH
`$agentArgs = $_pythonPrefixArgsLiteral + @($_agentScriptLiteral, '--port', '$($script:ODS_AGENT_PORT)', '--pid-file', $_pidFileLiteral, '--install-dir', $_installDirLiteral)
Set-Location $_installDirLiteral
Start-Process -FilePath $_pythonLiteral -ArgumentList `$agentArgs -WorkingDirectory $_installDirLiteral -WindowStyle Hidden -RedirectStandardError $_logFileLiteral -Wait
"@
        $_encodedAgentCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($_agentCommand))

        # Register Windows Scheduled Task for login persistence
        try { Stop-ScheduledTask -TaskName $script:ODS_AGENT_TASK_NAME -ErrorAction SilentlyContinue } catch { }

        $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" `
            -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand $_encodedAgentCommand" `
            -WorkingDirectory $installDir
        $taskTrigger  = New-ScheduledTaskTrigger -AtLogOn
        $taskSettings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
        $taskPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

        $taskError = $null
        try {
            Register-ScheduledTask -TaskName $script:ODS_AGENT_TASK_NAME `
                -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings -Principal $taskPrincipal `
                -Description "ODS Host Agent -- manages extensions and bridges dashboard to host" `
                -Force -ErrorAction Stop | Out-Null
            Start-ScheduledTask -TaskName $script:ODS_AGENT_TASK_NAME
            # Cleanup any legacy VBScript startup launcher if scheduled task succeeded
            $startupFolder = [Environment]::GetFolderPath("Startup")
            $vbsFile = Join-Path $startupFolder "ods-host-agent.vbs"
            if (Test-Path $vbsFile) {
                Remove-Item $vbsFile -Force -ErrorAction SilentlyContinue
            }
            Write-AISuccess "Host agent scheduled and started (Task: $($script:ODS_AGENT_TASK_NAME))"

            Start-Sleep -Seconds 3
            try {
                $resp = Invoke-WebRequest -Uri $script:ODS_AGENT_HEALTH_URL `
                    -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue
                if ($resp.StatusCode -eq 200) {
                    Write-AISuccess "ODS host agent started (port $($script:ODS_AGENT_PORT))"
                } else {
                    Write-AIWarn "ODS host agent started but health check returned $($resp.StatusCode)"
                }
            } catch {
                Write-AIWarn "ODS host agent started but not yet responding -- check: .\ods.ps1 agent status"
            }
        } catch {
            $taskError = $_
            Write-AIWarn "Could not register login task through Task Scheduler: $($taskError.Exception.Message)"
            Write-AI "Setting up alternative startup persistence for standard user..."

            $startupFolder = [Environment]::GetFolderPath("Startup")
            $vbsFile = Join-Path $startupFolder "ods-host-agent.vbs"
            $vbsContent = @"
' ODS Host Agent login startup launcher
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand $_encodedAgentCommand", 0, False
"@
            try {
                Write-Utf8NoBom -Path $vbsFile -Content $vbsContent
                Write-AISuccess "Startup persistence configured via Start Menu Startup folder: $vbsFile"
                # Start the agent now using the startup script
                Start-Process wscript.exe -ArgumentList ('"{0}"' -f $vbsFile) -NoNewWindow
            } catch {
                Write-AIError "Failed to set up alternative startup persistence: $_"
                Write-AIWarn "Starting host agent directly for this session..."
                Start-Process -FilePath $_python3.FilePath -ArgumentList @($_agentScript, '--port', "$($script:ODS_AGENT_PORT)", '--pid-file', $script:ODS_AGENT_PID_FILE, '--install-dir', $installDir) -WorkingDirectory $installDir -WindowStyle Hidden -RedirectStandardError $script:ODS_AGENT_LOG_FILE
            }
        }
    } else {
        Write-AIWarn "Python 3 unavailable -- ODS host agent not started"
        Write-AI "  Install Python 3.12 and re-run the installer, or start manually: .\ods.ps1 agent start"
    }
} else {
    Write-AI "ODS host agent script not found -- skipping"
}

Write-AISuccess "Developer tools setup complete"
