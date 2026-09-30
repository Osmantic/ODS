# Install only ComfyUI on this Windows GPU host. This fixed Compose project and
# data root are independent of a full ODS install on this or another machine.
[CmdletBinding()]
param(
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'Osmantic\ComfyUI-Standalone'),
    [ValidateRange(1024, 65535)][int]$Port = 8188,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($env:OS -ne 'Windows_NT') { throw 'The standalone ComfyUI installer requires Windows.' }
if ([string]::IsNullOrWhiteSpace($DataRoot) -or
    -not [System.IO.Path]::IsPathRooted($DataRoot)) {
    throw 'DataRoot must be an absolute local Windows path.'
}

$ProjectName = 'ods-comfyui-standalone'
$ContainerName = 'ods-comfyui-standalone'
$MinimumFreeBytes = 25GB
$SourceDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\..\extensions\services\comfyui'))
$ComposeFile = Join-Path $SourceDir 'compose.standalone.nvidia.yaml'
$Dockerfile = Join-Path $SourceDir 'Dockerfile.standalone'
$Root = [System.IO.Path]::GetFullPath($DataRoot).TrimEnd('\', '/').Replace('/', '\')
$NormalizedRoot = $Root.Replace('\', '/')

function Test-WithinPath {
    param([string]$Candidate, [string]$Parent)
    $normalizedParent = $Parent.TrimEnd('\', '/').Replace('/', '\')
    $normalizedCandidate = $Candidate.TrimEnd('\', '/').Replace('/', '\')
    $parentWithSlash = $normalizedParent + '\'
    return ($normalizedCandidate.Equals($normalizedParent,
                [System.StringComparison]::OrdinalIgnoreCase) -or
            $normalizedCandidate.StartsWith($parentWithSlash,
                [System.StringComparison]::OrdinalIgnoreCase))
}

function Assert-NoReparseAncestor {
    param([string]$Path)
    $current = $Path
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "DataRoot traverses a reparse point: $current"
            }
        }
        $parent = Split-Path -Parent $current
        if (-not $parent -or $parent -eq $current) { break }
        $current = $parent
    }
}

# These queries contain fixed arguments without spaces. Reading both streams
# asynchronously prevents a blocked Docker daemon from hanging the preflight.
function Invoke-BoundedDockerQuery {
    param([string[]]$Arguments, [int]$TimeoutSeconds = 20)
    foreach ($argument in $Arguments) {
        if ($argument -match '\s') { throw 'Internal Docker query has an unsafe argument.' }
    }
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = (Get-Command docker.exe -ErrorAction Stop).Source
    $start.Arguments = $Arguments -join ' '
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    [void]$process.Start()
    $outputTask = $process.StandardOutput.ReadToEndAsync()
    $errorTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch { }
        [void]$process.WaitForExit(5000)
        throw "Docker did not answer within $TimeoutSeconds seconds. Check Docker Desktop before retrying."
    }
    [void]$process.WaitForExit()
    try {
        $output = [string]$outputTask.GetAwaiter().GetResult()
        $errorText = [string]$errorTask.GetAwaiter().GetResult()
    } catch {
        throw 'Docker answered but its output could not be read.'
    }
    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Output = $output.Trim()
        Error = $errorText.Trim()
    }
}

function Assert-FreeSpace {
    param([string]$Path)
    $driveRoot = [System.IO.Path]::GetPathRoot($Path)
    if ($driveRoot -notmatch '^[A-Za-z]:\\$') {
        throw 'DataRoot and Docker Desktop storage must be on a local drive.'
    }
    $drive = Get-PSDrive -Name $driveRoot.Substring(0, 1) -ErrorAction Stop
    if ($drive.Free -lt $MinimumFreeBytes) {
        throw ("Not enough free disk on {0}: {1:N1} GiB available; at least 25 GiB is required before a CUDA/PyTorch image build." -f
            $driveRoot, ($drive.Free / 1GB))
    }
}

function Assert-PortFree {
    param([int]$Number)
    $listener = New-Object System.Net.Sockets.TcpListener(
        [System.Net.IPAddress]::Loopback, $Number)
    try { $listener.Start() }
    catch { throw "Loopback port $Number is already in use." }
    finally { $listener.Stop() }
}

function Wait-ComfyuiHealthy {
    $deadline = (Get-Date).AddMinutes(5)
    $lastInspectError = ''
    while ((Get-Date) -lt $deadline) {
        $health = Invoke-BoundedDockerQuery -Arguments @(
            '--context', 'desktop-linux', 'inspect', '--format',
            '{{.State.Health.Status}}', $ContainerName)
        if ($health.ExitCode -ne 0) {
            $lastInspectError = $health.Error
            Start-Sleep -Seconds 5
            continue
        }
        if ($health.Output -eq 'healthy') {
            try {
                $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" `
                    -UseBasicParsing -TimeoutSec 5
                if ($response.StatusCode -eq 200) { return }
            } catch { }
        }
        if ($health.Output -eq 'unhealthy') { break }
        Start-Sleep -Seconds 5
    }
    throw "Standalone ComfyUI did not become healthy. Last inspect error: $lastInspectError. Inspect: docker --context desktop-linux logs $ContainerName"
}

function Assert-ComfyuiCuda {
    $smoke = Invoke-BoundedDockerQuery -Arguments @(
        '--context', 'desktop-linux', 'exec', $ContainerName,
        'python3', '/opt/cuda-smoke.py') -TimeoutSeconds 180
    if ($smoke.ExitCode -ne 0) {
        $detail = if ($smoke.Error) { $smoke.Error } else { $smoke.Output }
        $message = 'ComfyUI responds over HTTP, but its CUDA runtime failed a real GPU operation.'
        if ($detail) {
            if ($detail.Length -gt 2048) { $detail = $detail.Substring(0, 2048) + '...' }
            $message += " Detail: $detail"
        }
        throw $message
    }
    if ($smoke.Output) { Write-Output $smoke.Output }
}

if ($Root -match '[\x00-\x1f\x7f]') { throw 'DataRoot contains control characters.' }
if ([System.IO.Path]::GetPathRoot($Root).TrimEnd('\', '/') -eq $Root) {
    throw 'DataRoot cannot be a drive root.'
}
Assert-NoReparseAncestor -Path $Root
if (Test-WithinPath -Candidate $Root -Parent $SourceDir) {
    throw 'DataRoot cannot be inside the ODS source tree.'
}
if ($env:ODS_HOME) {
    $odsHome = [System.IO.Path]::GetFullPath($env:ODS_HOME).TrimEnd('\', '/')
    if (Test-WithinPath -Candidate $Root -Parent $odsHome) {
        throw 'DataRoot cannot be inside the existing ODS installation.'
    }
}
foreach ($file in @($ComposeFile, $Dockerfile, (Join-Path $SourceDir 'startup.sh'))) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing source file: $file" }
}

$gpu = & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null
if ($LASTEXITCODE -ne 0 -or -not $gpu) { throw 'No Windows NVIDIA GPU was found.' }

Write-Host "Standalone ComfyUI: project=$ProjectName port=$Port data=$Root"

if ($env:DOCKER_HOST -or ($env:DOCKER_CONTEXT -and $env:DOCKER_CONTEXT -ne 'desktop-linux')) {
    throw 'A Docker host/context override is set. Use the local Docker Desktop Linux context.'
}
$endpoint = Invoke-BoundedDockerQuery -Arguments @(
    'context', 'inspect', 'desktop-linux', '--format', '{{.Endpoints.docker.Host}}')
if ($endpoint.ExitCode -ne 0 -or
    $endpoint.Output -notmatch '^npipe:////\./pipe/dockerDesktopLinuxEngine$') {
    throw 'The desktop-linux context is not the local Docker Desktop Linux engine.'
}
$server = Invoke-BoundedDockerQuery -Arguments @(
    '--context', 'desktop-linux', 'info', '--format', '{{.OSType}}')
if ($server.ExitCode -ne 0 -or $server.Output -ne 'linux') {
    throw 'Docker Desktop Linux engine is unavailable. Start it and retry.'
}
$composeVersion = Invoke-BoundedDockerQuery -Arguments @(
    '--context', 'desktop-linux', 'compose', 'version', '--short')
if ($composeVersion.ExitCode -ne 0 -or -not $composeVersion.Output) {
    throw 'The Docker Compose plugin is required.'
}

$temporaryEnvironment = @{}
foreach ($name in @('ODS_COMFYUI_DATA_ROOT', 'ODS_COMFYUI_PORT', 'COMPOSE_FILE')) {
    $temporaryEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
$env:ODS_COMFYUI_DATA_ROOT = $NormalizedRoot
$env:ODS_COMFYUI_PORT = [string]$Port
$compose = @('--context', 'desktop-linux', 'compose', '-p', $ProjectName,
    '-f', $ComposeFile)
$env:COMPOSE_FILE = $ComposeFile
$services = Invoke-BoundedDockerQuery -Arguments @('--context', 'desktop-linux',
    'compose', '-p', $ProjectName, 'config', '--services')
if ($services.ExitCode -ne 0 -or $services.Output -ne 'comfyui') {
    throw 'Standalone Compose must resolve exactly one ComfyUI service.'
}

$names = Invoke-BoundedDockerQuery -Arguments @('--context', 'desktop-linux',
    'ps', '-a', '--filter', ('name=^/' + $ContainerName + '$'), '--format', '{{.Names}}')
if ($names.ExitCode -ne 0) { throw "Could not inspect existing Docker containers: $($names.Error)" }
$existing = $names.Output -eq $ContainerName
if ($existing) {
    $details = Invoke-BoundedDockerQuery -Arguments @('--context', 'desktop-linux',
        'container', 'inspect', $ContainerName)
    if ($details.ExitCode -ne 0) { throw "Could not inspect $ContainerName" }
    $installed = @(ConvertFrom-Json -InputObject $details.Output)[0]
    $labels = $installed.Config.Labels
    if ($labels.'com.docker.compose.project' -ne $ProjectName) {
        throw "A foreign container named $ContainerName exists. It was not changed."
    }
    $savedRoot = [string]$labels.'org.osmantic.ods.comfyui.data-root'
    $savedPort = [string]$labels.'org.osmantic.ods.comfyui.port'
    if (-not $savedRoot.Equals($NormalizedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $savedPort -ne [string]$Port) {
        throw 'The existing standalone container uses a different data root or port. It was not changed.'
    }
    $healthState = $installed.State.PSObject.Properties['Health']
    if ($installed.State.Running -and $healthState -and
        $healthState.Value.Status -eq 'healthy') {
        if ($DryRun) {
            Write-Host 'Dry run: existing standalone container and its data root were verified; no changes made.'
            return
        }
        Wait-ComfyuiHealthy
        Assert-ComfyuiCuda
        Write-Host "Standalone ComfyUI is already healthy at http://127.0.0.1:$Port/"
        return
    }
}

if (-not $existing) {
    Assert-PortFree -Number $Port
    # A new CUDA/PyTorch build needs room in Docker Desktop's Windows disk
    # image and the bind-mount drive. A healthy or stopped installation can be
    # resumed after those files already exist, even if free space fell below
    # the new-build threshold.
    Assert-FreeSpace -Path $env:LOCALAPPDATA
    Assert-FreeSpace -Path $Root
}
if ($DryRun) {
    Write-Host 'Dry run: local Docker Desktop, Compose, Windows GPU, project isolation, port, and applicable disk checks passed; no changes made.'
    return
}
foreach ($directory in @('models', 'output', 'input', 'workflows', 'user', 'custom_nodes')) {
    $target = Join-Path $Root $directory
    if (-not (Test-Path -LiteralPath $target)) {
        New-Item -ItemType Directory -Path $target -Force | Out-Null
    } elseif (-not (Test-Path -LiteralPath $target -PathType Container)) {
        throw "ComfyUI data path is not a directory: $target"
    }
    $item = Get-Item -LiteralPath $target -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "ComfyUI data path is a reparse point: $target"
    }
}
Assert-NoReparseAncestor -Path $Root

if (-not $existing) {
    # This pinned CUDA base is also the Dockerfile base. Probe the local GPU
    # before the much larger Python build; the pulled layer is reused.
    $cudaImage = 'nvidia/cuda:12.8.0-runtime-ubuntu22.04@sha256:b0e5afeabc356a9c41148c50f219d3117d46cb6ec23c590e3a0ebaa1db0ff946'
    & docker --context desktop-linux run --rm --gpus all --pull=always `
        --entrypoint nvidia-smi $cudaImage -L
    if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop cannot pass the NVIDIA GPU to a Linux container.' }
    & docker @compose build comfyui
    if ($LASTEXITCODE -ne 0) { throw 'Standalone ComfyUI image build failed; data was preserved.' }
}

& docker @compose up -d --no-build comfyui
if ($LASTEXITCODE -ne 0) { throw 'Standalone ComfyUI launch failed; data was preserved.' }
Wait-ComfyuiHealthy
Assert-ComfyuiCuda
Write-Host "Standalone ComfyUI is healthy at http://127.0.0.1:$Port/"
} finally {
    # An owner can invoke this script with & from an existing PowerShell
    # session. Do not leave this isolated project's Compose selection or data
    # root in that session after success, a dry run, or a failed build.
    foreach ($name in @('ODS_COMFYUI_DATA_ROOT', 'ODS_COMFYUI_PORT', 'COMPOSE_FILE')) {
        [Environment]::SetEnvironmentVariable($name, $temporaryEnvironment[$name], 'Process')
    }
}
