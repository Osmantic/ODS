# SPDX-License-Identifier: Apache-2.0
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallerDirectory,
    [Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$installer=[IO.Path]::GetFullPath($InstallerDirectory)
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){throw 'Use a new QA directory; prior evidence is preserved.'}
$receipt=Get-Content -Raw -Encoding UTF8 (Join-Path $installer 'INSTALLER-BUILD-INFO.json') | ConvertFrom-Json
$setup=Join-Path $installer 'ODS-Windows-Preview-Setup.exe'
if((Get-FileHash -LiteralPath $setup).Hash.ToLowerInvariant() -ne $receipt.installer_sha256){throw 'Installer hash does not match its build receipt.'}
$inputFolder=Join-Path $output 'input'
$results=Join-Path $output 'results'
$null=New-Item -ItemType Directory -Path $inputFolder,$results
Copy-Item -LiteralPath $setup,(Join-Path $installer 'INSTALLER-BUILD-INFO.json') -Destination $inputFolder
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run-sandbox-qa.ps1') -Destination $inputFolder
$root=Join-Path $PSScriptRoot '../../..'
foreach($relative in @('desktop/windows/tests/InstallerUiTests.cs','desktop/windows/tests/PortalUiTests.cs','desktop/windows/tests/NativePortalTests.cs','desktop/windows/tests/DocumentSaveDialogTests.cs','tests/test-windows-native-portal.ps1')){
    $destination=Join-Path $inputFolder ('ods/'+$relative)
    $null=New-Item -ItemType Directory -Path (Split-Path $destination) -Force
    Copy-Item -LiteralPath (Join-Path $root $relative) -Destination $destination
}
$escape={param($value)[Security.SecurityElement]::Escape($value)}
$xml=@"
<Configuration>
  <VGpu>Disable</VGpu><Networking>Enable</Networking><MemoryInMB>12288</MemoryInMB>
  <AudioInput>Disable</AudioInput><VideoInput>Disable</VideoInput>
  <PrinterRedirection>Disable</PrinterRedirection><ClipboardRedirection>Disable</ClipboardRedirection>
  <MappedFolders>
    <MappedFolder><HostFolder>$(& $escape $inputFolder)</HostFolder><SandboxFolder>C:\ODS-QA-Input</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$(& $escape $results)</HostFolder><SandboxFolder>C:\ODS-QA-Output</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ODS-QA-Input\run-sandbox-qa.ps1</Command></LogonCommand>
</Configuration>
"@
$null=[xml]$xml
[IO.File]::WriteAllText((Join-Path $output 'ODS-clean-Windows.wsb'),$xml,[Text.UTF8Encoding]::new($false))
Write-Output "QA kit ready: $output"
Write-Output 'Only input (read-only) and results (writable) are mapped. Internet is enabled for Microsoft WebView2 and the checked model.'
Write-Output 'No Windows feature was enabled and no guest was started. Regenerate this kit after moving it: WSB mappings contain absolute paths.'
