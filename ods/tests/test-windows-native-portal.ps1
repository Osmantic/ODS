# SPDX-License-Identifier: Apache-2.0
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Bundle, [string]$LiveState, [string]$Screenshot,
    [string]$FixtureState, [switch]$VerifySaved)
$ErrorActionPreference='Stop'
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
        "/reference:$wpf/PresentationFramework.dll" "/reference:$wpf/PresentationCore.dll" `
        (Join-Path $source 'tests/PortalUiTests.cs') (Join-Path $source 'tests/NativePortalTests.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Native Portal UI tests did not compile.' }
    if ($LiveState) { & $runner --live ([IO.Path]::GetFullPath($LiveState)) $Screenshot }
    elseif ($FixtureState) { & $runner $(if($VerifySaved){'--verify-state'}else{'--saved-state'}) ([IO.Path]::GetFullPath($FixtureState)) }
    else { & $runner }
    if ($LASTEXITCODE -ne 0) { throw 'Native Portal UI tests failed.' }
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
