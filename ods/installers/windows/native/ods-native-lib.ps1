# ============================================================================
# ODS Windows-native app -- pure helpers for ods-native.ps1
# ============================================================================
# Part of: installers/windows/native/
# Purpose: Paths, the supervisor config, the sign-in launch command, the
#          Startup-folder fallback script, process/task ownership tests and
#          the uninstall removal plan. Everything here is a pure function of
#          its arguments: no filesystem, registry, network or process access.
#
# Expects: Nothing. Windows PowerShell 5.1 or PowerShell 7, StrictMode-safe.
# Provides: Get-ODSNativePaths, Get-ODSNativeDashboardEnv,
#           New-ODSNativeSupervisorConfig, ConvertTo-ODSNativeConfigJson,
#           Get-ODSNativeConfigModelPath, Get-ODSNativeSupervisorArguments,
#           Test-ODSNativeSupervisorCommandLine, Test-ODSNativeTaskAction,
#           Get-ODSNativeStartupScriptContent, Test-ODSNativeStartupScriptContent,
#           ConvertTo-ODSNativeSecretText, Get-ODSNativeRemovalPlan,
#           Get-ODSNativeHealthUrl
#
# Modder notes:
#   ods-native.ps1 is the imperative shell that calls these. The installer
#   bundle ships this file as lib\ods-native-lib.ps1 next to the shared
#   lib\private-file.ps1 and lib\backend-contract.ps1. Pester tests live in
#   tests\ods-native-lib.Tests.ps1. Secrets are referenced by path only; no
#   function here ever receives or returns a secret value except
#   ConvertTo-ODSNativeSecretText, which turns random bytes into text.
# ============================================================================

$script:ODSNativeTaskName = 'ODS'
$script:ODSNativeStartupFileName = 'ods-native.vbs'
$script:ODSNativeStartupMarker = "' ODS native supervisor sign-in launcher"
$script:ODSNativeLlamaPort = 8080
$script:ODSNativeDashboardPort = 3002
$script:ODSNativeDefaultCtxSize = 8192

function Join-ODSNativePath {
    # Windows path join that does not touch the filesystem (Join-Path can
    # resolve PSDrives); keeps the helpers pure and testable on any host.
    param([Parameter(Mandatory = $true)][string]$Base, [Parameter(Mandatory = $true)][string[]]$Child)
    $path = $Base.TrimEnd('\')
    foreach ($part in $Child) { $path = $path + '\' + $part.Trim('\') }
    return $path
}

function Assert-ODSNativeAbsolutePath {
    param([string]$Path, [string]$Name)
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -notmatch '^[A-Za-z]:\\') {
        throw "$Name must be an absolute Windows path such as C:\..., got '$Path'."
    }
    if ($Path.Contains('"')) { throw "$Name must not contain a double quote: '$Path'." }
}

function Get-ODSNativePaths {
    <#
    .SYNOPSIS
        Every path the native app uses, from the install root (the folder
        that holds ods-native.ps1) and the user's %LOCALAPPDATA%.
    .DESCRIPTION
        State lives in %LOCALAPPDATA%\ODS\Native, not %LOCALAPPDATA%\ODS
        itself: the WSL/Docker install keeps its own records under
        %LOCALAPPDATA%\ODS, and removing native data must never touch them.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$LocalAppData,
        [string]$StartupFolder = ''
    )
    Assert-ODSNativeAbsolutePath -Path $InstallRoot -Name 'InstallRoot'
    Assert-ODSNativeAbsolutePath -Path $LocalAppData -Name 'LocalAppData'
    $install = $InstallRoot.TrimEnd('\')
    $odsRoot = Join-ODSNativePath -Base $LocalAppData -Child 'ODS'
    $state = Join-ODSNativePath -Base $odsRoot -Child 'Native'
    $run = Join-ODSNativePath -Base $state -Child 'run'
    $startupScript = ''
    if ($StartupFolder) {
        Assert-ODSNativeAbsolutePath -Path $StartupFolder -Name 'StartupFolder'
        $startupScript = Join-ODSNativePath -Base $StartupFolder -Child $script:ODSNativeStartupFileName
    }
    return [ordered]@{
        InstallRoot = $install
        Python = Join-ODSNativePath -Base $install -Child 'python', 'python.exe'
        Pythonw = Join-ODSNativePath -Base $install -Child 'python', 'pythonw.exe'
        LlamaServer = Join-ODSNativePath -Base $install -Child 'llama', 'llama-server.exe'
        DashboardApi = Join-ODSNativePath -Base $install -Child 'dashboard-api'
        OdsRoot = $odsRoot
        StateRoot = $state
        SecretsDir = Join-ODSNativePath -Base $state -Child 'secrets'
        LlamaKey = Join-ODSNativePath -Base $state -Child 'secrets', 'llama-server-api-key'
        DashboardKey = Join-ODSNativePath -Base $state -Child 'secrets', 'dashboard-api-key'
        RunDir = $run
        LogsDir = Join-ODSNativePath -Base $state -Child 'logs'
        DataDir = Join-ODSNativePath -Base $state -Child 'data'
        ModelsDir = Join-ODSNativePath -Base $state -Child 'models'
        ConfigPath = Join-ODSNativePath -Base $state -Child 'supervisor.json'
        StopFile = Join-ODSNativePath -Base $run -Child 'supervisor.stop'
        RecordPath = Join-ODSNativePath -Base $run -Child 'supervisor.json'
        ControlLog = Join-ODSNativePath -Base $state -Child 'logs', 'ods-native.log'
        StartupScript = $startupScript
    }
}

function Get-ODSNativeDashboardEnv {
    # Extra environment for dashboard-api in native mode. The supervisor adds
    # the port, install/data dirs and the API keys itself. The native-mode
    # switches belong to the dashboard-api workstream; extend them here.
    return [ordered]@{
        SERVICE_HOST = '127.0.0.1'
        GPU_BACKEND = 'cpu'
    }
}

function New-ODSNativeSupervisorConfig {
    <#
    .SYNOPSIS
        The supervisor config (schema_version 1) as an ordered dictionary.
        llama-server is enabled only when a model path is given; without a
        model the app runs the dashboard alone until one is configured.
    #>
    param(
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Paths,
        [string]$ModelPath = '',
        [ValidateRange(512, 1048576)][int]$CtxSize = $script:ODSNativeDefaultCtxSize
    )
    # The supervisor validates a service's settings only when it is enabled.
    $llama = [ordered]@{ enabled = $false }
    if ($ModelPath) {
        Assert-ODSNativeAbsolutePath -Path $ModelPath -Name 'ModelPath'
        $llama = [ordered]@{
            enabled = $true
            port = $script:ODSNativeLlamaPort
            model_path = $ModelPath
            ctx_size = $CtxSize
            exe = $Paths.LlamaServer
            # Built-in chat UI on; dashboard-api serves it and adds the key.
            webui = $true
            extra_args = @()
        }
    }
    return [ordered]@{
        schema_version = 1
        install_root = $Paths.InstallRoot
        state_root = $Paths.StateRoot
        host = '127.0.0.1'
        services = [ordered]@{
            'llama-server' = $llama
            'dashboard-api' = [ordered]@{
                enabled = $true
                port = $script:ODSNativeDashboardPort
                python_exe = $Paths.Python
                app_dir = $Paths.DashboardApi
                env = (Get-ODSNativeDashboardEnv)
            }
        }
        restart = [ordered]@{ delay_seconds = 5; max_restarts = 3; window_seconds = 300 }
        logs = [ordered]@{ max_bytes = 10485760; backups = 3 }
    }
}

function ConvertTo-ODSNativeConfigJson {
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Config)
    return (ConvertTo-Json -InputObject $Config -Depth 8)
}

function Get-ODSNativeConfigModelPath {
    <#
    .SYNOPSIS
        The enabled llama-server model path from a parsed config (the output
        of ConvertFrom-Json), or '' when llama-server is disabled or absent.
    #>
    param([AllowNull()][object]$Config)
    if ($null -eq $Config) { return '' }
    $services = $Config.PSObject.Properties['services']
    if (-not $services) { return '' }
    $llama = $services.Value.PSObject.Properties['llama-server']
    if (-not $llama) { return '' }
    $enabled = $llama.Value.PSObject.Properties['enabled']
    $model = $llama.Value.PSObject.Properties['model_path']
    if (-not $enabled -or -not $model -or $enabled.Value -ne $true) { return '' }
    return [string]$model.Value
}

function Get-ODSNativeSupervisorArguments {
    # The pythonw arguments the scheduled task and the Startup script pass.
    # Paths never contain '"' (Assert-ODSNativeAbsolutePath), so plain
    # double quoting is a complete Windows command-line quoting here.
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Paths)
    return ('-m supervisor --config "{0}"' -f $Paths.ConfigPath)
}

function Test-ODSNativeSupervisorCommandLine {
    <#
    .SYNOPSIS
        True only for this installation's supervisor: our pythonw.exe (or
        python.exe) running `-m supervisor --config "<our config>"`. A stale
        or reused PID belonging to anything else never matches.
    #>
    param(
        [AllowEmptyString()][string]$ExecutablePath,
        [AllowEmptyString()][string]$CommandLine,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Paths
    )
    if (-not $ExecutablePath -or -not $CommandLine) { return $false }
    $line = $CommandLine.Trim()
    foreach ($exe in @($Paths.Pythonw, $Paths.Python)) {
        if (-not $ExecutablePath.Equals($exe, [StringComparison]::OrdinalIgnoreCase)) { continue }
        # Task Scheduler and WScript may or may not quote the executable.
        foreach ($prefix in @(('"' + $exe + '"'), $exe)) {
            if ($line.StartsWith($prefix + ' ', [StringComparison]::OrdinalIgnoreCase)) {
                $arguments = $line.Substring($prefix.Length).Trim()
                return $arguments.Equals((Get-ODSNativeSupervisorArguments -Paths $Paths), [StringComparison]::OrdinalIgnoreCase)
            }
        }
    }
    return $false
}

function Test-ODSNativeTaskAction {
    # True when a scheduled task action is the one install-state registers.
    param(
        [AllowEmptyString()][string]$Execute,
        [AllowEmptyString()][string]$Arguments,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Paths
    )
    if (-not $Execute) { return $false }
    return ($Execute.Trim('"').Equals($Paths.Pythonw, [StringComparison]::OrdinalIgnoreCase) -and
        ([string]$Arguments).Trim() -ceq (Get-ODSNativeSupervisorArguments -Paths $Paths))
}

function Get-ODSNativeStartupScriptContent {
    <#
    .SYNOPSIS
        The Startup-folder .vbs used when Task Scheduler refuses the task:
        runs pythonw directly (no console window, no PowerShell).
    #>
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Paths)
    $command = '"{0}" {1}' -f $Paths.Pythonw, (Get-ODSNativeSupervisorArguments -Paths $Paths)
    $vbsLiteral = '"' + $command.Replace('"', '""') + '"'
    $dirLiteral = '"' + $Paths.InstallRoot.Replace('"', '""') + '"'
    return ($script:ODSNativeStartupMarker + "`r`n" +
        "Set WshShell = CreateObject(`"WScript.Shell`")`r`n" +
        "WshShell.CurrentDirectory = $dirLiteral`r`n" +
        "WshShell.Run $vbsLiteral, 0, False`r`n")
}

function Test-ODSNativeStartupScriptContent {
    # Ours (any install root): starts with the marker line. Used before
    # replacing or removing a Startup entry so a foreign file is preserved.
    param([AllowEmptyString()][string]$Content)
    if (-not $Content) { return $false }
    return $Content.StartsWith($script:ODSNativeStartupMarker + "`r`n", [StringComparison]::Ordinal)
}

function ConvertTo-ODSNativeSecretText {
    # Lower-case hex of the given random bytes; at least 32 bytes.
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    if ($Bytes.Length -lt 32) { throw 'A secret needs at least 32 random bytes.' }
    return (($Bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Get-ODSNativeRemovalPlan {
    <#
    .SYNOPSIS
        What uninstall-state deletes. Default: everything ODS generated
        (config, secrets, run records, logs); the user's models and data
        (dashboard data, chats, settings) are kept for a later reinstall.
        -RemoveData: the whole %LOCALAPPDATA%\ODS\Native folder.
    #>
    param(
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Paths,
        [switch]$RemoveData
    )
    if ($RemoveData) { return @($Paths.StateRoot) }
    return @($Paths.ConfigPath, $Paths.SecretsDir, $Paths.RunDir, $Paths.LogsDir)
}

function Get-ODSNativeHealthUrl {
    param([Parameter(Mandatory = $true)][int]$Port)
    return "http://127.0.0.1:$Port/health"
}
