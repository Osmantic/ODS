# SPDX-License-Identifier: Apache-2.0
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Bundle, [string]$LiveState, [string]$Screenshot,
    [string]$FixtureState, [switch]$VerifySaved, [switch]$DocumentFixture, [switch]$DocumentLive)
$ErrorActionPreference='Stop'
if (($DocumentFixture -and ($LiveState -or $VerifySaved -or $DocumentLive)) -or
    ($DocumentLive -and (-not $LiveState -or $FixtureState -or $VerifySaved))) {
    throw 'DocumentFixture accepts only an optional FixtureState; DocumentLive requires LiveState and cannot verify a fixture.'
}
$livePath=if($LiveState){[IO.Path]::GetFullPath($LiveState)}else{''}
$fixtureStatePath=if($FixtureState){[IO.Path]::GetFullPath($FixtureState)}else{''}
$screenshotPath=if($Screenshot){[IO.Path]::GetFullPath($Screenshot)}else{''}
$source=Join-Path $PSScriptRoot '../desktop/windows'
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('ods-portal-ui-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $fixture
try {
    Get-ChildItem -LiteralPath $Bundle | Where-Object Name -NE 'models' | Copy-Item -Destination $fixture -Recurse
    $compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    $wpf=Join-Path (Split-Path $compiler) 'WPF'
    $runner=Join-Path $fixture 'PortalUiTests.exe'
    & $compiler /nologo /target:exe /platform:x64 "/out:$runner" `
        "/reference:$fixture/ODS Windows Preview.exe" "/reference:$fixture/OdsPortal.dll" `
        "/reference:$fixture/Microsoft.Web.WebView2.Core.dll" "/reference:$fixture/Microsoft.Web.WebView2.Wpf.dll" `
        /reference:System.Web.Extensions.dll /reference:System.Xaml.dll "/reference:$wpf/WindowsBase.dll" `
        "/reference:$wpf/UIAutomationClient.dll" "/reference:$wpf/UIAutomationTypes.dll" `
        "/reference:$wpf/PresentationFramework.dll" "/reference:$wpf/PresentationCore.dll" `
        (Join-Path $source 'tests/PortalUiTests.cs') (Join-Path $source 'tests/NativePortalTests.cs') `
        (Join-Path $source 'tests/DocumentSaveDialogTests.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Native Portal UI tests did not compile.' }
    # A native picker with an uncommitted filename must never default to the
    # source checkout. Preserve a sentinel even on a failing graphical test.
    $defaultFile=Join-Path $fixture 'document.md'
    [IO.File]::WriteAllText($defaultFile,'Preserve this default-name fixture sentinel.',[Text.UTF8Encoding]::new($false))
    $defaultHash=(Get-FileHash -LiteralPath $defaultFile -Algorithm SHA256).Hash
    Push-Location $fixture
    try {
        if ($DocumentLive) { & $runner --document-live $livePath $screenshotPath }
        elseif ($DocumentFixture) {
            if ($FixtureState) { & $runner --document-fixture $fixtureStatePath }
            else { & $runner --document-fixture }
        }
        elseif ($LiveState) { & $runner --live $livePath $screenshotPath }
        elseif ($FixtureState) { & $runner $(if($VerifySaved){'--verify-state'}else{'--saved-state'}) $fixtureStatePath }
        else { & $runner }
        $testExit=$LASTEXITCODE
    } finally {
        Pop-Location
        if(-not (Test-Path -LiteralPath $defaultFile) -or (Get-FileHash -LiteralPath $defaultFile -Algorithm SHA256).Hash -ne $defaultHash) {
            throw 'The graphical test changed its private default-name sentinel.'
        }
        Write-Output 'PASS private picker default-name sentinel remains unchanged'
    }
    if ($testExit -ne 0) { throw 'Native Portal UI tests failed.' }
} finally {
    if ((Split-Path $fixture -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\')) { throw 'Unexpected UI fixture path.' }
    # WebView2 releases its browser metrics handle shortly after the test
    # process exits. Bound that observed shutdown delay; never kill a browser.
    $shutdown=[Diagnostics.Stopwatch]::StartNew()
    while (Test-Path -LiteralPath $fixture) {
        try { Remove-Item -LiteralPath $fixture -Recurse -Force -ErrorAction Stop }
        catch [IO.IOException] { if ($shutdown.Elapsed.TotalSeconds -ge 15) { throw }; Start-Sleep -Milliseconds 100 }
        catch [UnauthorizedAccessException] { if ($shutdown.Elapsed.TotalSeconds -ge 15) { throw }; Start-Sleep -Milliseconds 100 }
    }
}
