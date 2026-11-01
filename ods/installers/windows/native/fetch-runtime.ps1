# ============================================================================
# ODS Windows-native -- fetch, verify and (optionally) smoke-start llama.cpp
# ============================================================================
# Part of: installers/windows/native/
# Purpose: Install the pinned llama.cpp CPU build (config/backends/cpu.json)
#          into a given state root, verify it, and, with -ModelPath, start
#          llama-server on loopback behind an API key file and prove it
#          answers. CI's windows-native workflow calls this; it is a thin
#          wrapper around installers/windows/lib/native-llama-runtime.ps1.
#
# Parameters:
#   -StateRoot <dir>        Required. The runtime goes to
#                           <StateRoot>\llama.cpp\<tag>-win-cpu-<arch>; the key
#                           file, log and pid file to <StateRoot>\native-runtime.
#                           Use a long (not 8.3) absolute path such as
#                           $env:RUNNER_TEMP: the listener proof compares it
#                           with the running process's path.
#   -Destination <dir>      Optional. Also publish the verified runtime here
#                           (the bundle builder passes <staging>\llama, so the
#                           supervisor finds <install_root>\llama\
#                           llama-server.exe). The copy carries pin.json and
#                           is re-verified; an existing verified copy of the
#                           same pin is kept, any other is replaced. The smoke
#                           start then runs this copy.
#   -ApiKeyPath <file>      Key file for the smoke start (default
#                           <StateRoot>\native-runtime\api-key; the supervisor
#                           uses <state>\secrets\llama-server-api-key). An
#                           existing file must hold a valid key and is reused;
#                           otherwise a new owner-only key file is written.
#   -Architecture x64|arm64 Defaults to this Windows' processor architecture.
#   -SourceRoot <dir>       The ODS tree holding config/backends/cpu.json.
#                           Defaults to the tree this script is in.
#   -ModelPath <file.gguf>  Optional. Start llama-server with this model and
#                           run the smoke proof (below). Without it the script
#                           only installs, verifies and qualifies the runtime.
#   -Port <n>               Loopback port for the smoke start (default 18080).
#   -ContextSize <n>        Context for the smoke start (default 2048).
#   -NativeApp              Launch with the native-app profile (built-in web UI
#                           on). Default is the 'api' profile (--no-webui).
#   -StartupTimeoutSeconds  How long the model may take to load (default 180).
#   -StopAfterProof         Stop llama-server after the proof. Otherwise it
#                           keeps running and its pid is in llama-server.pid.
#
# Smoke proof (with -ModelPath): /health ready, /v1/models serves the GGUF,
# /props reports the model and context (with the key), /props without the
# key answers 401, and POST /completion with the key generates. With
# -NativeApp, GET / must serve the web UI; without it, GET / must not.
#
# Output: one JSON line on stdout: ExecutablePath (the -Destination copy when
# given), RuntimeDirectory, ReleaseTag, Flavor, Build,
# and with -ModelPath also Port, ProcessId, ApiKeyPath, LogPath, Running.
# Any failure throws (exit code 1) with the cause.
#
# Expects: Windows, curl.exe (Windows 10 1803+), Windows PowerShell 5.1 or
#          PowerShell 7.
# Modder notes: the pins, required files and launch arguments live in the
#          runtime library; change them there, not here.
# ============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$StateRoot,
    [string]$Destination = '',
    [string]$ApiKeyPath = '',
    [ValidateSet('x64', 'arm64', IgnoreCase = $false)][string]$Architecture = '',
    [string]$SourceRoot = '',
    [string]$ModelPath = '',
    [ValidateRange(1024, 65535)][int]$Port = 18080,
    [ValidateRange(256, 1048576)][int]$ContextSize = 2048,
    [switch]$NativeApp,
    [ValidateRange(10, 3600)][int]$StartupTimeoutSeconds = 180,
    [switch]$StopAfterProof
)
$ErrorActionPreference = 'Stop'
. (Join-Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'lib') 'native-llama-runtime.ps1')

if (-not $SourceRoot) { $SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path }
if (-not $Architecture) {
    # PROCESSOR_ARCHITEW6432 is set when an x64 shell runs emulated on Arm64.
    $processor = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    $Architecture = if ($processor -ieq 'ARM64') { 'arm64' } elseif ($processor -ieq 'AMD64') { 'x64' } else {
        throw "Unsupported Windows processor architecture '$processor'; pass -Architecture x64 or arm64."
    }
}
foreach ($path in @($StateRoot, $Destination, $ApiKeyPath)) {
    if ($path -and -not [IO.Path]::IsPathRooted($path)) { throw "-StateRoot, -Destination and -ApiKeyPath must be absolute paths: $path" }
}

function Assert-ODSFetchHttp($Response, [int]$Expected, [string]$What) {
    if ($Response.StatusCode -ne $Expected) {
        throw "$What answered HTTP $($Response.StatusCode) ($($Response.Error)), expected $Expected."
    }
}

$pin = Get-ODSNativeLlamaPin -SourceRoot $SourceRoot -Backend cpu -Architecture $Architecture
$runtime = Install-ODSNativeLlamaRuntime -Pin $pin -Root (Join-Path $StateRoot 'llama.cpp')
$qualification = Test-ODSNativeLlamaQualification -ExecutablePath $runtime.ExecutablePath -ExpectedBuild $pin.Build `
    -Backend cpu -Architecture $Architecture
$executablePath = $runtime.ExecutablePath
$runtimeDirectory = $runtime.Directory
if ($Destination) {
    # Copies, then re-verifies the copy against its pin.json before publishing.
    $published = Publish-ODSNativeLlamaRuntimeCopy -Runtime $runtime -Destination $Destination
    $executablePath = $published.ExecutablePath
    $runtimeDirectory = $published.Directory
}
$result = [ordered]@{
    ExecutablePath = $executablePath
    RuntimeDirectory = $runtimeDirectory
    ReleaseTag = $runtime.ReleaseTag
    Flavor = $runtime.Flavor
    Build = $qualification.Build
}
if (-not $ModelPath) {
    Write-Output ($result | ConvertTo-Json -Compress)
    return
}

if (-not (Test-Path -LiteralPath $ModelPath -PathType Leaf)) { throw "-ModelPath does not exist: $ModelPath" }
$model = (Resolve-Path -LiteralPath $ModelPath).Path
$runtimeDir = Join-Path $StateRoot 'native-runtime'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$keyFile = if ($ApiKeyPath) { $ApiKeyPath } else { Join-Path $runtimeDir 'api-key' }
if (Test-Path -LiteralPath $keyFile -PathType Leaf) {
    $apiKey = Read-ODSNativeLlamaApiKey $keyFile
} else {
    New-Item -ItemType Directory -Path (Split-Path -Parent $keyFile) -Force | Out-Null
    $apiKey = New-ODSNativeLlamaApiKey
    Write-ODSNativeLlamaApiKeyFile -Path $keyFile -Key $apiKey
}
$plan = [pscustomobject]@{
    ExecutablePath = $executablePath
    ModelsDir = Split-Path -Parent $model
    GgufFile = Split-Path -Leaf $model
    ContextSize = $ContextSize
    Port = $Port
    MmprojFile = $null
}
$options = [pscustomobject]@{
    schemaVersion = 1
    Backend = 'cpu'
    Architecture = $Architecture
    Device = 'none'
    NGpuLayers = '0'
    ApiKeyPath = $keyFile
    LogPath = (Join-Path $runtimeDir 'llama-server.log')
    ReleaseTag = $runtime.ReleaseTag
    ZipSha256 = $runtime.ZipSha256
    Profile = $(if ($NativeApp) { 'native-app' } else { 'api' })
    ReasoningArguments = @()
    ExtraArguments = @()
}
# The same pin.json re-check the Portal launcher runs before every start.
$null = Test-ODSNativeLlamaInstall -Directory $runtimeDirectory -ExpectedZipSha256 $runtime.ZipSha256 -ExpectedReleaseTag $runtime.ReleaseTag
$arguments = New-ODSNativeLlamaLaunchArguments $plan $options
$child = Start-Process -FilePath $plan.ExecutablePath -ArgumentList (ConvertTo-ODSNativeLlamaArgumentString $arguments) `
    -WorkingDirectory $runtimeDirectory -WindowStyle Hidden -PassThru
$null = $child.Handle
[IO.File]::WriteAllText((Join-Path $runtimeDir 'llama-server.pid'), [string]$child.Id)
$stopped = $false
try {
    Wait-ODSNativeLlamaStartup -Port $Port -Process $child -TimeoutSeconds $StartupTimeoutSeconds -Backend cpu -Architecture $Architecture
    Assert-ODSNativeLlamaListener $Port $child.Id $plan.ExecutablePath
    $proof = Get-ODSNativeLlamaModelProof -Port $Port -GgufFile $plan.GgufFile -ModelPaths @($arguments[1], $model) `
        -ContextSize $ContextSize -ApiKey $apiKey
    if (-not $proof.ContextVerified) { throw $proof.Message }
    Assert-ODSFetchHttp (Invoke-ODSNativeLlamaHttp -Port $Port -Path '/props') 401 '/props without the API key'
    $completion = Invoke-ODSNativeLlamaHttp -Port $Port -Path '/completion' -ApiKey $apiKey -Method POST `
        -JsonBody '{"prompt":"Once upon a time","n_predict":8}' -TimeoutMilliseconds 60000
    Assert-ODSFetchHttp $completion 200 'POST /completion with the API key'
    if (-not [string]($completion.Body | ConvertFrom-Json -ErrorAction Stop).content) { throw 'POST /completion generated no text.' }
    $webUi = Invoke-ODSNativeLlamaHttp -Port $Port -Path '/'
    if ($NativeApp) { Assert-ODSFetchHttp $webUi 200 'The built-in web UI (GET /)' }
    elseif ($webUi.StatusCode -eq 200) { throw 'GET / served a page although the api profile passes --no-webui.' }
} catch {
    if (-not $child.HasExited) { $child.Kill(); $null = $child.WaitForExit(30000) }
    $stopped = $true
    throw
} finally {
    if ($StopAfterProof -and -not $stopped -and -not $child.HasExited) {
        $child.Kill()
        $null = $child.WaitForExit(30000)
        $stopped = $true
    }
}
$result['Port'] = $Port
$result['ProcessId'] = $child.Id
$result['ApiKeyPath'] = $keyFile
$result['LogPath'] = $options.LogPath
$result['Running'] = -not $stopped
Write-Output ($result | ConvertTo-Json -Compress)
