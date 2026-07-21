$ErrorActionPreference = "Stop"

$installerPath = Join-Path $PSScriptRoot "../../installers/windows/install-windows.ps1"
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path $installerPath),
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {
    throw $parseErrors[0]
}

$nameFunctionAst = $ast.Find(
    {
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq "Get-ODSPriorLemonadeTaskName"
    },
    $true
)
$cleanupFunctionAst = $ast.Find(
    {
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq "Stop-ODSWindowsLemonadeProcesses"
    },
    $true
)
if (-not $nameFunctionAst -or -not $cleanupFunctionAst) {
    throw "Windows Lemonade compatibility functions were not found"
}

. ([scriptblock]::Create($nameFunctionAst.Extent.Text))
. ([scriptblock]::Create($cleanupFunctionAst.Extent.Text))

$priorTaskName = Get-ODSPriorLemonadeTaskName
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $priorTaskHashBytes = $sha256.ComputeHash(
        [System.Text.Encoding]::UTF8.GetBytes($priorTaskName)
    )
} finally {
    $sha256.Dispose()
}
$priorTaskHash = -join ($priorTaskHashBytes | ForEach-Object { $_.ToString("x2") })
if ($priorTaskHash -ne "8e25972bf57578b932c392251072daa312eff439c21bd09cdc0791e6c3cdeb57") {
    throw "Prior managed task-name fingerprint mismatch"
}

$script:LEMONADE_PORT = 9000
$script:StoppedTasks = @()
$script:UnregisteredTasks = @()

function Get-ScheduledTask {
    param($ErrorAction)
    throw "Cleanup must not enumerate unrelated scheduled tasks"
}

function Stop-ScheduledTask {
    param(
        [string]$TaskName,
        $ErrorAction
    )
    $script:StoppedTasks += $TaskName
}

function Unregister-ScheduledTask {
    param(
        [string]$TaskName,
        [switch]$Confirm,
        $ErrorAction
    )
    $script:UnregisteredTasks += $TaskName
}

function Get-CimInstance {
    param(
        $ClassName,
        $ErrorAction
    )
    @()
}

function Stop-Process {
    param(
        $Id,
        [switch]$Force,
        $ErrorAction
    )
}

function Write-AIWarn {
    param([string]$Message)
}

Stop-ODSWindowsLemonadeProcesses `
    -ExePath "/opt/lemonade/LemonadeServer.exe" `
    -TaskNames @("ODSLemonadeRuntime", $priorTaskName)

$expectedTasks = @(
    "ODSLemonadeRuntime",
    $priorTaskName
) | Sort-Object
$stoppedDifference = Compare-Object $expectedTasks ($script:StoppedTasks | Sort-Object)
$unregisteredDifference = Compare-Object $expectedTasks ($script:UnregisteredTasks | Sort-Object)

if ($stoppedDifference -or $unregisteredDifference) {
    throw "Managed task cleanup mismatch: stopped=$($script:StoppedTasks -join ',') unregistered=$($script:UnregisteredTasks -join ',')"
}
Write-Host "[PASS] Windows Lemonade task cleanup removes only exact managed task names"
