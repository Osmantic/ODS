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

$functionAst = $ast.Find(
    {
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq "Stop-ODSWindowsLemonadeProcesses"
    },
    $true
)
if (-not $functionAst) {
    throw "Stop-ODSWindowsLemonadeProcesses was not found"
}

. ([scriptblock]::Create($functionAst.Extent.Text))

$script:LEMONADE_PORT = 9000
$script:StoppedTasks = @()
$script:UnregisteredTasks = @()

function Get-ScheduledTask {
    param($ErrorAction)

    @(
        [pscustomobject]@{
            TaskName = "PriorManagedRuntime"
            Actions = @(
                [pscustomobject]@{
                    Execute = '"/opt/lemonade/LemonadeServer.exe"'
                    Arguments = "serve --port 9000 --host 127.0.0.1"
                }
            )
        },
        [pscustomobject]@{
            TaskName = "PriorManagedRuntimeEquals"
            Actions = @(
                [pscustomobject]@{
                    Execute = "/another/location/lemonade-server.exe"
                    Arguments = "serve --port=9000 --host 127.0.0.1"
                }
            )
        },
        [pscustomobject]@{
            TaskName = "UnrelatedRuntime"
            Actions = @(
                [pscustomobject]@{
                    Execute = "/opt/lemonade/LemonadeServer.exe"
                    Arguments = "serve --port 9001 --host 127.0.0.1"
                }
            )
        }
    )
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
    -TaskNames @("ODSLemonadeRuntime")

$expectedTasks = @(
    "ODSLemonadeRuntime",
    "PriorManagedRuntime",
    "PriorManagedRuntimeEquals"
) | Sort-Object
$stoppedDifference = Compare-Object $expectedTasks ($script:StoppedTasks | Sort-Object)
$unregisteredDifference = Compare-Object $expectedTasks ($script:UnregisteredTasks | Sort-Object)

if ($stoppedDifference -or $unregisteredDifference) {
    throw "Managed task cleanup mismatch: stopped=$($script:StoppedTasks -join ',') unregistered=$($script:UnregisteredTasks -join ',')"
}
if ($script:StoppedTasks -contains "UnrelatedRuntime" -or
    $script:UnregisteredTasks -contains "UnrelatedRuntime") {
    throw "Cleanup removed an unrelated Lemonade task"
}

Write-Host "[PASS] Windows Lemonade task cleanup discovers managed runtime tasks"
