# SPDX-License-Identifier: Apache-2.0
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$PackageDirectory,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$ToolsCache,
    [ValidatePattern('^\d+\.\d+\.\d+\.\d+$')][string]$Version='0.2.0.0')
$ErrorActionPreference='Stop'
if(@($Version.Split('.') | Where-Object {[int64]$_ -gt 65535}).Count){throw 'Installer version parts must be between 0 and 65535.'}
# Schema 2 documents cannot be opened by the published 0.1 text-only reader.
# Keep the existing installer's numeric downgrade guard effective; reject an
# older label before reading a package, acquiring tools or creating output.
if([version]$Version -lt [version]'0.2.0.0'){throw 'Saved document support requires preview installer version 0.2.0.0 or newer.'}
$package=[IO.Path]::GetFullPath($PackageDirectory)
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){throw 'Use a new installer output directory to preserve prior packages.'}
$bundle=Get-Content -Raw -Encoding UTF8 (Join-Path $package 'bundle.json') | ConvertFrom-Json
if(-not $bundle.RuntimeBundled -or -not $bundle.PortalSha256){throw 'The desktop installer requires the source-built native CPU runtime and Portal profile.'}
if((Get-FileHash (Join-Path $package 'runtime/llama-server.exe')).Hash.ToLowerInvariant() -ne $bundle.Runtime.Sha256){throw 'Packaged CPU runtime failed verification.'}
if((Get-FileHash (Join-Path $package 'portal.json')).Hash.ToLowerInvariant() -ne $bundle.PortalSha256){throw 'Packaged Portal manifest failed verification.'}
foreach($pin in (Get-Content -Raw -Encoding UTF8 (Join-Path $package 'portal.json') | ConvertFrom-Json)){
    $path=[IO.Path]::GetFullPath((Join-Path $package $pin.Name))
    if(-not $path.StartsWith($package+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase) -or
       (Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant() -ne $pin.Sha256){throw 'Portal package file failed verification.'}
}
$binaries=@(Get-ChildItem -LiteralPath $package -Recurse -File | Where-Object Extension -EQ '.exe')
if($binaries.Count -ne 2 -or @($binaries | Where-Object Name -NotIn 'ODS Windows Preview.exe','llama-server.exe').Count){throw 'Unexpected executable in the installer payload.'}
$tools=& (Join-Path $PSScriptRoot 'get-build-tools.ps1') -CacheDirectory $ToolsCache -Scope Installer
$signature=Get-AuthenticodeSignature -LiteralPath $tools.webview
if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|, )CN=Microsoft Corporation(,|$)'){throw 'The pinned WebView2 bootstrapper has no valid Microsoft signature.'}
$null=New-Item -ItemType Directory -Path $output
Copy-Item -LiteralPath (Join-Path (Split-Path $tools.inno) 'license.txt') -Destination (Join-Path $output 'LICENSE-INNO-SETUP.txt')
$marker=Join-Path $output 'install-marker.ini'
[IO.File]::WriteAllText($marker,"[Install]`r`nAppId=A1A2BD01-A38F-4790-94D7-75ED422D9FC1`r`nVersion=$Version`r`n",[Text.UTF8Encoding]::new($false))
& $tools.inno /Qp "/DPackageDir=$package" "/DInstallerOutput=$output" "/DPreviewVersion=$Version" "/DWebViewBootstrapper=$($tools.webview)" "/DMarkerFile=$marker" (Join-Path $PSScriptRoot 'installer.iss')
if($LASTEXITCODE -ne 0){throw 'Windows desktop installer did not compile.'}
$installer=Join-Path $output 'ODS-Windows-Preview-Setup.exe'
$sourceReceipt=if(Test-Path -LiteralPath (Join-Path $package 'BUILD-INFO.json')){Get-Content -Raw -Encoding UTF8 (Join-Path $package 'BUILD-INFO.json') | ConvertFrom-Json}else{$null}
$receipt=@{schemaVersion=1;version=$Version;unsigned=$true;scope='Per-user native Portal CPU preview; data retained on uninstall';
    checkout_sha=$sourceReceipt.checkout_sha;pr_head_sha=$sourceReceipt.pr_head_sha;
    installer_sha256=(Get-FileHash $installer).Hash.ToLowerInvariant();installer_bytes=(Get-Item $installer).Length;
    application_sha256=(Get-FileHash (Join-Path $package 'ODS Windows Preview.exe')).Hash.ToLowerInvariant();portal_manifest_sha256=$bundle.PortalSha256;
    runtime_sha256=$bundle.Runtime.Sha256;webview_bootstrapper_sha256=(Get-FileHash $tools.webview).Hash.ToLowerInvariant()}
[IO.File]::WriteAllText((Join-Path $output 'INSTALLER-BUILD-INFO.json'),($receipt|ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $output 'SHA256SUMS.txt'),"$($receipt.installer_sha256)  ODS-Windows-Preview-Setup.exe`n",[Text.UTF8Encoding]::new($false))
Write-Output $installer
