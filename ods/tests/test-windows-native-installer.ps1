# SPDX-License-Identifier: Apache-2.0
# Run sequentially with other desktop/entrypoint suites: the product's exact
# per-account singleton deliberately makes concurrently open previews busy.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Package,
    [Parameter(Mandatory=$true)][string]$ToolsCache,
    [string]$ReceiptDirectory)
$ErrorActionPreference='Stop'
$source=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../desktop/windows'))
$packagePath=[IO.Path]::GetFullPath($Package)
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('ods-installer-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $fixture
$installId=[guid]::NewGuid().ToString('D')
$title='ODS Windows Preview QA '+$installId
$target=Join-Path $fixture ('installed app with spaces '+[char]0x1EEF+[char]0x6E+[char]0x67)
$data=Join-Path $fixture 'saved-data'
$null=New-Item -ItemType Directory -Path $data
[IO.File]::WriteAllText((Join-Path $data 'conversation.json'),'keep saved conversation')
$registry='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\'+$installId+'_is1'
$shortcut=Join-Path ([Environment]::GetFolderPath('Programs')) ($title+'.lnk')
$tools=& (Join-Path $source 'get-build-tools.ps1') -CacheDirectory $ToolsCache -Scope Installer
$checks=0
$app=$null
function Assert-Installer([bool]$Condition,[string]$Message){
    if(-not $Condition){throw $Message}
    $script:checks++;Write-Output ('PASS '+$Message)
}
function Invoke-Setup([string]$Executable,[string]$Log,[bool]$Uninstall=$false){
    $arguments=@('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="'+$Log+'"'))
    if(-not $Uninstall){$arguments+=('/DIR="'+$target+'"')}
    $process=Start-Process -FilePath $Executable -ArgumentList $arguments -WindowStyle Hidden -PassThru
    try {
        if(-not $process.WaitForExit(60000)){throw 'The owned setup/uninstall process did not settle.'}
        return $process.ExitCode
    } finally {$process.Dispose()}
}
function Build-Fixture([string]$Version){
    $directory=Join-Path $fixture ('setup-'+$Version)
    $null=New-Item -ItemType Directory -Path $directory
    $marker=Join-Path $directory 'marker.ini'
    [IO.File]::WriteAllText($marker,"[Install]`r`nAppId=$installId`r`nVersion=$Version`r`n",[Text.UTF8Encoding]::new($false))
    & $tools.inno /Qp "/DPackageDir=$packagePath" "/DInstallerOutput=$directory" "/DPreviewVersion=$Version" "/DAppGuid=$installId" "/DAppTitle=$title" "/DWebViewBootstrapper=$($tools.webview)" "/DMarkerFile=$marker" (Join-Path $source 'installer.iss') | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Isolated installer fixture did not compile.'}
    return Join-Path $directory 'ODS-Windows-Preview-Setup.exe'
}
try {
    $v1=Build-Fixture '0.1.0.1';$v2=Build-Fixture '0.1.0.2'
    $compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    $wpf=Join-Path (Split-Path $compiler) 'WPF'
    $runner=Join-Path $fixture 'InstallerUiTests.exe'
    & $compiler /nologo /target:exe /platform:x64 "/out:$runner" /reference:System.Management.dll "/reference:$wpf/WindowsBase.dll" "/reference:$wpf/UIAutomationClient.dll" "/reference:$wpf/UIAutomationTypes.dll" (Join-Path $source 'tests/InstallerUiTests.cs')
    if($LASTEXITCODE -ne 0){throw 'Installer GUI harness did not compile.'}
    & $runner $v1 $target (Join-Path $fixture 'cancel-install.log') --cancel
    if($LASTEXITCODE -ne 0){throw 'Installer cancellation did not complete.'}
    Assert-Installer (-not (Test-Path -LiteralPath $target) -and -not (Test-Path -LiteralPath $registry) -and -not (Test-Path -LiteralPath $shortcut)) 'cancelling the actual wizard leaves no installation, registration or shortcut'
    $launch=@(& $runner $v1 $target (Join-Path $fixture 'gui-install.log'))
    if($LASTEXITCODE -ne 0){throw 'Actual graphical install and Finish launch failed.'}
    $pidLine=@($launch | Where-Object {$_ -match '^APPLICATION_PID=\d+$'})
    if($pidLine.Count -ne 1){throw 'The installer did not identify its own launched application.'}
    $app=Get-Process -Id ([int]($pidLine[0].Split('=')[1]))
    $null=$app.Handle
    Assert-Installer ($app.MainModule.FileName -eq (Join-Path $target 'ODS Windows Preview.exe')) 'real installer wizard installs and launches the exact per-user application'
    Assert-Installer ((Test-Path -LiteralPath $shortcut) -and (Test-Path -LiteralPath $registry)) 'native Start Menu shortcut and current-user uninstall entry are registered'
    if(-not $app.CloseMainWindow() -or -not $app.WaitForExit(10000)){throw 'Finish-launched application did not close normally.'}
    $app.Dispose();$app=$null
    # Exercise the actual Shell link. WScript.Shell's TargetPath getter uses
    # an ANSI representation on this host and corrupts Unicode-only characters.
    $app=Start-Process -FilePath $shortcut -PassThru
    $null=$app.Handle
    $opening=[Diagnostics.Stopwatch]::StartNew()
    while(-not $app.HasExited -and $app.MainWindowHandle -eq [IntPtr]::Zero -and $opening.Elapsed.TotalSeconds -lt 15){Start-Sleep -Milliseconds 100;$app.Refresh()}
    Assert-Installer (-not $app.HasExited -and $app.MainModule.FileName -eq (Join-Path $target 'ODS Windows Preview.exe') -and $app.MainWindowHandle -ne [IntPtr]::Zero) 'Start Menu shortcut launches the exact Unicode-path application without a command shell'
    $before=(Get-FileHash (Join-Path $target 'ODS Windows Preview.exe')).Hash
    Assert-Installer ((Invoke-Setup $v2 (Join-Path $fixture 'busy-upgrade.log')) -ne 0 -and -not $app.HasExited) 'upgrade refuses a running ODS application without terminating it'
    Assert-Installer ((Get-FileHash (Join-Path $target 'ODS Windows Preview.exe')).Hash -eq $before) 'busy refusal preserves installed application bytes'
    Assert-Installer ((Invoke-Setup (Join-Path $target 'unins000.exe') (Join-Path $fixture 'busy-uninstall.log') $true) -ne 0 -and -not $app.HasExited) 'uninstall refuses a running ODS application without terminating it'
    if(-not $app.CloseMainWindow() -or -not $app.WaitForExit(10000)){throw 'Installed application did not close normally.'}
    $app.Dispose();$app=$null
    & (Join-Path $PSScriptRoot 'test-windows-native-portal.ps1') -Bundle $target -FixtureState $data
    if($LASTEXITCODE -ne 0){throw 'The installed Portal did not create its actual saved-history fixture.'}
    $savedResults=@(Get-ChildItem -LiteralPath (Join-Path $data 'portal/results') -File | Get-FileHash -Algorithm SHA256 | Select-Object -ExpandProperty Hash)
    [IO.File]::WriteAllText((Join-Path $target 'foreign-user-file.txt'),'keep foreign file')
    [IO.File]::AppendAllText((Join-Path $target 'ODS Windows Preview.exe'),'damaged')
    Assert-Installer ((Invoke-Setup $v1 (Join-Path $fixture 'repair.log')) -eq 0) 'same-version repair completes without a terminal or global runtime installation'
    Assert-Installer ((Get-FileHash (Join-Path $target 'ODS Windows Preview.exe')).Hash -eq $before) 'repair restores the exact packaged application'
    Assert-Installer ((Invoke-Setup $v2 (Join-Path $fixture 'upgrade.log')) -eq 0) 'newer installer upgrades the same owned installation'
    Assert-Installer ((Get-Content -Raw (Join-Path $target '.ods-preview-install')).Contains('Version=0.1.0.2')) 'upgrade records the new version in the installation receipt'
    Assert-Installer ((Invoke-Setup $v1 (Join-Path $fixture 'downgrade.log')) -ne 0) 'older installer refuses a downgrade and preserves the newer installation'
    $denied=Join-Path $target 'uninspectable-child'
    $null=New-Item -ItemType Directory -Path $denied
    $acl=Get-Acl -LiteralPath $denied
    $originalAcl=$acl.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::All)
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,[Security.AccessControl.FileSystemRights]::ListDirectory,[Security.AccessControl.AccessControlType]::Deny))
    Set-Acl -LiteralPath $denied -AclObject $acl
    try {
        Assert-Installer ((Invoke-Setup $v2 (Join-Path $fixture 'denied-repair.log')) -ne 0) 'repair refuses a descendant whose contents cannot be inspected'
        Assert-Installer ((Invoke-Setup (Join-Path $target 'unins000.exe') (Join-Path $fixture 'denied-uninstall.log') $true) -ne 0 -and (Get-FileHash (Join-Path $target 'ODS Windows Preview.exe')).Hash -eq $before) 'failed enumeration refuses uninstall before removing owned files'
    } finally {
        $restore=[Security.AccessControl.DirectorySecurity]::new()
        $restore.SetSecurityDescriptorSddlForm($originalAcl)
        Set-Acl -LiteralPath $denied -AclObject $restore
    }
    $assets=Join-Path $target 'portal/assets';$held=Join-Path $target 'portal/assets-held'
    $foreign=Join-Path $fixture 'foreign-assets';$null=New-Item -ItemType Directory -Path $foreign
    [IO.File]::WriteAllText((Join-Path $foreign 'sentinel.txt'),'keep foreign directory')
    Move-Item -LiteralPath $assets -Destination $held
    $null=New-Item -ItemType Junction -Path $assets -Target $foreign
    try {
        Assert-Installer ((Invoke-Setup $v2 (Join-Path $fixture 'link-repair.log')) -ne 0) 'repair refuses a substituted descendant junction before copying files'
        Assert-Installer ((Invoke-Setup (Join-Path $target 'unins000.exe') (Join-Path $fixture 'link-uninstall.log') $true) -ne 0) 'uninstall refuses a substituted descendant junction before deleting files'
        Assert-Installer ((Get-Content -Raw (Join-Path $foreign 'sentinel.txt')) -eq 'keep foreign directory') 'refusal preserves the foreign junction target'
    } finally {
        if(-not ((Get-Item -LiteralPath $assets).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Expected private fixture junction is missing.'}
        [IO.Directory]::Delete($assets)
        Move-Item -LiteralPath $held -Destination $assets
    }
    Assert-Installer ((Invoke-Setup (Join-Path $target 'unins000.exe') (Join-Path $fixture 'uninstall.log') $true) -eq 0) 'normal per-user uninstall completes'
    Assert-Installer (-not (Test-Path (Join-Path $target 'ODS Windows Preview.exe')) -and -not (Test-Path $registry) -and -not (Test-Path $shortcut)) 'uninstall removes owned binaries, shortcut and its own registration'
    Assert-Installer ((Get-Content -Raw (Join-Path $target 'foreign-user-file.txt')) -eq 'keep foreign file' -and (Get-Content -Raw (Join-Path $data 'conversation.json')) -eq 'keep saved conversation') 'uninstall preserves foreign files and separate saved data'
    $afterResults=@(Get-ChildItem -LiteralPath (Join-Path $data 'portal/results') -File | Get-FileHash -Algorithm SHA256 | Select-Object -ExpandProperty Hash)
    Assert-Installer (-not (Compare-Object $savedResults $afterResults)) 'repair, upgrade and uninstall preserve actual saved Portal result bytes'
    & (Join-Path $PSScriptRoot 'test-windows-native-portal.ps1') -Bundle $packagePath -FixtureState $data -VerifySaved
    if($LASTEXITCODE -ne 0){throw 'The saved Portal history did not restore after uninstall.'}
    [IO.File]::WriteAllText((Join-Path $target 'unowned.txt'),'unowned target')
    Assert-Installer ((Invoke-Setup $v1 (Join-Path $fixture 'foreign-folder.log')) -ne 0 -and (Get-Content -Raw (Join-Path $target 'unowned.txt')) -eq 'unowned target') 'fresh setup refuses an unmarked nonempty folder without changing it'
    Write-Output "PASS $checks Windows native installer lifecycle checks"
} finally {
    if($app){if(-not $app.HasExited){$null=$app.CloseMainWindow();$null=$app.WaitForExit(10000)};$app.Dispose()}
    if($ReceiptDirectory){$null=New-Item -ItemType Directory -Path $ReceiptDirectory -Force;Get-ChildItem -LiteralPath $fixture -Filter '*.log' -File | Copy-Item -Destination $ReceiptDirectory}
    # Preserve failed fixtures for inspection; successful uninstallation already
    # removed only the files registered by this test's unique application ID.
    Write-Output "Installer QA fixture: $fixture"
}
