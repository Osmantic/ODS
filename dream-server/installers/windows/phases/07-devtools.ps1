# ============================================================================
# Dream Server Windows Installer -- Phase 07: Developer Tools
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
    Write-AI "[DRY RUN] Would start Dream Host Agent on port $($script:DREAM_AGENT_PORT)"
    Write-AI "[DRY RUN] Would register $($script:DREAM_AGENT_TASK_NAME) scheduled task for login persistence"
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
' Dream Server -- OpenCode Web Server (silent launcher)
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

# ── Dream Host Agent (extension lifecycle management) ────────────────────────
$_agentScript = Join-Path (Join-Path $installDir "bin") "dream-host-agent.py"
if (Test-Path $_agentScript) {
    $_python = Resolve-DreamWindowsPython

    if ($_python) {
        # Kill existing agent on reinstall (matches Linux force-restart pattern)
        if (Test-Path $script:DREAM_AGENT_PID_FILE) {
            $_oldPid = $null
            try {
                $_oldPid = [int](Get-Content $script:DREAM_AGENT_PID_FILE -Raw).Trim()
                Stop-Process -Id $_oldPid -Force -ErrorAction SilentlyContinue
            } catch { }
            Remove-Item $script:DREAM_AGENT_PID_FILE -Force -ErrorAction SilentlyContinue
        }

        # Ensure data directory exists for PID and log files
        $pidDir = Split-Path $script:DREAM_AGENT_PID_FILE
        New-Item -ItemType Directory -Path $pidDir -Force -ErrorAction SilentlyContinue | Out-Null

        # Start agent through a hidden PowerShell host so login persistence does
        # not leave a visible cmd.exe window on the desktop. Prepend Docker to
        # PATH so the agent can find docker.exe (Docker Desktop may not be in
        # the system PATH yet after fresh install).
        $_dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
        $_psQuote = {
            param([string]$Value)
            "'" + ($Value -replace "'", "''") + "'"
        }
        $_dockerPathLiteral = & $_psQuote "$_dockerBin;"
        $_pythonLiteral = & $_psQuote $_python.Source
        $_pythonArgsLiteral = ConvertTo-DreamPowerShellArrayExpression @($_python.PythonArgs)
        $_agentScriptLiteral = & $_psQuote $_agentScript
        $_pidFileLiteral = & $_psQuote $script:DREAM_AGENT_PID_FILE
        $_installDirLiteral = & $_psQuote $installDir
        $_logFileLiteral = & $_psQuote $script:DREAM_AGENT_LOG_FILE
        $_agentCommand = @"
`$env:PATH = $_dockerPathLiteral + `$env:PATH
`$agentArgs = $_pythonArgsLiteral + @($_agentScriptLiteral, '--port', '$($script:DREAM_AGENT_PORT)', '--pid-file', $_pidFileLiteral, '--install-dir', $_installDirLiteral)
Start-Process -FilePath $_pythonLiteral -ArgumentList `$agentArgs -WorkingDirectory $_installDirLiteral -WindowStyle Hidden -RedirectStandardError $_logFileLiteral
"@
        $_encodedAgentCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($_agentCommand))
        Start-Process -FilePath "powershell.exe" `
            -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-EncodedCommand", $_encodedAgentCommand `
            -WindowStyle Hidden -WorkingDirectory $installDir

        # Brief health check
        Start-Sleep -Seconds 3
        try {
            $resp = Invoke-WebRequest -Uri $script:DREAM_AGENT_HEALTH_URL `
                -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue
            if ($resp.StatusCode -eq 200) {
                Write-AISuccess "Dream host agent started (port $($script:DREAM_AGENT_PORT))"
            } else {
                Write-AIWarn "Dream host agent started but health check returned $($resp.StatusCode)"
            }
        } catch {
            Write-AIWarn "Dream host agent started but not yet responding -- check: .\dream.ps1 agent status"
        }

        # Register Windows Scheduled Task for login persistence
        Unregister-ScheduledTask -TaskName $script:DREAM_AGENT_TASK_NAME `
            -Confirm:$false -ErrorAction SilentlyContinue

        $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" `
            -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand $_encodedAgentCommand" `
            -WorkingDirectory $installDir
        $taskTrigger  = New-ScheduledTaskTrigger -AtLogOn
        $taskSettings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

        $taskError = $null
        try {
            Register-ScheduledTask -TaskName $script:DREAM_AGENT_TASK_NAME `
                -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings `
                -Description "DreamServer Host Agent -- manages extensions and bridges dashboard to host" `
                -ErrorAction Stop | Out-Null
            Write-AISuccess "Host agent registered to start at login (Task: $($script:DREAM_AGENT_TASK_NAME))"
        } catch {
            $taskError = $_
            Write-AIWarn "Could not register login task -- start manually: .\dream.ps1 agent start"
            if ($taskError -and $taskError.Exception) {
                Write-AI "  Scheduled Tasks error: $($taskError.Exception.Message)"
            }
        }
    } else {
        Write-AIWarn "Python 3.8+ not found -- Dream host agent not started"
        Write-AI "  Install Python 3 and re-run the installer, or start manually: .\dream.ps1 agent start"
    }
} else {
    Write-AI "Dream host agent script not found -- skipping"
}

Write-AISuccess "Developer tools setup complete"
