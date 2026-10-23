# SPDX-License-Identifier: Apache-2.0
# Explicit designated disposable VM only. Default invocation writes nothing.
[CmdletBinding()]
param([switch]$RunQualification,[switch]$PreflightOnly)
$ErrorActionPreference='Stop'
if($RunQualification -and $PreflightOnly){throw 'Choose preflight or qualification, not both.'}
. (Join-Path $PSScriptRoot 'clean-user-preflight.ps1')
$policy=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'QA-SOURCE.json')) | ConvertFrom-Json
$machine=Get-CleanUserMachine
Assert-CleanUserMachine $policy $machine
Assert-CleanUserInput $PSScriptRoot $policy
$installed=Join-Path $env:LOCALAPPDATA 'Programs/ODS Windows Preview'
$state=Join-Path $env:LOCALAPPDATA 'ODS Guest QA'
foreach($path in @($installed,$state,(Join-Path $env:LOCALAPPDATA 'ODS Desktop Preview'))){
    if(Test-Path -LiteralPath $path){throw 'Use a fresh disposable profile; ODS application/state already exists.'}
}
foreach($name in @('git','node','python','docker')){if(Get-Command $name -ErrorAction SilentlyContinue){throw 'Use a fresh VM without developer tools.'}}
if(-not $RunQualification){Write-Output ($machine | ConvertTo-Json);Write-Output 'PASS read-only standard-user VM preflight; use -RunQualification only in the designated disposable VM.';return}
# All identity, token, fresh-profile and frozen-input guards precede any write.
$output=Join-Path $env:LOCALAPPDATA ('ODS Clean User QA/'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $output
Write-Output ('QA_OUTPUT='+$output)
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=[Security.Principal.WindowsPrincipal]::new($identity)
$record=@{schemaVersion=1;status='running';phase='preflight';started_utc=[DateTime]::UtcNow.ToString('o');os=$null;build=$null;
    architecture=$null;account=$identity.Name;elevated=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);
    developer_commands=@{};optional_features=@();optional_features_status='not_run';optional_features_error=$null;
    scope='Designated standard-user VM install, real Portal CPU chat and document save';machine=$machine;source_head=$policy.source_head}
$app=$null;$transcriptStarted=$false
try {
    Start-Transcript -Path (Join-Path $output 'guest.log') -NoClobber
    $transcriptStarted=$true
    Write-Output 'QA_PHASE=preflight'
    $os=Get-CimInstance Win32_OperatingSystem -OperationTimeoutSec 15
    $record.os=$os.Caption;$record.build=$os.BuildNumber;$record.architecture=$os.OSArchitecture
    foreach($name in @('git','node','python','docker','wsl')){$record.developer_commands[$name]=[bool](Get-Command $name -ErrorAction SilentlyContinue)}
    try {
        $record.optional_features=@(Get-CimInstance Win32_OptionalFeature -OperationTimeoutSec 15 | Where-Object Name -Match 'Subsystem-Linux|VirtualMachinePlatform|Hyper-V' | Select-Object Name,InstallState)
        $record.optional_features_status='available'
    } catch {
        # A VM may disable the service used by this diagnostic provider
        # (observed 0x80070422). Missing inventory is not an installer failure.
        $record.optional_features_status='unavailable'
        $record.optional_features_error=@{type=$_.Exception.GetType().FullName;message=$_.Exception.Message;hresult=('0x{0:X8}' -f $_.Exception.HResult)}
        Write-Output 'Optional feature inventory unavailable; continuing the native application checks.'
    }
    $receipt=Get-Content -Raw (Join-Path $PSScriptRoot 'INSTALLER-BUILD-INFO.json') | ConvertFrom-Json
    $setup=Join-Path $PSScriptRoot 'ODS-Windows-Preview-Setup.exe'
    $record.installer_sha256=(Get-FileHash -LiteralPath $setup).Hash.ToLowerInvariant()
    if($record.installer_sha256 -ne $receipt.installer_sha256){throw 'Mapped installer failed verification.'}
    if($record.developer_commands.docker -or $record.developer_commands.node -or $record.developer_commands.git){throw 'This guest already contains unexpected developer tools; use a fresh VM.'}
    $record.phase='setup';Write-Output 'QA_PHASE=setup'
    $compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    $wpf=Join-Path (Split-Path $compiler) 'WPF'
    $runner=Join-Path $output 'InstallerUiTests.exe'
    $compilerSource=Join-Path $PSScriptRoot 'ods/desktop/windows/tests/InstallerUiTests.cs'
    $compilerArguments=@('/nologo','/utf8output','/target:exe','/platform:x64',"/out:$runner",'/reference:System.Management.dll',
        "/reference:$wpf/WindowsBase.dll","/reference:$wpf/UIAutomationClient.dll","/reference:$wpf/UIAutomationTypes.dll",$compilerSource)
    $compilerStart=[Diagnostics.ProcessStartInfo]::new()
    $compilerStart.FileName=$compiler;$compilerStart.UseShellExecute=$false;$compilerStart.CreateNoWindow=$true
    $compilerStart.Arguments=($compilerArguments | ForEach-Object {
        '"'+[regex]::Replace([regex]::Replace($_,'(\\*)"','$1$1\"'),'(\\+)$','$1$1')+'"'
    }) -join ' '
    $compilerStart.RedirectStandardOutput=$true;$compilerStart.RedirectStandardError=$true
    $compilerEncoding=[Text.UTF8Encoding]::new($false)
    $compilerStart.StandardOutputEncoding=$compilerEncoding;$compilerStart.StandardErrorEncoding=$compilerEncoding
    $compile=[Diagnostics.Process]::new();$compile.StartInfo=$compilerStart
    $record.compiler=@{executable=$compiler;arguments=$compilerArguments;source=$compilerSource;
        source_sha256=(Get-FileHash -LiteralPath $compilerSource).Hash.ToLowerInvariant();log='compiler.log';exit_code=$null}
    try {
        if(-not $compile.Start()){throw 'Guest inbox compiler did not start.'}
        $compilerOut=$compile.StandardOutput.ReadToEndAsync();$compilerError=$compile.StandardError.ReadToEndAsync()
        $compile.WaitForExit()
        $compilerStdout=$compilerOut.GetAwaiter().GetResult();$compilerStderr=$compilerError.GetAwaiter().GetResult()
        $record.compiler.exit_code=$compile.ExitCode
        [IO.File]::WriteAllText((Join-Path $output 'compiler.log'),("STDOUT:`r`n"+$compilerStdout+"`r`nSTDERR:`r`n"+$compilerStderr),$compilerEncoding)
    }finally{$compile.Dispose()}
    if($record.compiler.exit_code -ne 0){throw 'Guest UI harness did not compile using the inbox .NET compiler; see compiler.log and compiler receipt.'}
    $installed=Join-Path $env:LOCALAPPDATA 'Programs/ODS Windows Preview'
    if(Test-Path -LiteralPath $installed){throw 'Use a fresh guest; the application folder already exists.'}
    $installStarted=[DateTime]::UtcNow
    $launch=@(& $runner $setup $installed (Join-Path $output 'setup.log') --allow-prerequisite)
    if($LASTEXITCODE -ne 0){throw 'Graphical installer or Finish launch failed in the guest.'}
    $line=@($launch | Where-Object {$_ -match '^APPLICATION_PID=\d+$'})
    if($line.Count -ne 1){throw 'Missing exact installed application PID.'}
    $candidate=Get-Process -Id ([int]$line[0].Split('=')[1])
    try {
        $null=$candidate.Handle
        if($candidate.StartTime.ToUniversalTime() -lt $installStarted -or $candidate.MainModule.FileName -ne (Join-Path $installed 'ODS Windows Preview.exe') -or
            (Get-FileHash -LiteralPath $candidate.MainModule.FileName).Hash.ToLowerInvariant() -ne $receipt.application_sha256){throw 'Unexpected guest application identity.'}
        $app=$candidate;$candidate=$null
    } finally {if($candidate){$candidate.Dispose()}}
    if(-not $app.CloseMainWindow() -or -not $app.WaitForExit(10000)){throw 'Installed guest application did not close normally.'}
    $app.Dispose();$app=$null
    $record.phase='chat_model';Write-Output 'QA_PHASE=chat_model'
    & (Join-Path $PSScriptRoot 'ods/tests/test-windows-native-portal.ps1') -Bundle $installed -LiveState (Join-Path $env:LOCALAPPDATA 'ODS Guest QA') -Screenshot (Join-Path $output 'native-portal.png') -DiagnosticsDirectory (Join-Path $output 'chat-diagnostics')
    if($LASTEXITCODE -ne 0){throw 'Real model/Portal qualification failed in the guest.'}
    $record.text_model='passed'
    $record.phase='document_model';Write-Output 'QA_PHASE=document_model'
    & (Join-Path $PSScriptRoot 'ods/tests/test-windows-native-portal.ps1') -Bundle $installed -LiveState (Join-Path $env:LOCALAPPDATA 'ODS Guest QA') -DocumentLive -Screenshot (Join-Path $output 'native-document.png') -DiagnosticsDirectory (Join-Path $output 'document-diagnostics')
    if($LASTEXITCODE -ne 0){throw 'Real model/document/save qualification failed in the guest.'}
    $record.document_model='passed'
    $record.phase='complete'
    $record.status='passed'
} catch {
    $record.status='failed';$record.error=$_.Exception.ToString()
    throw
} finally {
    if($app){if(-not $app.HasExited){$null=$app.CloseMainWindow();$null=$app.WaitForExit(10000)};$app.Dispose()}
    $record.finished_utc=[DateTime]::UtcNow.ToString('o')
    $record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output 'RESULTS.json') -Encoding UTF8
    if($transcriptStarted){Stop-Transcript}
}
