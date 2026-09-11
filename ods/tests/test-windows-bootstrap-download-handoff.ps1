$ErrorActionPreference = 'Stop'

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$uiPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'installers\windows\lib\ui.ps1'
. $uiPath

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ods-handoff-{0}" -f [guid]::NewGuid().ToString('N'))
$wrapperPath = Join-Path $testRoot 'logs\bootstrap-run.sh'
$destination = Join-Path $testRoot 'data\models\Full.gguf'
$modelFile = 'Full.gguf'

try {
    New-Item -ItemType Directory -Path (Split-Path -Parent $wrapperPath), (Split-Path -Parent $destination) -Force | Out-Null
    [System.IO.File]::WriteAllText($wrapperPath, "exec bash bootstrap-upgrade.sh /c/test $modelFile")

    $script:mockTask = [pscustomobject]@{
        State = 'Running'
        Actions = @([pscustomobject]@{ Arguments = ('"{0}"' -f $wrapperPath) })
    }
    $script:mockProcesses = @()
    function Get-ScheduledTask { return $script:mockTask }
    function Get-CimInstance { return $script:mockProcesses }

    Assert-True (Test-ODSBootstrapUpgradeActive -InstallDir $testRoot -ModelFile $modelFile) `
        'matching scheduled task was not detected'
    Assert-True (-not (Test-ODSBootstrapUpgradeActive -InstallDir $testRoot -ModelFile 'Other.gguf')) `
        'scheduled task for a different model caused a false positive'

    $otherRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ods-other-{0}" -f [guid]::NewGuid().ToString('N'))
    Assert-True (-not (Test-ODSBootstrapUpgradeActive -InstallDir $otherRoot -ModelFile $modelFile)) `
        'scheduled task for a different install caused a false positive'

    $script:mockTask = $null
    function Get-ScheduledTask { throw 'no scheduled task' }
    $normalized = ($testRoot -replace '\\', '/').ToLowerInvariant()
    if ($normalized -match '^([a-z]):/(.*)$') {
        $normalized = "/$($Matches[1])/$($Matches[2])"
    }
    $script:mockProcesses = @([pscustomobject]@{
        CommandLine = "bash bootstrap-upgrade.sh $normalized $modelFile"
    })
    Assert-True (Test-ODSBootstrapUpgradeActive -InstallDir $testRoot -ModelFile $modelFile) `
        'Git Bash direct-launch process was not detected'

    $script:activeChecks = 0
    function Test-ODSBootstrapUpgradeActive {
        $script:activeChecks++
        if ($script:activeChecks -eq 1) { return $true }
        [System.IO.File]::WriteAllText($destination, 'complete')
        return $false
    }
    function Start-Sleep { }

    $result = Wait-ODSBootstrapDownloadHandoff `
        -InstallDir $testRoot -ModelFile $modelFile -Destination $destination -WaitSeconds 5
    Assert-True ($result.WasActive -and $result.Completed -and -not $result.TimedOut) `
        'handoff did not wait for and reuse the completed background download'

    Write-Output 'PASS: Windows bootstrap handoff runtime behavior'
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
