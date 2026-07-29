$ErrorActionPreference = "Stop"

$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$installerPath = Join-Path $root "installers\windows\install-windows.ps1"
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $installerPath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {
    throw "Windows installer failed to parse: $($errors[0].Message)"
}

$functionAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq "Invoke-ODSWindowsDockerPullWithRetry"
}, $true)
if (-not $functionAst) {
    throw "Invoke-ODSWindowsDockerPullWithRetry was not found"
}
. ([scriptblock]::Create($functionAst.Extent.Text))

foreach ($functionName in @(
    "Invoke-ODSWindowsComposeBuildService",
    "Invoke-ODSWindowsPlainDockerBuildService",
    "Get-ODSWindowsComposeExternalImages",
    "Invoke-ODSWindowsDockerPullWithRetry",
    "Invoke-ODSWindowsComposeImagePreflight"
)) {
    $dockerFunction = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $functionName
    }, $true)
    if (-not $dockerFunction) { throw "Function not found: $functionName" }
    $dockerArgsParameter = $dockerFunction.Body.ParamBlock.Parameters | Where-Object {
        $_.Name.VariablePath.UserPath -eq "DockerClientArgs"
    }
    $allowsEmpty = @($dockerArgsParameter.Attributes | Where-Object {
        $_.TypeName.Name -eq "AllowEmptyCollection"
    }).Count -eq 1
    if (-not $allowsEmpty) {
        throw "$functionName rejects the empty Docker argument list used by the default user config"
    }
}

function Write-AI { param([string]$Message) Write-Host $Message }
function Write-AISuccess { param([string]$Message) Write-Host $Message }
function Write-AIWarn { param([string]$Message) Write-Host $Message }
function Write-AIError { param([string]$Message) Write-Host $Message }
function Start-Sleep {
    param([int]$Seconds)
    $script:sleepDelays += $Seconds
}

$script:inspectExitCode = 1
$script:pullExitCodes = @()
$script:pullAttempts = 0
$script:emitPullError = $false
$script:sleepDelays = @()
function docker {
    $commandLine = $args -join " "
    if ($commandLine -match "image inspect") {
        $global:LASTEXITCODE = $script:inspectExitCode
        return
    }
    if ($commandLine -match "pull") {
        $index = $script:pullAttempts
        $script:pullAttempts++
        if ($script:emitPullError) {
            Write-Error "registry unavailable"
        }
        Write-Output "layer-$($script:pullAttempts): downloading"
        Write-Output "layer-$($script:pullAttempts): complete"
        $global:LASTEXITCODE = [int]$script:pullExitCodes[$index]
        return
    }
    throw "Unexpected docker invocation: $commandLine"
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$tempDir = Join-Path ([IO.Path]::GetTempPath()) "ods-docker-pull-$([Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $tempDir | Out-Null
try {
    $logPath = Join-Path $tempDir "compose progress.log"

    $script:inspectExitCode = 1
    $script:pullExitCodes = @(0)
    $script:pullAttempts = 0
    $script:emitPullError = $false
    $captured = @(& {
        Invoke-ODSWindowsDockerPullWithRetry `
            -Image "registry.example/large:latest" `
            -DockerClientArgs @("--config", "mock") -LogPath $logPath -MaxAttempts 1
    } 6>&1)
    $result = @($captured | Where-Object { $_ -is [bool] })
    Assert-True ($result.Count -eq 1 -and $result[0]) `
        "Successful pull did not return exactly one true result"
    Assert-True ($script:pullAttempts -eq 1) "Successful pull attempt count changed"
    $visible = ($captured | ForEach-Object { [string]$_ }) -join "`n"
    Assert-True ($visible -match "layer-1: downloading") `
        "Docker progress was not emitted to the terminal stream"
    $logText = Get-Content -LiteralPath $logPath -Raw
    Assert-True ($logText -match "layer-1: downloading" -and $logText -match "layer-1: complete") `
        "Docker progress was not appended to the compose log"
    Assert-True ($ErrorActionPreference -eq "Stop") `
        "Pull helper did not restore ErrorActionPreference after success"

    Remove-Item -LiteralPath $logPath -Force
    $script:inspectExitCode = 1
    $script:pullExitCodes = @(0)
    $script:pullAttempts = 0
    $captured = @(& {
        Invoke-ODSWindowsDockerPullWithRetry `
            -Image "registry.example/default-config:latest" `
            -DockerClientArgs @() -LogPath $logPath -MaxAttempts 1
    } 6>&1)
    $result = @($captured | Where-Object { $_ -is [bool] })
    Assert-True ($result.Count -eq 1 -and $result[0]) `
        "Pull helper rejected the empty Docker argument list used by the default user config"
    Assert-True ($script:pullAttempts -eq 1) `
        "Default Docker config did not execute exactly one pull"

    Remove-Item -LiteralPath $logPath -Force
    $script:inspectExitCode = 1
    $script:pullExitCodes = @(1, 0)
    $script:pullAttempts = 0
    $script:sleepDelays = @()
    $captured = @(& {
        Invoke-ODSWindowsDockerPullWithRetry `
            -Image "registry.example/transient:latest" `
            -DockerClientArgs @() -LogPath $logPath -MaxAttempts 2
    } 6>&1)
    $result = @($captured | Where-Object { $_ -is [bool] })
    Assert-True ($result.Count -eq 1 -and $result[0]) `
        "Transient pull failure did not recover on retry"
    Assert-True ($script:pullAttempts -eq 2) `
        "Transient pull did not execute exactly two attempts"
    Assert-True ($script:sleepDelays.Count -eq 1 -and $script:sleepDelays[0] -eq 5) `
        "Transient pull did not retain the first retry delay"
    $retryLog = Get-Content -LiteralPath $logPath -Raw
    Assert-True ($retryLog -match "layer-1: downloading" -and $retryLog -match "layer-2: complete") `
        "Transient pull did not retain output from both attempts"

    Remove-Item -LiteralPath $logPath -Force
    $script:inspectExitCode = 1
    $script:pullExitCodes = @(1)
    $script:pullAttempts = 0
    $script:emitPullError = $true
    $captured = @(& {
        Invoke-ODSWindowsDockerPullWithRetry `
            -Image "registry.example/missing:latest" `
            -DockerClientArgs @("--config", "mock") -LogPath $logPath -MaxAttempts 1
    } 6>&1)
    $result = @($captured | Where-Object { $_ -is [bool] })
    Assert-True ($result.Count -eq 1 -and -not $result[0]) `
        "Failed pull did not return exactly one false result"
    Assert-True ($script:pullAttempts -eq 1) "Failed pull attempt count changed"
    $failureLog = Get-Content -LiteralPath $logPath -Raw
    Assert-True ($failureLog -match "layer-1: downloading" -and $failureLog -match "registry unavailable") `
        "Failed pull stdout/stderr was not retained in the compose log"
    Assert-True ($ErrorActionPreference -eq "Stop") `
        "Pull helper did not restore ErrorActionPreference after failure"

    $badLogPath = Join-Path $tempDir "log-target-directory"
    New-Item -ItemType Directory -Path $badLogPath | Out-Null
    $script:inspectExitCode = 1
    $script:pullExitCodes = @(0)
    $script:pullAttempts = 0
    $script:emitPullError = $false
    $logFailure = Invoke-ODSWindowsDockerPullWithRetry `
        -Image "registry.example/log-failure:latest" `
        -DockerClientArgs @("--config", "mock") -LogPath $badLogPath -MaxAttempts 1
    Assert-True ($logFailure -eq $false) "Pull helper ignored a compose-log write failure"
    Assert-True ($ErrorActionPreference -eq "Stop") `
        "Pull helper did not restore ErrorActionPreference after a log failure"

    Remove-Item -LiteralPath $logPath -Force
    $script:inspectExitCode = 0
    $script:pullExitCodes = @()
    $script:pullAttempts = 0
    $script:emitPullError = $false
    $cached = Invoke-ODSWindowsDockerPullWithRetry `
        -Image "registry.example/cached:latest" `
        -DockerClientArgs @("--config", "mock") -LogPath $logPath -MaxAttempts 1
    Assert-True ($cached -eq $true) "Cached image no longer skips the pull"
    Assert-True ($script:pullAttempts -eq 0) "Cached image unexpectedly invoked docker pull"
    Assert-True ((Get-Content -LiteralPath $logPath -Raw) -match "Compose image already cached") `
        "Cached-image receipt was not written"
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[PASS] Windows Docker pull progress and result contract"
