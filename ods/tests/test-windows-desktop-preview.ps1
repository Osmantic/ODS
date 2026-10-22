# SPDX-License-Identifier: Apache-2.0
[CmdletBinding()]
param([string]$LiveBundle, [string]$ModelPath, [string]$UiBundle, [string]$UiState, [string]$Screenshot, [string]$PackageBundle)
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'This suite must execute on native Windows.' }
$source = Join-Path $PSScriptRoot '../desktop/windows'
$portalSources = @('PortalPackage.cs','PortalState.cs','NativePortalSession.cs') | ForEach-Object { Join-Path $source $_ }
$supportSources = @('ModelDownload.cs','NativeContextBudget.cs','NativeDocument.cs','NativeDocumentModel.cs') | ForEach-Object { Join-Path $source $_ }
$reviewTests = @('PortalStateReviewTests.cs','NativeContextBudgetTests.cs','ModelDownloadReviewTests.cs','NativeDocumentTests.cs','NativeDocumentBudgetTests.cs','NativeDocumentFlowTests.cs') | ForEach-Object { Join-Path $source "tests/$_" }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$wpf = Join-Path (Split-Path $compiler) 'WPF'
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-desktop-build-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
try {
    $runner = Join-Path $fixture 'DesktopTests.exe'
    & $compiler /nologo /target:exe /main:DesktopTests /platform:x64 "/out:$runner" `
        /reference:System.Net.Http.dll /reference:System.Web.Extensions.dll /reference:System.Xaml.dll `
        "/reference:$wpf/WindowsBase.dll" "/reference:$wpf/PresentationFramework.dll" "/reference:$wpf/PresentationCore.dll" `
        /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll `
        (Join-Path $source 'Runtime.cs') (Join-Path $source 'RuntimeDownload.cs') (Join-Path $source 'App.cs') `
        (Join-Path $source 'tests/DesktopTests.cs') (Join-Path $source 'tests/WindowTests.cs') (Join-Path $source 'tests/EntrypointTests.cs') (Join-Path $source 'tests/NativePortalTests.cs') $portalSources $supportSources $reviewTests
    if ($LASTEXITCODE -ne 0) { throw 'Desktop test runner did not compile.' }
    & $compiler /nologo /target:winexe /platform:x64 "/out:$fixture/ODS Windows Preview.exe" `
        /reference:System.Net.Http.dll /reference:System.Web.Extensions.dll /reference:System.Xaml.dll `
        /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/reference:$wpf/WindowsBase.dll" `
        "/reference:$wpf/PresentationFramework.dll" "/reference:$wpf/PresentationCore.dll" `
        (Join-Path $source 'Runtime.cs') (Join-Path $source 'RuntimeDownload.cs') (Join-Path $source 'App.cs') $portalSources $supportSources
    if ($LASTEXITCODE -ne 0) { throw 'Desktop GUI did not compile.' }
    if ($PackageBundle) {
        & $runner --package ([IO.Path]::GetFullPath($PackageBundle))
    } elseif ($UiBundle) {
        if (-not $UiState) { throw 'UiBundle needs an isolated UiState directory.' }
        & $runner --ui-live ([IO.Path]::GetFullPath($UiBundle)) ([IO.Path]::GetFullPath($UiState)) $Screenshot
    } elseif ($LiveBundle) {
        if (-not $ModelPath) { throw 'LiveBundle needs ModelPath.' }
        & $runner --live ([IO.Path]::GetFullPath($LiveBundle)) ([IO.Path]::GetFullPath($ModelPath))
    } else { & $runner }
    if ($LASTEXITCODE -ne 0) { throw 'Desktop behavioral tests failed.' }
} finally {
    # A fixed fixture child created by this suite; never enumerate arbitrary paths.
    if ((Split-Path $fixture -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\')) { throw 'Unexpected fixture path.' }
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
