# SPDX-License-Identifier: Apache-2.0
# Guest-only qualification. This script must never install into the host profile.
$ErrorActionPreference='Stop'
$machine=Get-CimInstance Win32_ComputerSystem
if($env:USERNAME -ne 'WDAGUtilityAccount' -or $machine.Manufacturer -ne 'Microsoft Corporation' -or $machine.Model -ne 'Virtual Machine' -or $PSScriptRoot -ne 'C:\ODS-QA-Input'){
    throw 'Refusing to run outside the expected Microsoft Windows Sandbox guest and dedicated mapping.'
}
$output='C:\ODS-QA-Output'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=[Security.Principal.WindowsPrincipal]::new($identity)
$os=Get-CimInstance Win32_OperatingSystem
$record=@{schemaVersion=1;status='running';started_utc=[DateTime]::UtcNow.ToString('o');os=$os.Caption;build=$os.BuildNumber;
    architecture=$os.OSArchitecture;account=$identity.Name;elevated=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);
    developer_commands=@{};optional_features=@();scope='Clean Sandbox install, real Portal CPU chat and document save; actual guest token recorded, not assumed standard-user'}
foreach($name in @('git','node','python','docker','wsl')){$record.developer_commands[$name]=[bool](Get-Command $name -ErrorAction SilentlyContinue)}
$record.optional_features=@(Get-CimInstance Win32_OptionalFeature | Where-Object Name -Match 'Subsystem-Linux|VirtualMachinePlatform|Hyper-V' | Select-Object Name,InstallState)
Start-Transcript -Path (Join-Path $output 'guest.log') -NoClobber
$app=$null
try {
    $receipt=Get-Content -Raw (Join-Path $PSScriptRoot 'INSTALLER-BUILD-INFO.json') | ConvertFrom-Json
    $setup=Join-Path $PSScriptRoot 'ODS-Windows-Preview-Setup.exe'
    $record.installer_sha256=(Get-FileHash -LiteralPath $setup).Hash.ToLowerInvariant()
    if($record.installer_sha256 -ne $receipt.installer_sha256){throw 'Mapped installer failed verification.'}
    if($record.developer_commands.docker -or $record.developer_commands.node -or $record.developer_commands.git){throw 'This guest already contains unexpected developer tools; use a fresh Sandbox.'}
    $compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    $wpf=Join-Path (Split-Path $compiler) 'WPF'
    $runner=Join-Path $env:TEMP 'InstallerUiTests.exe'
    & $compiler /nologo /target:exe /platform:x64 "/out:$runner" /reference:System.Management.dll "/reference:$wpf/WindowsBase.dll" "/reference:$wpf/UIAutomationClient.dll" "/reference:$wpf/UIAutomationTypes.dll" (Join-Path $PSScriptRoot 'ods/desktop/windows/tests/InstallerUiTests.cs')
    if($LASTEXITCODE -ne 0){throw 'Guest UI harness did not compile using the inbox .NET compiler.'}
    $installed=Join-Path $env:LOCALAPPDATA 'Programs/ODS Windows Preview'
    if(Test-Path -LiteralPath $installed){throw 'Use a fresh guest; the application folder already exists.'}
    $launch=@(& $runner $setup $installed (Join-Path $output 'setup.log') --allow-prerequisite)
    if($LASTEXITCODE -ne 0){throw 'Graphical installer or Finish launch failed in the guest.'}
    $line=@($launch | Where-Object {$_ -match '^APPLICATION_PID=\d+$'})
    if($line.Count -ne 1){throw 'Missing exact installed application PID.'}
    $app=Get-Process -Id ([int]$line[0].Split('=')[1]);$null=$app.Handle
    if($app.MainModule.FileName -ne (Join-Path $installed 'ODS Windows Preview.exe')){throw 'Unexpected guest application path.'}
    if(-not $app.CloseMainWindow() -or -not $app.WaitForExit(10000)){throw 'Installed guest application did not close normally.'}
    $app.Dispose();$app=$null
    & (Join-Path $PSScriptRoot 'ods/tests/test-windows-native-portal.ps1') -Bundle $installed -LiveState (Join-Path $env:LOCALAPPDATA 'ODS Guest QA') -Screenshot (Join-Path $output 'native-portal.png')
    if($LASTEXITCODE -ne 0){throw 'Real model/Portal qualification failed in the guest.'}
    $record.text_model='passed'
    & (Join-Path $PSScriptRoot 'ods/tests/test-windows-native-portal.ps1') -Bundle $installed -LiveState (Join-Path $env:LOCALAPPDATA 'ODS Guest QA') -DocumentLive -Screenshot (Join-Path $output 'native-document.png')
    if($LASTEXITCODE -ne 0){throw 'Real model/document/save qualification failed in the guest.'}
    $record.document_model='passed'
    $record.status='passed'
} catch {
    $record.status='failed';$record.error=$_.Exception.ToString()
    throw
} finally {
    if($app){if(-not $app.HasExited){$null=$app.CloseMainWindow();$null=$app.WaitForExit(10000)};$app.Dispose()}
    $record.finished_utc=[DateTime]::UtcNow.ToString('o')
    $record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output 'RESULTS.json') -Encoding UTF8
    Stop-Transcript
}
