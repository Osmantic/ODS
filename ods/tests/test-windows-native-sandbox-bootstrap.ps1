# SPDX-License-Identifier: Apache-2.0
# Execute the actual guest preflight in isolated children. Never run Setup,
# a compiler, a publisher bootstrapper, or change host inventory/settings.
[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$source=Join-Path $PSScriptRoot '../desktop/windows/qa/run-sandbox-qa.ps1'
$original=[IO.File]::ReadAllText($source)
$sourceHash=(Get-FileHash -LiteralPath $source).Hash
$inputLiteral="'C:\ODS-QA-Input'"
$outputLiteral="'C:\ODS-QA-Output'"
foreach($literal in @($inputLiteral,$outputLiteral)){
    if(($original.Split(@($literal),[StringSplitOptions]::None).Count-1) -ne 1){throw 'Review the actual Sandbox mapping substitution boundary.'}
}
$mutantBoundary='        $record.optional_features_status=''unavailable'''
if(($original.Split(@($mutantBoundary),[StringSplitOptions]::None).Count-1) -ne 1){throw 'Review the optional-inventory negative-control boundary.'}
$interpreter=(Get-Process -Id $PID).Path
if([IO.Path]::GetFileNameWithoutExtension($interpreter) -notin @('powershell','pwsh')){throw 'Run this contract in an actual Windows PowerShell or PowerShell 7 interpreter.'}
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('ods-sandbox-bootstrap-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $fixture
$checks=0
function Assert-SandboxBootstrap([bool]$Condition,[string]$Message){
    if(-not $Condition){throw $Message}
    $script:checks++;Write-Output ('PASS '+$Message)
}
function ConvertTo-ScriptLiteral([string]$Value){return "'"+$Value.Replace("'","''")+"'"}
function Test-OptionalInventoryRecovery($Record){
    return $Record.status -eq 'failed' -and $Record.phase -eq 'preflight' -and
        $Record.optional_features_status -eq 'unavailable' -and
        $Record.optional_features_error.message -eq 'fixture optional inventory disabled' -and
        $Record.optional_features_error.hresult -eq '0x80070422' -and
        $Record.error.Contains('Mapped installer failed verification.')
}
function Invoke-SandboxBootstrapCase([string]$Name){
    $directory=Join-Path $fixture $Name
    $inputDirectory=Join-Path $directory 'input'
    $outputDirectory=Join-Path $directory 'results'
    $privateWindows=Join-Path $directory 'windows'
    $privateLocal=Join-Path $directory 'local'
    $privateTemp=Join-Path $directory 'temp'
    foreach($path in @($inputDirectory,$outputDirectory,$privateWindows,$privateLocal,$privateTemp)){
        $null=New-Item -ItemType Directory -Path $path
    }
    $sentinel=Join-Path $inputDirectory 'preserve-input.txt'
    [IO.File]::WriteAllText($sentinel,'Preserve mapped input and saved data.',[Text.UTF8Encoding]::new($false))
    $setup=Join-Path $inputDirectory 'ODS-Windows-Preview-Setup.exe'
    [IO.File]::WriteAllText($setup,'Deliberately invalid PE. This fixture must never execute.',[Text.UTF8Encoding]::new($false))
    $setupHash=(Get-FileHash -LiteralPath $setup).Hash.ToLowerInvariant()
    $receiptHash=if($Name -eq 'developer-git'){$setupHash}else{[string]::new('0',64)}
    [IO.File]::WriteAllText((Join-Path $inputDirectory 'INSTALLER-BUILD-INFO.json'),
        (@{installer_sha256=$receiptHash}|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    $copy=$original.Replace($inputLiteral,(ConvertTo-ScriptLiteral $inputDirectory)).Replace($outputLiteral,(ConvertTo-ScriptLiteral $outputDirectory))
    if($Name -eq 'fatal-optional-mutant'){$copy=$copy.Replace($mutantBoundary,('        throw'+[Environment]::NewLine+$mutantBoundary))}
    $guestScript=Join-Path $inputDirectory 'run-sandbox-qa.ps1'
    [IO.File]::WriteAllText($guestScript,$copy,[Text.UTF8Encoding]::new($false))
    $before=@{}
    Get-ChildItem -LiteralPath $inputDirectory -Recurse -File | ForEach-Object {$before[$_.FullName]=(Get-FileHash -LiteralPath $_.FullName).Hash}
    $prefix=@'
$ErrorActionPreference='Stop'
function Get-CimInstance {
    [CmdletBinding()]
    param([Parameter(Position=0)][string]$ClassName,[int]$OperationTimeoutSec)
    if($OperationTimeoutSec -ne 15){throw 'Unexpected fixture inventory timeout contract.'}
    switch($ClassName){
        'Win32_ComputerSystem' {
            return [pscustomobject]@{
                Manufacturer=$(if($fixtureCase -eq 'wrong-manufacturer'){'Fixture Corporation'}else{'Microsoft Corporation'})
                Model='Virtual Machine'
            }
        }
        'Win32_OperatingSystem' {
            if($fixtureCase -eq 'os-failure'){throw 'fixture operating system query failed'}
            return [pscustomobject]@{Caption='Private Windows fixture';BuildNumber='19045';OSArchitecture='64-bit'}
        }
        'Win32_OptionalFeature' {
            if($fixtureCase -eq 'optional-unavailable' -or $fixtureCase -eq 'fatal-optional-mutant'){
                throw [Runtime.InteropServices.COMException]::new('fixture optional inventory disabled',-2147023838)
            }
            return @([pscustomobject]@{Name='VirtualMachinePlatform';InstallState=2},
                [pscustomobject]@{Name='Microsoft-Hyper-V';InstallState=1},
                [pscustomobject]@{Name='UnrelatedFixtureFeature';InstallState=1})
        }
        default {throw 'Unexpected host inventory lookup.'}
    }
}
function Get-Command {
    [CmdletBinding()]
    param([Parameter(Position=0)][string]$Name)
    if($Name -notin @('git','node','python','docker','wsl')){throw 'Unexpected developer command lookup.'}
    if($fixtureCase -eq 'developer-git' -and $Name -eq 'git'){return [pscustomobject]@{Name='git';Source='private fixture only'}}
}
'@
    $runner=Join-Path $directory 'runner.ps1'
    [IO.File]::WriteAllText($runner,('$fixtureCase='+(ConvertTo-ScriptLiteral $Name)+[Environment]::NewLine+$prefix+
        [Environment]::NewLine+'. '+(ConvertTo-ScriptLiteral $guestScript)),[Text.UTF8Encoding]::new($false))
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName=$interpreter
    $start.Arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$runner+'"'
    $start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WorkingDirectory=$directory
    $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
    $start.EnvironmentVariables['USERNAME']=$(if($Name -eq 'wrong-user'){'FixtureDeveloper'}else{'WDAGUtilityAccount'})
    $start.EnvironmentVariables['WINDIR']=$privateWindows
    $start.EnvironmentVariables['LOCALAPPDATA']=$privateLocal
    $start.EnvironmentVariables['TEMP']=$privateTemp;$start.EnvironmentVariables['TMP']=$privateTemp
    $child=[Diagnostics.Process]::new();$child.StartInfo=$start
    try {
        if(-not $child.Start()){throw 'The isolated Sandbox fixture process did not start.'}
        $stdout=$child.StandardOutput.ReadToEndAsync();$stderr=$child.StandardError.ReadToEndAsync()
        if(-not $child.WaitForExit(20000)){
            # Only this retained child created by this test can be terminated.
            $child.Kill();$null=$child.WaitForExit(5000)
            throw "Sandbox fixture $Name exceeded its 20-second deadline."
        }
        if(-not [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($stdout,$stderr),5000)){throw 'The isolated Sandbox fixture output did not drain.'}
        $console=$stdout.GetAwaiter().GetResult()+$stderr.GetAwaiter().GetResult()
        $exitCode=$child.ExitCode
    } finally {$child.Dispose()}
    [IO.File]::WriteAllText((Join-Path $directory 'console.log'),$console,[Text.UTF8Encoding]::new($false))
    if($exitCode -eq 0){throw "Sandbox fixture $Name unexpectedly succeeded. $console"}
    $after=@(Get-ChildItem -LiteralPath $inputDirectory -Recurse -File)
    if($after.Count -ne $before.Count){throw 'The mapped input file census changed.'}
    foreach($path in $before.Keys){if((Get-FileHash -LiteralPath $path).Hash -cne $before[$path]){throw 'The mapped input bytes changed.'}}
    foreach($path in @($privateWindows,$privateLocal,$privateTemp)){
        if(@(Get-ChildItem -LiteralPath $path -Recurse -Force).Count){throw "Sandbox fixture $Name reached compilation/installation or wrote private environment state."}
    }
    $resultPath=Join-Path $outputDirectory 'RESULTS.json'
    $logPath=Join-Path $outputDirectory 'guest.log'
    if($Name -in @('wrong-user','wrong-manufacturer')){
        Assert-SandboxBootstrap ($console.Contains('Refusing to run outside the expected Microsoft Windows Sandbox guest') -and
            -not (Test-Path -LiteralPath $resultPath) -and -not (Test-Path -LiteralPath $logPath) -and
            @(Get-ChildItem -LiteralPath $outputDirectory -Force).Count -eq 0) ($Name+' refuses before transcript/results and preserves private input')
        return
    }
    if(-not (Test-Path -LiteralPath $resultPath) -or -not (Test-Path -LiteralPath $logPath)){throw "Sandbox fixture $Name lost its failed result or transcript. $console"}
    $record=Get-Content -Raw -LiteralPath $resultPath | ConvertFrom-Json
    $log=[IO.File]::ReadAllText($logPath)
    if($record.status -ne 'failed' -or $record.phase -ne 'preflight' -or -not $record.finished_utc -or
        -not $log.Contains('QA_PHASE=preflight') -or $console.Contains('QA_PHASE=setup')){throw "Sandbox fixture $Name reached an unexpected phase or lost diagnostic custody."}
    switch($Name){
        'optional-unavailable' {
            Assert-SandboxBootstrap ((Test-OptionalInventoryRecovery $record) -and
                $log.Contains('Optional feature inventory unavailable; continuing the native application checks.')) 'optional inventory failure records unavailable/error and reaches the actual checksum refusal without compiling or installing'
        }
        'available' {
            Assert-SandboxBootstrap ($record.optional_features_status -eq 'available' -and $null -eq $record.optional_features_error -and
                @($record.optional_features).Count -eq 2 -and $record.optional_features[0].Name -eq 'VirtualMachinePlatform' -and
                $record.optional_features[0].InstallState -eq 2 -and $record.optional_features[1].Name -eq 'Microsoft-Hyper-V' -and
                $record.error.Contains('Mapped installer failed verification.')) 'available optional inventory retains filtered feature rows before the actual checksum refusal'
        }
        'os-failure' {
            Assert-SandboxBootstrap ($record.error.Contains('fixture operating system query failed') -and
                $record.optional_features_status -eq 'not_run' -and $null -eq $record.os) 'operating system inventory failure remains fatal with a failed result and transcript'
        }
        'developer-git' {
            Assert-SandboxBootstrap ($record.installer_sha256 -eq $setupHash -and $record.developer_commands.git -eq $true -and
                $record.error.Contains('This guest already contains unexpected developer tools')) 'a developer tool with a valid dummy hash refuses before the compiler or installer'
        }
        'fatal-optional-mutant' {
            Assert-SandboxBootstrap (-not (Test-OptionalInventoryRecovery $record) -and $record.optional_features_status -eq 'not_run' -and
                $record.error.Contains('fixture optional inventory disabled') -and -not $record.error.Contains('Mapped installer failed verification.')) 'private catch-to-throw mutant fails the optional-inventory continuation contract'
        }
    }
}
try {
    foreach($name in @('optional-unavailable','available','os-failure','wrong-user','wrong-manufacturer','developer-git','fatal-optional-mutant')){
        Invoke-SandboxBootstrapCase $name
    }
    if((Get-FileHash -LiteralPath $source).Hash -cne $sourceHash){throw 'The actual guest source changed during its isolated contract.'}
    Write-Output "PASS $checks actual Sandbox bootstrap cases on PowerShell $($PSVersionTable.PSVersion)"
} finally {
    if([IO.Path]::GetDirectoryName($fixture) -ne [IO.Path]::GetTempPath().TrimEnd('\')){throw 'Unexpected Sandbox bootstrap fixture path.'}
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
