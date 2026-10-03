$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$installerPath = Join-Path $root 'installers/windows/install-windows.ps1'
$phasePath = Join-Path $root 'installers/windows/phases/05-docker.ps1'
$tokens = $null; $errors = $null
$installerAst = [Management.Automation.Language.Parser]::ParseFile($installerPath, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
if (-not ($installerAst.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'RepairGpuWsl' })) {
    throw 'Native installer does not expose -RepairGpuWsl'
}
if ($installerAst.Extent.Text -notmatch '\$repairGpuWslFlag\s*=\s*\$RepairGpuWsl\.IsPresent') {
    throw 'Native installer does not pass the switch to Phase 05'
}
$tokens = $null; $errors = $null
$phaseAst = [Management.Automation.Language.Parser]::ParseFile($phasePath, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
$gpuBranch = $phaseAst.Find({
    param($node)
    $node -is [Management.Automation.Language.IfStatementAst] -and
        $node.Clauses[0].Item1.Extent.Text -match 'WSL2Backend'
}, $true)
if (-not $gpuBranch) { throw 'NVIDIA GPU probe branch not found' }
$probe = [scriptblock]::Create($gpuBranch.Extent.Text)

function Write-AI { param($Message) }
function Write-AIWarn { param($Message) }
function Write-AISuccess { param($Message) }
function Start-Sleep { param($Seconds) }
function docker {
    $script:dockerCalls++
    if ($script:dockerExitCodes.Count -lt $script:dockerCalls) { throw 'Unexpected Docker retry' }
    $global:LASTEXITCODE = $script:dockerExitCodes[$script:dockerCalls - 1]
}
function wsl {
    $script:wslCalls += ,([string]::Join(' ', [string[]]$args))
    if ($args[0] -eq 'wslpath') { return '/tmp/ods-nvidia-toolkit-install.sh' }
}

$gpuInfo = [pscustomobject]@{ Backend = 'nvidia' }
$preflight_docker = [pscustomobject]@{ WSL2Backend = $true }
$oldTemp = $env:TEMP
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-gpu-opt-in-' + [Guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $fixture -Force | Out-Null
    $env:TEMP = $fixture

    $repairGpuWslFlag = $false
    $script:dockerExitCodes = @(1)
    $script:dockerCalls = 0
    $script:wslCalls = @()
    . $probe
    if (-not $script:gpuPassthroughFailed -or $script:dockerCalls -ne 1 -or $script:wslCalls.Count -ne 0) {
        throw 'Default GPU failure did not fall back without touching WSL'
    }
    if (Test-Path (Join-Path $fixture 'ods-nvidia-toolkit-install.sh')) {
        throw 'Default GPU failure wrote a toolkit installer'
    }

    $repairGpuWslFlag = $false
    $script:dockerExitCodes = @(0)
    $script:dockerCalls = 0
    $script:wslCalls = @()
    . $probe
    if ($script:gpuPassthroughFailed -or $script:dockerCalls -ne 1 -or $script:wslCalls.Count -ne 0) {
        throw 'Successful GPU probe changed behavior'
    }

    $repairGpuWslFlag = $true
    $script:dockerExitCodes = @(1, 1, 1)
    $script:dockerCalls = 0
    $script:wslCalls = @()
    . $probe
    if (-not $script:gpuPassthroughFailed -or $script:dockerCalls -ne 3) {
        throw 'Opt-in GPU recovery did not reach final CPU fallback'
    }
    if (@($script:wslCalls | Where-Object { $_ -eq '--shutdown' }).Count -ne 2 -or
        @($script:wslCalls | Where-Object { $_ -like 'bash *' }).Count -ne 1) {
        throw 'Opt-in GPU recovery did not run its WSL repair ladder'
    }
    if (Test-Path (Join-Path $fixture 'ods-nvidia-toolkit-install.sh')) {
        throw 'Opt-in GPU recovery left a temporary toolkit script'
    }
    Write-Host '[PASS] Windows GPU repair leaves WSL untouched by default and runs only with explicit opt-in'
} finally {
    $env:TEMP = $oldTemp
    $absolute = [IO.Path]::GetFullPath($fixture)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $absolute.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture cleanup path' }
    Remove-Item -LiteralPath $absolute -Recurse -Force -ErrorAction SilentlyContinue
}
