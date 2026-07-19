# ============================================================================
# ODS Windows Installer -- Backend Contract Loader
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Read backend contracts from an explicit ODS root path.
# ============================================================================

function Get-ODSBackendContract {
    <#
    .SYNOPSIS
        Read a backend contract JSON file from a known ODS root.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath,

        [string]$Backend = "amd"
    )

    if ([string]::IsNullOrWhiteSpace($RootPath)) {
        throw "RootPath is required to load backend contract '$Backend'."
    }

    $resolvedRoot = Resolve-Path -LiteralPath $RootPath -ErrorAction Stop
    $contractPath = Join-Path (Join-Path (Join-Path $resolvedRoot.Path "config") "backends") "$Backend.json"
    if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) {
        throw "Backend contract not found: $contractPath"
    }

    try {
        $raw = Get-Content -LiteralPath $contractPath -Raw -ErrorAction Stop
        $contract = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Invalid backend contract '$contractPath': $($_.Exception.Message)"
    }

    if (-not $contract.id -or $contract.id -ne $Backend) {
        throw "Backend contract '$contractPath' has id '$($contract.id)', expected '$Backend'."
    }

    return $contract
}

function Get-ODSAmdLemonadeRuntime {
    <#
    .SYNOPSIS
        Return the AMD Lemonade runtime contract from config/backends/amd.json.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath
    )

    $contract = Get-ODSBackendContract -RootPath $RootPath -Backend "amd"
    if (-not $contract.runtime -or -not $contract.runtime.lemonade) {
        throw "AMD backend contract is missing runtime.lemonade."
    }

    $lemonade = $contract.runtime.lemonade
    $required = @(
        "container_image",
        "windows_version",
        "windows_msi_file",
        "windows_executable",
        "api_port",
        "health_path",
        "linux_backend",
        "windows_backend"
    )
    foreach ($field in $required) {
        if (-not $lemonade.PSObject.Properties[$field] -or [string]::IsNullOrWhiteSpace([string]$lemonade.$field)) {
            throw "AMD Lemonade runtime contract is missing '$field'."
        }
    }

    return $lemonade
}

function Get-ODSLemonadeUserInstallDir {
    <#
    .SYNOPSIS
        Return Lemonade's supported per-user Windows install root.

    .DESCRIPTION
        The Lemonade minimal MSI defaults to a per-user installation below
        LOCALAPPDATA. ODS itself must run as a normal user, so this is the
        primary runtime location; Program Files remains a legacy/all-users
        fallback for existing installations.
    #>
    [CmdletBinding()]
    param()

    $localAppData = $env:LOCALAPPDATA
    if ([string]::IsNullOrWhiteSpace($localAppData)) {
        $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    }
    if ([string]::IsNullOrWhiteSpace($localAppData)) { return $null }

    return (Join-Path $localAppData "lemonade_server")
}

function Get-ODSLemonadeExeCandidatePaths {
    <#
    .SYNOPSIS
        Return native Windows Lemonade executable candidates across MSI roots.

    .DESCRIPTION
        Lemonade's minimal MSI installs per-user below LOCALAPPDATA by default.
        Older/all-users installs can land under either Program Files root depending
        on package architecture and Windows installer behavior. Recent MSI builds
        also install LemonadeServer.exe instead of the historical
        lemonade-server.exe. Keep this candidate list shared between resolver
        and installer diagnostics so failures name the roots that were checked.
    #>
    [CmdletBinding()]
    param(
        [string]$ExecutableName = "lemonade-server.exe"
    )

    $candidates = New-Object System.Collections.Generic.List[string]

    $userInstallDir = Get-ODSLemonadeUserInstallDir
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)})
    $installFolders = @("Lemonade Server", "lemonade_server", "LemonadeServer")
    $executableNames = @($ExecutableName, "LemonadeServer.exe", "lemonade-server.exe") |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Unique

    # Prefer the normal per-user path. The root itself is already lemonade_server,
    # so it must be checked before the generic Program Files permutations.
    if (-not [string]::IsNullOrWhiteSpace($userInstallDir)) {
        foreach ($name in $executableNames) {
            $candidates.Add((Join-Path (Join-Path $userInstallDir "bin") $name))
        }
    }

    $existingVar = Get-Variable -Name LEMONADE_EXE -Scope Script -ErrorAction SilentlyContinue
    if ($existingVar -and -not [string]::IsNullOrWhiteSpace([string]$existingVar.Value)) {
        $candidates.Add([string]$existingVar.Value)
    }
    foreach ($root in $roots) {
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        foreach ($folder in $installFolders) {
            foreach ($name in $executableNames) {
                $candidates.Add((Join-Path (Join-Path (Join-Path $root $folder) "bin") $name))
            }
        }
    }

    return @($candidates | Select-Object -Unique)
}

function Resolve-ODSLemonadeExe {
    <#
    .SYNOPSIS
        Resolve the native Windows Lemonade executable across both MSI roots.
    #>
    [CmdletBinding()]
    param(
        [string]$ExecutableName = "lemonade-server.exe"
    )

    foreach ($candidate in (Get-ODSLemonadeExeCandidatePaths -ExecutableName $ExecutableName)) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    return $null
}

function Get-ODSLemonadeExecutableVersion {
    <#
    .SYNOPSIS
        Return the normalized Lemonade executable version.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExecutablePath,

        [string]$VersionOverride
    )

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($VersionOverride)) {
        $candidates.Add($VersionOverride)
    } else {
        if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
            throw "Lemonade executable not found: $ExecutablePath"
        }
        $item = Get-Item -LiteralPath $ExecutablePath -ErrorAction Stop
        if ($item.VersionInfo) {
            $candidates.Add([string]$item.VersionInfo.ProductVersion)
            $candidates.Add([string]$item.VersionInfo.FileVersion)
        }
    }

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $match = [regex]::Match($candidate, '\d+(?:\.\d+){1,3}')
        if (-not $match.Success) { continue }
        try {
            return [Version]$match.Value
        } catch { }
    }

    throw "Could not determine Lemonade version from '$ExecutablePath'."
}

function Test-ODSLoopbackAddress {
    [CmdletBinding()]
    param([string]$Address)

    $normalized = ([string]$Address).Trim().ToLowerInvariant()
    return $normalized -in @("", "localhost", "127.0.0.1", "::1", "[::1]")
}

function Get-ODSEnvFileValue {
    [CmdletBinding()]
    param(
        [string]$EnvPath,
        [Parameter(Mandatory = $true)]
        [string]$Key
    )

    if ([string]::IsNullOrWhiteSpace($EnvPath) -or -not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) {
        return $null
    }
    foreach ($line in (Get-Content -LiteralPath $EnvPath -ErrorAction SilentlyContinue)) {
        if ($line -match ('^\s*' + [regex]::Escape($Key) + '=(.*)$')) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Get-ODSLemonadeAdminApiKey {
    <#
    .SYNOPSIS
        Resolve the admin-only key used to protect Lemonade /internal routes.

    .DESCRIPTION
        ODS already generates LITELLM_LEMONADE_API_KEY for AMD installs. Reuse
        that secret as Lemonade's process-local admin key rather than persisting
        another credential or exposing internal control endpoints on LAN binds.
    #>
    [CmdletBinding()]
    param([string]$EnvPath)

    if (-not [string]::IsNullOrWhiteSpace($env:LEMONADE_ADMIN_API_KEY)) {
        return $env:LEMONADE_ADMIN_API_KEY
    }
    foreach ($key in @("LEMONADE_ADMIN_API_KEY", "LITELLM_LEMONADE_API_KEY")) {
        $value = Get-ODSEnvFileValue -EnvPath $EnvPath -Key $key
        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
    }
    return $null
}

function Get-ODSLemonadeLaunchContract {
    <#
    .SYNOPSIS
        Build the version-specific Windows Lemonade launch contract.

    .DESCRIPTION
        Lemonade 10.7 removed the legacy --no-tray, --llamacpp, and
        --extra-models-dir startup options. It accepts only server startup
        options such as --port and --host; runtime settings move to
        /internal/set. Older ODS-pinned releases retain the legacy contract.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExecutablePath,

        [Parameter(Mandatory = $true)]
        [int]$Port,

        [string]$BindAddress = "127.0.0.1",

        [Parameter(Mandatory = $true)]
        [string]$ModelsDir,

        [string]$AdminApiKey,

        [string]$VersionOverride
    )

    if ($Port -lt 1 -or $Port -gt 65535) { throw "Invalid Lemonade port: $Port" }
    if ([string]::IsNullOrWhiteSpace($BindAddress)) { $BindAddress = "127.0.0.1" }

    $version = Get-ODSLemonadeExecutableVersion `
        -ExecutablePath $ExecutablePath -VersionOverride $VersionOverride
    $modern = $version -ge [Version]"10.7.0"
    $effectiveBind = $BindAddress
    if ($modern -and -not (Test-ODSLoopbackAddress -Address $effectiveBind) -and
        [string]::IsNullOrWhiteSpace($AdminApiKey)) {
        throw "Lemonade 10.7+ requires an admin API key before binding to '$effectiveBind'."
    }

    if ($modern) {
        $argumentList = @("--port", [string]$Port, "--host", $effectiveBind)
        $argumentString = $argumentList -join " "
    } else {
        $escapedModelsDir = ([string]$ModelsDir).Replace('"', '\"')
        $argumentList = @(
            "serve", "--port", [string]$Port, "--host", $effectiveBind,
            "--no-tray", "--llamacpp", "vulkan", "--extra-models-dir", $ModelsDir
        )
        $argumentString = "serve --port $Port --host $effectiveBind --no-tray --llamacpp vulkan --extra-models-dir `"$escapedModelsDir`""
    }

    return [pscustomobject]@{
        Version = $version
        Modern = $modern
        ExecutablePath = $ExecutablePath
        Port = $Port
        BindAddress = $effectiveBind
        RequestedBindAddress = $BindAddress
        ModelsDir = $ModelsDir
        AdminApiKey = $AdminApiKey
        ArgumentList = $argumentList
        ArgumentString = $argumentString
        RequiresRuntimeConfiguration = $modern
    }
}

function ConvertTo-ODSPowerShellSingleQuotedLiteral {
    param([AllowNull()][string]$Value)
    if ($null -eq $Value) { return "''" }
    return "'" + $Value.Replace("'", "''") + "'"
}

function New-ODSLemonadeScheduledTaskAction {
    <#
    .SYNOPSIS
        Create the scheduled-task action for a Lemonade launch contract.

    .DESCRIPTION
        Modern Lemonade needs a process-local admin key on non-loopback binds.
        The task action reads it from the ODS env file at runtime, so the secret
        is not embedded in Task Scheduler XML. The wrapper waits for the child,
        preserving Lemonade's exit code as LastTaskResult.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$Contract,

        [string]$EnvPath,

        [string]$DiagnosticLogPath
    )

    $workingDirectory = Split-Path -Parent $Contract.ExecutablePath
    if (-not $Contract.Modern) {
        return New-ScheduledTaskAction -Execute $Contract.ExecutablePath `
            -Argument $Contract.ArgumentString -WorkingDirectory $workingDirectory
    }

    $exeLiteral = ConvertTo-ODSPowerShellSingleQuotedLiteral $Contract.ExecutablePath
    $argsLiteral = ConvertTo-ODSPowerShellSingleQuotedLiteral $Contract.ArgumentString
    $workLiteral = ConvertTo-ODSPowerShellSingleQuotedLiteral $workingDirectory
    $envLiteral = ConvertTo-ODSPowerShellSingleQuotedLiteral $EnvPath
    $logLiteral = ConvertTo-ODSPowerShellSingleQuotedLiteral $DiagnosticLogPath
    $wrapper = @"
`$ErrorActionPreference = 'Stop'
`$exe = $exeLiteral
`$argumentString = $argsLiteral
`$workingDirectory = $workLiteral
`$envPath = $envLiteral
`$diagnosticLog = $logLiteral
function Read-ODSLauncherEnvValue([string]`$key) {
    if ([string]::IsNullOrWhiteSpace(`$envPath) -or -not (Test-Path -LiteralPath `$envPath -PathType Leaf)) { return `$null }
    foreach (`$line in (Get-Content -LiteralPath `$envPath -ErrorAction SilentlyContinue)) {
        if (`$line -match ('^\s*' + [regex]::Escape(`$key) + '=(.*)$')) { return `$Matches[1].Trim().Trim('"').Trim("'") }
    }
    return `$null
}
try {
    `$adminKey = `$env:LEMONADE_ADMIN_API_KEY
    if ([string]::IsNullOrWhiteSpace(`$adminKey)) { `$adminKey = Read-ODSLauncherEnvValue 'LEMONADE_ADMIN_API_KEY' }
    if ([string]::IsNullOrWhiteSpace(`$adminKey)) { `$adminKey = Read-ODSLauncherEnvValue 'LITELLM_LEMONADE_API_KEY' }
    if (-not [string]::IsNullOrWhiteSpace(`$adminKey)) { `$env:LEMONADE_ADMIN_API_KEY = `$adminKey }
    `$child = Start-Process -FilePath `$exe -ArgumentList `$argumentString -WorkingDirectory `$workingDirectory -WindowStyle Hidden -PassThru
    `$child.WaitForExit()
    exit `$child.ExitCode
} catch {
    if (-not [string]::IsNullOrWhiteSpace(`$diagnosticLog)) {
        try { "`$(Get-Date -Format o) task wrapper failed: `$(`$_.Exception.Message)" | Out-File -LiteralPath `$diagnosticLog -Append -Encoding utf8 } catch {}
    }
    exit 1
}
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($wrapper))
    return New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand $encoded" `
        -WorkingDirectory $workingDirectory
}

function Start-ODSLemonadeDirectProcess {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][psobject]$Contract)

    $previousAdminKey = $env:LEMONADE_ADMIN_API_KEY
    try {
        if (-not [string]::IsNullOrWhiteSpace([string]$Contract.AdminApiKey)) {
            $env:LEMONADE_ADMIN_API_KEY = [string]$Contract.AdminApiKey
        }
        return Start-Process -FilePath $Contract.ExecutablePath `
            -ArgumentList $Contract.ArgumentString -WindowStyle Hidden `
            -WorkingDirectory (Split-Path -Parent $Contract.ExecutablePath) -PassThru
    } finally {
        if ($null -eq $previousAdminKey) {
            Remove-Item Env:\LEMONADE_ADMIN_API_KEY -ErrorAction SilentlyContinue
        } else {
            $env:LEMONADE_ADMIN_API_KEY = $previousAdminKey
        }
    }
}

function Set-ODSLemonadeModernRuntimeConfig {
    <#
    .SYNOPSIS
        Configure and verify the Lemonade 10.7+ runtime contract.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,

        [Parameter(Mandatory = $true)]
        [string]$ModelsDir,

        [string]$AdminApiKey
    )

    $headers = @{}
    if (-not [string]::IsNullOrWhiteSpace($AdminApiKey)) {
        $headers["Authorization"] = "Bearer $AdminApiKey"
    }
    $baseUrl = "http://127.0.0.1:$Port"
    $payload = [ordered]@{
        extra_models_dir = [System.IO.Path]::GetFullPath($ModelsDir)
        llamacpp = [ordered]@{
            backend = "vulkan"
        }
    }
    $body = $payload | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Method Post -Uri "$baseUrl/internal/set" `
        -Headers $headers -ContentType "application/json" -Body $body `
        -TimeoutSec 10 -ErrorAction Stop
    $config = Invoke-RestMethod -Method Get -Uri "$baseUrl/internal/config" `
        -Headers $headers -TimeoutSec 10 -ErrorAction Stop

    $expectedModelsDir = [System.IO.Path]::GetFullPath($ModelsDir)
    $actualModelsDir = [string]$config.extra_models_dir
    $trimChars = [char[]]@(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $normalizedExpected = $expectedModelsDir.TrimEnd($trimChars)
    $normalizedActual = $actualModelsDir.TrimEnd($trimChars)
    if (-not $normalizedActual.Equals($normalizedExpected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Lemonade extra_models_dir verification failed: expected '$expectedModelsDir', got '$actualModelsDir'."
    }
    if ([string]$config.llamacpp.backend -ne "vulkan") {
        throw "Lemonade llamacpp backend verification failed: expected 'vulkan', got '$($config.llamacpp.backend)'."
    }
    return $config
}

function Resolve-ODSLemonadeModelId {
    <#
    .SYNOPSIS
        Resolve the request model ID Lemonade assigned to a local GGUF.

    .DESCRIPTION
        Lemonade releases before 10.7 expose extra-directory GGUFs as
        extra.<file>.gguf. Lemonade 10.7 exposes the filename stem instead.
        Prefer the live model catalog so future naming changes remain safe,
        then fall back to the runtime version when the catalog is still
        refreshing after launch.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,

        [Parameter(Mandatory = $true)]
        [string]$GgufFile,

        [string]$VersionOverride
    )

    if ($Port -lt 1 -or $Port -gt 65535) { throw "Invalid Lemonade port: $Port" }
    $targetFile = [IO.Path]::GetFileName($GgufFile)
    if ([string]::IsNullOrWhiteSpace($targetFile)) { throw "GGUF filename is required." }
    $targetStem = [IO.Path]::GetFileNameWithoutExtension($targetFile)

    try {
        $catalog = Invoke-RestMethod -Method Get `
            -Uri "http://127.0.0.1:$Port/api/v1/models" `
            -TimeoutSec 10 -ErrorAction Stop
        foreach ($entry in @($catalog.data)) {
            if ($null -eq $entry) { continue }
            $id = [string]$entry.id
            if ([string]::IsNullOrWhiteSpace($id)) { continue }
            $tokens = New-Object System.Collections.Generic.List[string]
            $tokens.Add($id)
            if (-not [string]::IsNullOrWhiteSpace([string]$entry.checkpoint)) {
                $tokens.Add([string]$entry.checkpoint)
            }
            if ($entry.checkpoints) {
                foreach ($property in $entry.checkpoints.PSObject.Properties) {
                    if (-not [string]::IsNullOrWhiteSpace([string]$property.Value)) {
                        $tokens.Add([string]$property.Value)
                    }
                }
            }
            foreach ($token in $tokens) {
                $normalized = ([string]$token).Replace([IO.Path]::DirectorySeparatorChar, '/')
                $leaf = ($normalized -split '/')[-1]
                if ($leaf.Contains(':')) { $leaf = ($leaf -split ':')[-1] }
                if ($leaf.Equals($targetFile, [StringComparison]::OrdinalIgnoreCase) -or
                    $leaf.Equals($targetStem, [StringComparison]::OrdinalIgnoreCase) -or
                    $id.Equals($targetFile, [StringComparison]::OrdinalIgnoreCase) -or
                    $id.Equals($targetStem, [StringComparison]::OrdinalIgnoreCase) -or
                    $id.Equals("extra.$targetFile", [StringComparison]::OrdinalIgnoreCase)) {
                    return $id
                }
            }
        }
    } catch { }

    $versionText = $VersionOverride
    if ([string]::IsNullOrWhiteSpace($versionText)) {
        try {
            $health = Invoke-RestMethod -Method Get `
                -Uri "http://127.0.0.1:$Port/api/v1/health" `
                -TimeoutSec 5 -ErrorAction Stop
            $versionText = [string]$health.version
        } catch { }
    }
    $versionMatch = [regex]::Match([string]$versionText, '\d+(?:\.\d+){1,3}')
    if ($versionMatch.Success) {
        try {
            if ([Version]$versionMatch.Value -ge [Version]'10.7.0') {
                return $targetStem
            }
        } catch { }
    }
    return "extra.$targetFile"
}

function Get-ODSLemonadeLaunchDiagnostics {
    [CmdletBinding()]
    param(
        [string]$TaskName = "ODSLemonadeRuntime",
        [object]$ChildProcess,
        [int]$LogTailLines = 40
    )

    $childExitCode = $null
    if ($ChildProcess) {
        try {
            $ChildProcess.Refresh()
            if ($ChildProcess.HasExited) { $childExitCode = [int]$ChildProcess.ExitCode }
        } catch { }
    }

    $taskState = $null
    $taskResult = $null
    try { $taskState = [string](Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue).State } catch { }
    try { $taskResult = (Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue).LastTaskResult } catch { }

    $logPath = Join-Path $env:TEMP "lemonade-server.log"
    $logTail = @()
    if (Test-Path -LiteralPath $logPath -PathType Leaf) {
        $logTail = @(Get-Content -LiteralPath $logPath -Tail $LogTailLines -ErrorAction SilentlyContinue)
    }
    return [pscustomobject]@{
        ChildExitCode = $childExitCode
        TaskState = $taskState
        TaskResult = $taskResult
        LogPath = $logPath
        LogTail = $logTail
    }
}

function Format-ODSLemonadeLaunchDiagnostics {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][psobject]$Diagnostics)

    $taskResult = if ($null -eq $Diagnostics.TaskResult) { "unknown" } else { [string]$Diagnostics.TaskResult }
    $childExit = if ($null -eq $Diagnostics.ChildExitCode) { "unknown" } else { [string]$Diagnostics.ChildExitCode }
    $summary = "child exit=$childExit; task state=$($Diagnostics.TaskState); task result=$taskResult"
    if (@($Diagnostics.LogTail).Count -gt 0) {
        $summary += "`nLemonade log ($($Diagnostics.LogPath)):`n" + (@($Diagnostics.LogTail) -join "`n")
    }
    return $summary
}
