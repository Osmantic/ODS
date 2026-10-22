# SPDX-License-Identifier: Apache-2.0
# Execute the shipped prerequisite policy without opening WebView2 or any app.
[CmdletBinding()]
param([string]$SourceDirectory)
$ErrorActionPreference = 'Stop'
if (-not $SourceDirectory) { $SourceDirectory = Join-Path $PSScriptRoot '../desktop/windows' }
$hostSource = Get-Content -Raw -Encoding UTF8 (Join-Path $SourceDirectory 'PortalHost.cs')
$installer = Get-Content -Raw -Encoding UTF8 (Join-Path $SourceDirectory 'installer.iss')
$policy = [regex]::Match($hostSource, '(?ms)^    public static class WebView2Runtime\r?\n.*?^    }\r?$')
if (-not $policy.Success) { throw 'The app needs an explicit, testable WebView2 runtime prerequisite policy.' }
Add-Type -TypeDefinition ('using System; namespace OdsDesktop { ' + $policy.Value + ' }')
$minimum = [OdsDesktop.WebView2Runtime]::MinimumVersion
if ($minimum -cne '154.0.4258.31') { throw 'Review the pinned SDK release compatibility contract before changing its supported runtime floor.' }
$cases = @(
    @{ Version = $null; Expected = $false },
    @{ Version = ''; Expected = $false },
    @{ Version = '0.0.0.0'; Expected = $false },
    @{ Version = '153.0.9999.99'; Expected = $false },
    @{ Version = '154.0.4258.30'; Expected = $false },
    @{ Version = '154.0.4258.31'; Expected = $true },
    @{ Version = '154.0.4258.32'; Expected = $true },
    @{ Version = '154.1.0.0'; Expected = $true },
    @{ Version = '155.0.0.0'; Expected = $true },
    @{ Version = '154.0.4258.31 beta'; Expected = $true },
    @{ Version = '154.0.4258'; Expected = $false },
    @{ Version = '154.0.4258.31.1'; Expected = $false },
    @{ Version = '154.0.4258.31garbage'; Expected = $false },
    @{ Version = '154.0.65536.1'; Expected = $false }
)
foreach ($case in $cases) {
    if ([OdsDesktop.WebView2Runtime]::Supports($case.Version) -ne $case.Expected) { throw "Wrong runtime prerequisite result: $($case.Version)" }
    $message = [OdsDesktop.WebView2Runtime]::PrerequisiteFor($case.Version)
    if ($case.Expected) {
        if ($null -ne $message) { throw 'A supported runtime should not request an update.' }
    } elseif (-not $message.Contains($minimum) -or -not $message.Contains('Install or update')) {
        throw 'An unavailable/old runtime must give the precise update requirement.'
    }
}
if (-not $installer.Contains('#define MinimumWebViewVersion "' + $minimum + '"') -or
    -not $installer.Contains('not Downgrade(MinimumWebViewVersion, MachineVersion)') -or
    -not $installer.Contains('not Downgrade(MinimumWebViewVersion, UserVersion)')) {
    throw 'Setup must compare the full installed runtime version against the same SDK minimum.'
}
if (-not $hostSource.Contains('WebView2Runtime.PrerequisiteFor(CoreWebView2Environment.GetAvailableBrowserVersionString())') -or
    -not $hostSource.Contains('WebView2Runtime.PrerequisiteFor(environment.BrowserVersionString)') -or
    -not $hostSource.Contains('options.TargetCompatibleBrowserVersion = WebView2Runtime.MinimumVersion;')) {
    throw 'Check the selected browser before downloads and the actual environment before opening Portal.'
}
Write-Output "PASS $($cases.Count) WebView2 version boundaries, update diagnostics, installer parity and selected-runtime checks"
