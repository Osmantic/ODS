# SPDX-License-Identifier: Apache-2.0
# Compiles the real installer with only its registry lookup redirected into a
# fresh private fixture. The publisher process is a stub, never Microsoft setup.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Package,
    [Parameter(Mandatory=$true)][string]$ToolsCache,[string]$ReceiptDirectory)
$ErrorActionPreference='Stop'
$source=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../desktop/windows'))
$packagePath=[IO.Path]::GetFullPath($Package)
$id=[guid]::NewGuid().ToString('D')
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('ods-prerequisite-'+$id)
$null=New-Item -ItemType Directory -Path $fixture
$key='Software\ODS\Tests\WebView2\'+$id
$privateKey='HKCU:\'+$key
$installId=[guid]::NewGuid().ToString('D')
$title='ODS Prerequisite QA '+$installId
$registry='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\'+$installId+'_is1'
$shortcut=Join-Path ([Environment]::GetFolderPath('Programs')) ($title+'.lnk')
$target=Join-Path $fixture 'installed app'
$state=Join-Path $fixture 'bootstrapper-state.txt'
$attempts=Join-Path $fixture 'bootstrapper-attempts.txt'
$sentinel=Join-Path $fixture 'saved-conversation.txt'
[IO.File]::WriteAllText($sentinel,'Preserve saved conversations through prerequisite recovery.')
$sentinelHash=(Get-FileHash -LiteralPath $sentinel).Hash
$checks=0;$app=$null;$uninstalled=$false
$priorRoot=$env:ODS_WEBVIEW_TEST_ROOT;$priorId=$env:ODS_WEBVIEW_TEST_ID
$tools=& (Join-Path $source 'get-build-tools.ps1') -CacheDirectory $ToolsCache -Scope Installer
function Assert-Prerequisite([bool]$Condition,[string]$Message){
    if(-not $Condition){throw $Message}
    $script:checks++;Write-Output ('PASS '+$Message)
}
function Clear-PrivateVersion {
    if(Test-Path -LiteralPath $privateKey){Remove-Item -LiteralPath $privateKey -Recurse -Force}
}
function Assert-NoInstall {
    Assert-Prerequisite (-not (Test-Path -LiteralPath $target) -and -not (Test-Path -LiteralPath $registry) -and -not (Test-Path -LiteralPath $shortcut) -and (Get-FileHash -LiteralPath $sentinel).Hash -eq $sentinelHash) 'prerequisite refusal preserves saved bytes and creates no application, registration or shortcut'
}
function Build-PrerequisiteFixture([string]$Bootstrapper,[string]$Name){
    $directory=Join-Path $fixture $Name
    $null=New-Item -ItemType Directory -Path $directory
    $marker=Join-Path $directory 'marker.ini'
    [IO.File]::WriteAllText($marker,"[Install]`r`nAppId=$installId`r`nVersion=0.2.0.0`r`n",[Text.UTF8Encoding]::new($false))
    $script=[IO.File]::ReadAllText((Join-Path $source 'installer.iss'))
    $producer='Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    if(($script.Split(@($producer),[StringSplitOptions]::None).Count-1) -ne 1){throw 'WebView2 registry contract changed; review the fixture boundary.'}
    $script=$script.Replace($producer,$key)
    $iss=Join-Path $directory 'installer.iss'
    [IO.File]::WriteAllText($iss,$script,[Text.UTF8Encoding]::new($false))
    & $tools.inno /Qp "/DPackageDir=$packagePath" "/DInstallerOutput=$directory" "/DAppGuid=$installId" "/DAppTitle=$title" "/DWebViewBootstrapper=$Bootstrapper" "/DMarkerFile=$marker" $iss | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Prerequisite fixture did not compile'}
    return Join-Path $directory 'ODS-Windows-Preview-Setup.exe'
}
function Invoke-PrerequisiteGui([string]$Setup,[string]$Name,[string[]]$Options){
    $priorPreference=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        $lines=@(& $script:runner $Setup $target (Join-Path $fixture ($Name+'.log')) --allow-prerequisite @Options 2>&1)
        $nativeExit=$LASTEXITCODE
    } finally {$ErrorActionPreference=$priorPreference}
    Write-Verbose ($lines -join [Environment]::NewLine) -Verbose
    $lines
    return $nativeExit
}
function Invoke-FixtureUninstall {
    if($script:uninstalled){return}
    $uninstall=Join-Path $target 'unins000.exe'
    if(Test-Path -LiteralPath $uninstall){
        if((Get-Content -Raw (Join-Path $target '.ods-preview-install')) -notmatch ([regex]::Escape('AppId='+$installId))){throw 'Unexpected prerequisite fixture ownership'}
        $process=Start-Process -FilePath $uninstall -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="'+(Join-Path $fixture 'uninstall.log')+'"') -WindowStyle Hidden -PassThru
        try{if(-not $process.WaitForExit(60000) -or $process.ExitCode -ne 0){throw 'Owned prerequisite fixture did not uninstall'}}finally{$process.Dispose()}
        # Inno may leave its self-deleting uninstaller briefly after all
        # registered files are gone. Never execute that lingering stub twice.
        $script:uninstalled=$true
    }
}
try {
    $env:ODS_WEBVIEW_TEST_ROOT=$fixture;$env:ODS_WEBVIEW_TEST_ID=$id
    $compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    $wpf=Join-Path (Split-Path $compiler) 'WPF'
    $bootstrapper=Join-Path $fixture 'MicrosoftEdgeWebview2Setup.exe'
    & $compiler /nologo /target:exe /platform:x64 "/out:$bootstrapper" (Join-Path $source 'tests/WebViewBootstrapperFixture.cs')
    if($LASTEXITCODE -ne 0){throw 'Publisher fixture compile failed'}
    $script:runner=Join-Path $fixture 'InstallerUiTests.exe'
    & $compiler /nologo /target:exe /platform:x64 "/out:$script:runner" /reference:System.Management.dll "/reference:$wpf/WindowsBase.dll" "/reference:$wpf/UIAutomationClient.dll" "/reference:$wpf/UIAutomationTypes.dll" (Join-Path $source 'tests/InstallerUiTests.cs')
    if($LASTEXITCODE -ne 0){throw 'Prerequisite UI driver compile failed'}
    $setup=Build-PrerequisiteFixture $bootstrapper 'setup'
    [IO.File]::WriteAllText($state,'not-installed')
    $silent=Start-Process -FilePath $setup -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/DIR="'+$target+'"'),('/LOG="'+(Join-Path $fixture 'silent.log')+'"') -WindowStyle Hidden -PassThru
    try{Assert-Prerequisite ($silent.WaitForExit(60000) -and $silent.ExitCode -ne 0 -and -not (Test-Path -LiteralPath $attempts)) 'silent missing-runtime setup refuses before invoking any publisher process'}finally{$silent.Dispose()}
    Assert-NoInstall
    $decline=@(Invoke-PrerequisiteGui $setup 'decline' @('--decline-prerequisite','--expect-prerequisite-failure'))
    Assert-Prerequisite ($decline[-1] -eq 0 -and -not (Test-Path -LiteralPath $attempts)) 'declining the actual WebView2 prompt never executes the publisher'
    Assert-NoInstall
    foreach($scenario in @('fail','not-installed','old')){
        Clear-PrivateVersion
        [IO.File]::WriteAllText($state,$scenario)
        $result=@(Invoke-PrerequisiteGui $setup $scenario @('--expect-prerequisite-failure'))
        Assert-Prerequisite ($result[-1] -eq 0 -and $result -contains 'PREREQUISITE_FAILURE_OBSERVED=True') ('actual prerequisite '+$scenario+' shows actionable refusal before application installation')
        if($scenario -eq 'fail'){
            Assert-Prerequisite ([IO.File]::ReadAllText((Join-Path $fixture 'bootstrapper-argv.txt')) -ceq '/silent|/install') 'explicit owner consent invokes the publisher with documented silent install flags'
        }
        Assert-NoInstall
    }
    Clear-PrivateVersion
    $invalidDirectory=Join-Path $fixture 'invalid-publisher'
    $null=New-Item -ItemType Directory -Path $invalidDirectory
    $invalid=Join-Path $invalidDirectory 'MicrosoftEdgeWebview2Setup.exe'
    [IO.File]::WriteAllText($invalid,'This private fixture is deliberately not an executable.')
    $invalidSetup=Build-PrerequisiteFixture $invalid 'invalid-publisher-setup'
    $attemptCount=@(Get-Content -LiteralPath $attempts).Count
    $execFailure=@(Invoke-PrerequisiteGui $invalidSetup 'exec-failure' @('--expect-prerequisite-failure'))
    Assert-Prerequisite ($execFailure[-1] -eq 0 -and $execFailure -contains 'PREREQUISITE_FAILURE_OBSERVED=True' -and @(Get-Content -LiteralPath $attempts).Count -eq $attemptCount) 'publisher launch failure refuses installation without launching another process'
    Assert-NoInstall
    Clear-PrivateVersion
    [IO.File]::WriteAllText($state,'fail')
    $recovered=@(Invoke-PrerequisiteGui $setup 'recovery' @('--recover-prerequisite',$state))
    if($recovered[-1] -ne 0){throw 'Same-wizard prerequisite recovery failed'}
    $pidLine=@($recovered | Where-Object {$_ -match '^APPLICATION_PID=\d+$'})
    if($pidLine.Count -ne 1){throw 'Recovery did not identify exact Finish-launched application'}
    $candidate=Get-Process -Id ([int]$pidLine[0].Split('=')[1])
    try{$null=$candidate.Handle;if($candidate.MainModule.FileName -ne (Join-Path $target 'ODS Windows Preview.exe')){throw 'Unexpected recovery application'};$app=$candidate;$candidate=$null}finally{if($candidate){$candidate.Dispose()}}
    Assert-Prerequisite ($recovered -contains 'PREREQUISITE_FAILURE_OBSERVED=True' -and (Test-Path -LiteralPath $registry) -and (Test-Path -LiteralPath $shortcut)) 'the same wizard recovers after a failed prerequisite and Finish opens its exact installed application'
    if(-not $app.CloseMainWindow() -or -not $app.WaitForExit(10000)){throw 'Recovery app did not close normally'}
    $app.Dispose();$app=$null
    Invoke-FixtureUninstall
    Assert-Prerequisite (-not (Test-Path -LiteralPath (Join-Path $target 'ODS Windows Preview.exe')) -and -not (Test-Path -LiteralPath $registry) -and -not (Test-Path -LiteralPath $shortcut) -and (Get-FileHash -LiteralPath $sentinel).Hash -eq $sentinelHash) 'recovery uninstall removes its application and unique registration and preserves saved bytes'
    Clear-PrivateVersion
    $target=Join-Path $fixture 'nonzero installed app'
    $script:uninstalled=$false
    [IO.File]::WriteAllText($state,'nonzero-installed')
    $usable=@(Invoke-PrerequisiteGui $setup 'nonzero-installed' @())
    if($usable[-1] -ne 0){throw 'A verified usable runtime must remain sufficient despite a nonzero publisher exit.'}
    $pidLine=@($usable | Where-Object {$_ -match '^APPLICATION_PID=\d+$'})
    if($pidLine.Count -ne 1){throw 'Nonzero publisher scenario did not identify its exact application.'}
    $candidate=Get-Process -Id ([int]$pidLine[0].Split('=')[1])
    try{$null=$candidate.Handle;if($candidate.MainModule.FileName -ne (Join-Path $target 'ODS Windows Preview.exe')){throw 'Unexpected nonzero-scenario application.'};$app=$candidate;$candidate=$null}finally{if($candidate){$candidate.Dispose()}}
    Assert-Prerequisite ([IO.File]::ReadAllText((Join-Path $fixture 'bootstrapper-exit.txt')) -ceq '13' -and (Test-Path -LiteralPath $registry) -and (Test-Path -LiteralPath $shortcut)) 'a verified usable runtime remains sufficient after a nonzero publisher exit'
    if(-not $app.CloseMainWindow() -or -not $app.WaitForExit(10000)){throw 'Nonzero-scenario app did not close normally.'}
    $app.Dispose();$app=$null
    Invoke-FixtureUninstall
    Assert-Prerequisite (-not (Test-Path -LiteralPath (Join-Path $target 'ODS Windows Preview.exe')) -and -not (Test-Path -LiteralPath $registry) -and -not (Test-Path -LiteralPath $shortcut) -and (Get-FileHash -LiteralPath $sentinel).Hash -eq $sentinelHash) 'nonzero publisher fixture uninstalls only its owned application and preserves saved bytes'
    Write-Output "PASS $checks Windows native prerequisite checks"
} finally {
    try {
        if($app){if(-not $app.HasExited){$null=$app.CloseMainWindow();$null=$app.WaitForExit(10000)};$app.Dispose()}
        Invoke-FixtureUninstall
    } finally {
        try {Clear-PrivateVersion} finally {
            $env:ODS_WEBVIEW_TEST_ROOT=$priorRoot;$env:ODS_WEBVIEW_TEST_ID=$priorId
            if($ReceiptDirectory){$null=New-Item -ItemType Directory -Path $ReceiptDirectory -Force;Get-ChildItem -LiteralPath $fixture -File -Filter '*.log' | Copy-Item -Destination $ReceiptDirectory}
            Write-Output "Prerequisite fixture retained: $fixture"
        }
    }
}
