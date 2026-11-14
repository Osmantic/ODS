# ============================================================================
# ODS Windows Desktop -- setup (preview, opt-in)
# ============================================================================
# Runs the real ODS dashboard and a local AI model natively on Windows: no
# Docker, no WSL, no administrator rights. Everything is per user under
# %LOCALAPPDATA%\ODS and listens on 127.0.0.1 only.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File ods\installers\windows-desktop.ps1
#
# A rerun repairs and updates in place and keeps the owner's model, port and
# chats. -Repair also re-verifies every file hash. Reference: RFC #7968.
# ============================================================================

[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$Repair,
    [int]$Port = 0,
    [string]$DashboardDist = '',
    [switch]$NoShortcut
)

$ErrorActionPreference = 'Stop'
$SourceRoot = Split-Path -Parent $PSScriptRoot
$Lib = Join-Path $PSScriptRoot 'windows\lib'
. (Join-Path $Lib 'wsl-portal-amd.ps1')
. (Join-Path $Lib 'app-shortcut.ps1')
foreach ($name in @('desktop-model.ps1', 'desktop-settings.ps1', 'desktop-python.ps1', 'desktop-runtime.ps1', 'desktop-api-launcher.ps1',
    'desktop-uninstall.ps1')) {
    . (Join-Path $Lib $name)
}

# Files the sign-in task and uninstall.ps1 run, copied beside launch-api.ps1
# so both keep working after the ODS checkout is moved or deleted.
$script:ODSDesktopLauncherLibraries = @('private-file.ps1', 'native-llama-runtime.ps1', 'desktop-python.ps1',
    'desktop-runtime.ps1', 'desktop-api-launcher.ps1', 'app-shortcut.ps1', 'desktop-uninstall.ps1')
$script:ODSDesktopApiReadySeconds = 150
$script:ODSDesktopFreeSpaceHeadroomGB = 1.5

$script:ODSDesktopLaunchScript = @'
$ErrorActionPreference = 'Stop'
try {
    foreach ($name in @('native-llama-runtime.ps1', 'desktop-python.ps1', 'desktop-runtime.ps1', 'desktop-api-launcher.ps1')) {
        . (Join-Path $PSScriptRoot $name)
    }
    $paths = Get-ODSDesktopPaths (Split-Path -Parent $PSScriptRoot)
    $results = @(Invoke-ODSDesktopApi $paths)
    exit ([int]$results[-1])
} catch {
    # The failure record setup waits for comes first; the log folder may not
    # exist yet when startup fails early.
    $message = 'ODS app startup failed: ' + $_.Exception.Message
    $root = Split-Path -Parent $PSScriptRoot
    Write-ODSPrivateEnvFile -Path (Join-Path $root 'api-ready.json') -Content (@{ Error = $message } | ConvertTo-Json -Compress)
    $logs = Join-Path $root 'logs'
    $null = New-Item -ItemType Directory -Force -Path $logs
    Add-Content -LiteralPath (Join-Path $logs 'launch-api.log') -Encoding UTF8 -Value ('{0:o} {1}' -f (Get-Date), $message)
    exit 1
}
'@

$script:ODSDesktopUninstallScript = @'
# ODS Desktop uninstaller (Settings > Apps runs this). -KeepData or
# -RemoveData answers the data question; -NonInteractive keeps the data.
[CmdletBinding()]
param([switch]$KeepData, [switch]$RemoveData, [switch]$NonInteractive)
$ErrorActionPreference = 'Stop'
foreach ($name in @('native-llama-runtime.ps1', 'desktop-runtime.ps1', 'desktop-api-launcher.ps1', 'app-shortcut.ps1', 'desktop-uninstall.ps1')) {
    . (Join-Path (Join-Path $PSScriptRoot 'launcher') $name)
}
Write-Host ''
Write-Host '  Removing ODS Desktop'
$code = 0
try {
    $result = Invoke-ODSDesktopUninstall -Paths (Get-ODSDesktopPaths $PSScriptRoot) -KeepData:$KeepData -RemoveData:$RemoveData -NonInteractive:$NonInteractive
    Write-Host '         Done: the ODS sign-in tasks, shortcuts and the Apps & Features entry are gone.'
    foreach ($entry in $result.Removed) { Write-Host "         Deleted $($entry.Path) ($(Format-ODSDesktopBytes $entry.Bytes))" }
    foreach ($entry in $result.Kept) { Write-Host "         Kept $($entry.Path) ($(Format-ODSDesktopBytes $entry.Bytes)); delete it yourself any time." }
    Write-Host '         Chats you saved in the ODS window are kept by Microsoft Edge as site data for localhost, not in these folders.'
    Write-Host '         To erase them: Edge Settings > Cookies and site permissions > Manage and delete cookies and site data > localhost.'
} catch {
    Write-Host "         Uninstall stopped: $($_.Exception.Message)"
    $code = 1
}
if (-not $NonInteractive) { $null = Read-Host 'Press Enter to close' }
exit $code
'@

function Write-ODSDesktopStage([int]$Step, [string]$Title, [string]$Detail) {
    Write-Host ''
    $style = @{}
    if (-not $env:NO_COLOR) { $style.ForegroundColor = 'Cyan' }
    Write-Host ("  [{0}/6]  {1}" -f $Step, $Title) @style
    Write-Host "         $Detail"
}

function Test-ODSDesktopElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-ODSDesktopHost([string]$Dist) {
    if ($env:OS -ne 'Windows_NT') { throw 'ODS Desktop setup runs on Windows.' }
    # The Start menu also offers "Windows PowerShell (x86)". In it, typing
    # powershell starts the 32-bit one again, so say which window to open.
    if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
        throw 'This window is the 32-bit "Windows PowerShell (x86)". Open "Windows PowerShell" (the one without x86) from the Start menu and run setup there.'
    }
    if (Test-ODSDesktopElevated) {
        throw ('This window is running as Administrator. ODS Desktop installs for your own Windows account only: open ' +
            'PowerShell without "Run as administrator" and run setup again. (Windows asks separately if a Microsoft ' +
            'component is needed.)')
    }
    if ((Get-HostArchitecture) -ne 'amd64') { throw 'ODS Desktop needs 64-bit Windows on an Intel or AMD processor (x64) for now.' }
    if (-not (Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'Windows is missing curl.exe, which comes with Windows 10 version 1803 and later. Update Windows, then run setup again.'
    }
    # Fail before any download when the dashboard was never built.
    if (-not (Test-Path -LiteralPath (Join-Path $Dist 'index.html') -PathType Leaf)) {
        throw "The built dashboard was not found at $Dist. Build it (npm ci, then npm run build in ods\extensions\services\dashboard) or pass -DashboardDist, then run setup again."
    }
}

function Assert-ODSDesktopDiskSpace($Selection, $Recommendation) {
    # The model still to download, plus Python, llama.cpp, the app and room
    # for Windows itself to keep working.
    $modelBytes = Get-ODSDesktopModelDownloadBytes $Selection $Recommendation.Config (Get-ODSPortalModelsDir)
    $needBytes = $modelBytes + [long]($script:ODSDesktopFreeSpaceHeadroomGB * 1GB)
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($env:LOCALAPPDATA))
    if ($drive.AvailableFreeSpace -lt $needBytes) {
        throw ("ODS Desktop needs about {0:N1} GB free on {1} (the AI model still needs {2:N1} GB), and it has {3:N1} GB. Free some space, then run setup again." -f
            ($needBytes / 1GB), $drive.Name, ($modelBytes / 1GB), ($drive.AvailableFreeSpace / 1GB))
    }
}

function Install-ODSDesktopModelEngine($Existing, [string]$OwnerPath) {
    Assert-ODSDesktopRuntimeTakeover $Existing $OwnerPath
    $pin = Get-ODSNativeLlamaPin -SourceRoot $SourceRoot
    $runtime = $null
    $installDir = Get-ODSNativeLlamaInstallDirectory $pin
    if (Test-Path -LiteralPath $installDir) {
        try { $runtime = Test-ODSNativeLlamaInstall -Directory $installDir -ExpectedZipSha256 $pin.Sha256 -ExpectedReleaseTag $pin.ReleaseTag } catch { $runtime = $null }
    }
    if (-not $runtime) {
        Write-Host "         Downloading llama.cpp $($pin.ReleaseTag) from github.com/ggml-org (about $([math]::Ceiling($pin.Size / 1MB)) MB, SHA-256 pinned)."
        $runtime = Install-ODSNativeLlamaRuntime -Pin $pin
    }
    $qualification = Test-ODSNativeLlamaQualification -ExecutablePath $runtime.ExecutablePath -ExpectedBuild $pin.Build
    if ($qualification.PolicyHint) { Write-Host "         $($qualification.PolicyHint)" }
    Write-Host "         llama.cpp $($pin.ReleaseTag) runs on this PC (CPU)."
    return [pscustomobject]@{ Runtime = $runtime; Qualification = $qualification }
}

function Get-ODSDesktopRecommendation([long]$ContextSetting) {
    # main's catalog selector for a PC without a GPU route, so Desktop gets
    # the model Linux and Windows give the same CPU and memory.
    $gpu = @{ Backend = 'none'; VramMB = 0; Name = 'CPU'; MemoryType = 'none' }
    $ramGB = Get-SystemRamGB
    $tier = [string](ConvertTo-TierFromGpu -GpuInfo $gpu -SystemRamGB $ramGB)
    if ($tier -eq '0') { throw "This PC has $ramGB GB of memory; ODS Desktop needs at least 8 GB to run a local model." }
    if (-not $env:MODEL_PROFILE) { $env:MODEL_PROFILE = 'qwen' }
    $config = Resolve-CatalogModelRecommendation -TierConfig (Resolve-TierConfig -Tier $tier) -Tier $tier -GpuInfo $gpu `
        -SystemRamGB $ramGB -SourceRoot $SourceRoot
    $context = Resolve-ODSDesktopContextSize ([int]$config.MaxContext)
    if ($ContextSetting -gt 0) { $context = Resolve-ODSDesktopContextSize ([int]$ContextSetting) ([int]$ContextSetting) }
    return [pscustomobject]@{ Config = $config; Plan = [pscustomobject]@{ GgufFile = [string]$config.GgufFile; ContextSize = $context } }
}

function Start-ODSDesktopModel($Existing, $Engine, $Selection, $Paths) {
    if ($Existing.Kind -eq 'llama-server') { $port = [int]$Existing.Selection.Port; $keep = $true }
    else { $port = Select-ODSPortalRuntimePort; $keep = $false }
    $options = New-ODSDesktopLlamaOptions $Engine.Runtime $Engine.Qualification
    $plan = [ordered]@{ ExecutablePath = $Engine.Runtime.ExecutablePath; Port = $port; ModelsDir = (Get-ODSPortalModelsDir)
        ContextSize = $Selection.ContextSize; GgufFile = $Selection.GgufFile }
    $null = New-Item -ItemType Directory -Force -Path $Paths.Root
    $owner = @{ Task = (Get-ODSPortalRuntimeTaskName); RuntimeDir = (Get-ODSPortalRuntimeDir); Profile = 'windows-desktop' }
    Write-ODSPrivateEnvFile -Path $Paths.RuntimeOwner -Content ($owner | ConvertTo-Json -Compress)
    Write-Host "         Loading $($Selection.GgufFile) on 127.0.0.1:$port ($($Selection.ContextSize) tokens of context)..."
    $null = Invoke-ODSPortalRuntimeCutover -Existing $Existing -Plan $plan -Options $options -ApiKey (Get-ODSPortalApiKey) `
        -KeepPort $keep -TaskDescription $script:ODSDesktopRuntimeTaskDescription
    $previous = if ($Existing.Kind -eq 'llama-server') { Split-Path -Parent $Existing.Launch.ExecutablePath } else { '' }
    Remove-ODSNativeLlamaOldRuntimes -KeepDirectory $Engine.Runtime.Directory -PreviousDirectory $previous
    return $port
}

function Get-ODSDesktopApiTask {
    $task = @(Get-ScheduledTask -TaskName (Get-ODSDesktopApiTaskName) -TaskPath '\' -ErrorAction SilentlyContinue)
    if ($task.Count) { return $task[0] }
    return $null
}

function Stop-ODSDesktopApp($Paths) {
    # Intent first, so a queued sign-in start exits at once; then the task,
    # then only the processes the ownership record proves.
    if (Test-Path -LiteralPath $Paths.Root) { Write-ODSPrivateEnvFile -Path $Paths.Intent -Content '{"State":"stopped"}' }
    $task = Get-ODSDesktopApiTask
    if ($task -and [string]$task.State -eq 'Running') { Stop-ScheduledTask -TaskName $task.TaskName -TaskPath '\' }
    $null = Stop-ODSDesktopApi $Paths.Ownership
}

function Publish-ODSDesktopLauncher($Paths) {
    $null = New-Item -ItemType Directory -Force -Path $Paths.Launcher
    foreach ($name in $script:ODSDesktopLauncherLibraries) {
        Write-ODSPrivateFileBytes -Path (Join-Path $Paths.Launcher $name) -Bytes ([IO.File]::ReadAllBytes((Join-Path $Lib $name)))
    }
    Write-ODSPrivateEnvFile -Path $Paths.LauncherScript -Content $script:ODSDesktopLaunchScript
}

function Register-ODSDesktopApiTask($Paths) {
    $shell = Get-Command pwsh.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $shellPath = if ($shell) { $shell.Source } else { 'powershell.exe' }
    $action = New-ScheduledTaskAction -Execute $shellPath -Argument (Get-ODSDesktopApiLauncherArguments $Paths) -WorkingDirectory $Paths.Launcher
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User (Get-ODSPortalUserSid)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName (Get-ODSDesktopApiTaskName) -TaskPath '\' -Action $action -Trigger $trigger -Settings $settings `
        -Principal (New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited) `
        -Description 'ODS Desktop: the ODS app (dashboard-api on 127.0.0.1). Starts at sign-in.' -Force | Out-Null
}

function Wait-ODSDesktopApiReady($Paths, [int]$Port) {
    $deadline = (Get-Date).AddSeconds($script:ODSDesktopApiReadySeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $Paths.Ready -PathType Leaf) {
            $ready = Read-ODSNativeLlamaJson $Paths.Ready
            if ($ready.Error) { throw ([string]$ready.Error) }
            $listener = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
            if ([int]$ready.Port -ne $Port -or $listener.Count -ne 1 -or $listener[0].LocalAddress -ne '127.0.0.1' -or
                [int]$listener[0].OwningProcess -ne [int]$ready.ProcessId) {
                throw "The ODS app on port $Port is not the one setup started."
            }
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "The ODS app did not start within $($script:ODSDesktopApiReadySeconds) seconds. See $(Join-Path $Paths.Logs 'dashboard-api.err.log')."
}

function Install-ODSDesktopApp($Paths, [string]$Dist, [int]$LlamaPort, $Settings, [bool]$Repair) {
    Stop-ODSDesktopApp $Paths
    $lock = Join-Path $SourceRoot 'extensions\services\dashboard-api\requirements.lock'
    $problem = Test-ODSDesktopPythonInstall -Directory $Paths.Python -Requirements $lock -Full:$Repair
    if ($problem) {
        Write-Host "         $problem Installing Python and the ODS app packages (hash-checked)..."
        $null = Install-ODSDesktopPython -SourceRoot $SourceRoot -Destination $Paths.Python -Requirements $lock
    }
    Publish-ODSDesktopApp $SourceRoot $Paths
    Publish-ODSDesktopDist $Dist $Paths
    $null = New-Item -ItemType Directory -Force -Path $Paths.Data
    $previous = if (Test-Path -LiteralPath $Paths.Environment -PathType Leaf) {
        ConvertFrom-ODSDesktopEnvText ([IO.File]::ReadAllText($Paths.Environment, [Text.UTF8Encoding]::new($false, $true)))
    } else { @{} }
    $key = if ($previous['DASHBOARD_API_KEY']) { [string]$previous['DASHBOARD_API_KEY'] } else { New-ODSNativeLlamaApiKey }
    $current = if ($previous['DASHBOARD_API_PORT']) { [int]$previous['DASHBOARD_API_PORT'] } else { 0 }
    $apiPort = Select-ODSDesktopApiPort ([int]$Settings.Port) $current
    $environment = New-ODSDesktopApiEnvironment $Paths $apiPort $LlamaPort (Get-ODSPortalRuntimeDir) $key
    Write-ODSPrivateEnvFile -Path $Paths.Environment -Content (ConvertTo-ODSDesktopEnvText $environment)
    Publish-ODSDesktopLauncher $Paths
    Write-ODSPrivateEnvFile -Path $Paths.Intent -Content '{"State":"running"}'
    if (Test-Path -LiteralPath $Paths.Ready) { Remove-Item -LiteralPath $Paths.Ready -Force }
    Register-ODSDesktopApiTask $Paths
    Start-ScheduledTask -TaskName (Get-ODSDesktopApiTaskName) -TaskPath '\'
    Wait-ODSDesktopApiReady $Paths $apiPort
    return $apiPort
}

function Publish-ODSDesktopUninstaller($Paths, [string[]]$Shortcuts, [string]$ModelPath, [string]$EngineDir) {
    # Settings > Apps lists ODS Desktop with its real size (the model is most
    # of it); uninstall deletes only shortcuts that still match this record.
    $records = @(Get-ODSPortalShortcutRecord -Paths $Shortcuts)
    Write-ODSPrivateEnvFile -Path (Join-Path $Paths.Root 'shortcuts.json') -Content (ConvertTo-Json -InputObject $records -Depth 3 -Compress)
    Write-ODSPrivateEnvFile -Path (Join-Path $Paths.Root 'uninstall.ps1') -Content $script:ODSDesktopUninstallScript
    $manifest = [IO.File]::ReadAllText((Join-Path $SourceRoot 'manifest.json'), [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json
    $bytes = (Get-ODSDesktopFolderBytes $Paths.Root) + (Get-ODSDesktopFolderBytes $ModelPath) + (Get-ODSDesktopFolderBytes $EngineDir)
    $icon = if (Test-Path -LiteralPath $Paths.Icon -PathType Leaf) { $Paths.Icon } else { '' }
    $null = Register-ODSDesktopUninstallEntry -Paths $Paths -Version ([string]$manifest.release.version) -Bytes $bytes -IconPath $icon
}

function Install-ODSDesktop {
    param([bool]$NonInteractive, [bool]$Repair, [int]$Port, [string]$DashboardDist, [bool]$NoShortcut)
    $paths = Get-ODSDesktopPaths
    $dist = if ($DashboardDist) { $DashboardDist } else { Join-Path $SourceRoot 'extensions\services\dashboard\dist' }
    Write-Host ''
    Write-Host '  O D S  /  DESKTOP (preview)'
    Write-Host '  Your local AI, natively on Windows. No Docker, no WSL, nothing leaves this PC.'

    Write-ODSDesktopStage 1 'CHECKS' 'Windows, your account, the model for this PC, disk space.'
    Assert-ODSDesktopHost $dist
    $defaults = [ordered]@{ Port = [long]0; ContextSize = [long]0 }
    $explicit = @{}
    if ($Port) { $explicit.Port = [long]$Port }
    $settings = Resolve-ODSDesktopSettings -Explicit $explicit -Previous (Read-ODSDesktopSettings $paths.Settings) -Defaults $defaults
    $recommendation = Get-ODSDesktopRecommendation ([long]$settings.ContextSize)
    # Read-only here; stage 3 refuses a runtime Desktop may not take over.
    $existing = Get-ODSPortalExistingRuntime
    $selection = Resolve-ODSPortalRuntimeSelection $existing $recommendation.Plan
    Assert-ODSDesktopDiskSpace $selection $recommendation

    Write-ODSDesktopStage 2 'MICROSOFT VISUAL C++ RUNTIME' 'The model engine is built with it.'
    $missing = Test-ODSDesktopVcRuntime
    if ($missing) {
        if (-not (Install-ODSDesktopVcRuntime -Reason $missing -NonInteractive $NonInteractive)) {
            Write-Host "         Setup stopped; nothing else was changed. Run it again when ready: $(Format-ODSDesktopCommand $PSCommandPath)"
            return 1
        }
    }
    Write-Host '         Present.'

    Write-ODSDesktopStage 3 'MODEL ENGINE' 'llama.cpp, the engine that runs the AI model on this PC.'
    $engine = Install-ODSDesktopModelEngine $existing $paths.RuntimeOwner

    Write-ODSDesktopStage 4 'AI MODEL' 'Chosen for this PC from the ODS model catalog.'
    if (-not $selection.Carried) {
        Write-Host "         $($selection.GgufFile): a large download. If it is interrupted, run setup again and it continues where it stopped."
        $null = Get-ODSDesktopModel -Pin (Get-ODSDesktopModelPin $recommendation.Config) -ModelsDir (Get-ODSPortalModelsDir) -Repair:$Repair
    }

    Write-ODSDesktopStage 5 'START THE MODEL' 'It starts by itself whenever you sign in to Windows.'
    $llamaPort = Start-ODSDesktopModel $existing $engine $selection $paths

    Write-ODSDesktopStage 6 'ODS APP' 'The ODS dashboard and chat, served on this PC only.'
    $apiPort = Install-ODSDesktopApp $paths $dist $llamaPort $settings $Repair
    Write-ODSDesktopSettings $paths.Settings ([ordered]@{ Port = [long]$apiPort; ContextSize = [long]$settings.ContextSize })
    $shortcuts = @()
    if (-not $NoShortcut) {
        $icon = Join-Path $SourceRoot 'extensions\services\dashboard\public\osmantic-os.ico'
        try { $shortcuts = @(Publish-ODSDesktopShortcut -Paths $paths -Port $apiPort -IconSource $icon) }
        catch { Write-Warning "The ODS shortcut could not be created: $($_.Exception.Message)" }
    }
    Publish-ODSDesktopUninstaller $paths $shortcuts (Join-Path (Get-ODSPortalModelsDir) $selection.GgufFile) $engine.Runtime.Directory

    Write-Host ''
    Write-Host '  ODS is ready.'
    if ($shortcuts.Count) { Write-Host '         Open it with the ODS icon on your desktop or in the Start menu.' }
    Write-Host "         Address: http://localhost:$apiPort/"
    Write-Host "         To repair or update later: $(Format-ODSDesktopCommand $PSCommandPath)"
    Write-Host '         To remove it: Settings > Apps > Installed apps > ODS Desktop (preview).'
    if (-not $NoShortcut -and -not $NonInteractive -and $shortcuts.Count) { Start-Process -FilePath $shortcuts[0] }
    return 0
}

try {
    $code = @(Install-ODSDesktop -NonInteractive $NonInteractive.IsPresent -Repair $Repair.IsPresent -Port $Port `
        -DashboardDist $DashboardDist -NoShortcut $NoShortcut.IsPresent)[-1]
} catch {
    # The owner sees one plain sentence and the way back; the full error,
    # with its stack, goes to a log for whoever helps them. It lives in
    # %TEMP% so a refused run (elevated, no space) creates no ODS folder.
    $log = Join-Path $env:TEMP 'ods-desktop-setup.log'
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0:o} {1}{2}' -f (Get-Date), ($_ | Out-String), $_.ScriptStackTrace)
    Write-Host ''
    Write-Host "  Setup stopped: $($_.Exception.Message)"
    Write-Host "         Run it again when ready: $(Format-ODSDesktopCommand $PSCommandPath)"
    Write-Host "         Details for anyone helping you: $log"
    $code = 1
}
exit ([int]$code)
