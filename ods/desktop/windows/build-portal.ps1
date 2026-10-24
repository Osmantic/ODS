# SPDX-License-Identifier: Apache-2.0
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$PortalAssets,
    [Parameter(Mandatory=$true)][string]$WebViewArchive)
$ErrorActionPreference='Stop'
$archive=Get-Item -LiteralPath $WebViewArchive
if ($archive.Length -ne 9801922 -or (Get-FileHash -LiteralPath $archive.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne '56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9') {
    throw 'Microsoft.Web.WebView2 1.0.4258.31 package failed verification.'
}
if (-not (Test-Path -LiteralPath (Join-Path $PortalAssets 'native.html'))) { throw 'Build the existing Dashboard native entrypoint first (npm run build:native).' }
$output=[IO.Path]::GetFullPath($OutputDirectory)
$compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$wpf=Join-Path (Split-Path $compiler) 'WPF'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip=[IO.Compression.ZipFile]::OpenRead($archive.FullName)
try {
    $sources=@{'lib/net462/Microsoft.Web.WebView2.Core.dll'='Microsoft.Web.WebView2.Core.dll'
        'lib/net462/Microsoft.Web.WebView2.Wpf.dll'='Microsoft.Web.WebView2.Wpf.dll'
        'runtimes/win-x64/native/WebView2Loader.dll'='WebView2Loader.dll';'LICENSE.txt'='LICENSE-WEBVIEW2.txt'}
    foreach($name in $sources.Keys) {
        $entry=$zip.GetEntry($name)
        if (-not $entry) { throw 'Required WebView2 SDK file is missing.' }
        $entryStream=$entry.Open()
        try { $file=[IO.File]::Create((Join-Path $output $sources[$name]));try{$entryStream.CopyTo($file)}finally{$file.Dispose()} }finally{$entryStream.Dispose()}
    }
} finally { $zip.Dispose() }
Copy-Item -LiteralPath $PortalAssets -Destination (Join-Path $output 'portal') -Recurse
& $compiler /nologo /target:library /platform:x64 /optimize+ "/out:$output/OdsPortal.dll" `
    "/reference:$output/ODS Windows Preview.exe" "/reference:$output/Microsoft.Web.WebView2.Core.dll" "/reference:$output/Microsoft.Web.WebView2.Wpf.dll" `
    /reference:System.Xaml.dll "/reference:$wpf/WindowsBase.dll" "/reference:$wpf/PresentationFramework.dll" "/reference:$wpf/PresentationCore.dll" `
    (Join-Path $PSScriptRoot 'PortalHost.cs')
if ($LASTEXITCODE -ne 0) { throw 'Native Portal host did not compile.' }
$pins=@(Get-ChildItem -LiteralPath (Join-Path $output 'portal') -Recurse -File)+@('OdsPortal.dll','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.Wpf.dll','WebView2Loader.dll','LICENSE-WEBVIEW2.txt'|ForEach-Object {Get-Item -LiteralPath (Join-Path $output $_)})
$manifest=@($pins|Sort-Object FullName|ForEach-Object {@{Name=$_.FullName.Substring($output.Length+1).Replace('\','/');Sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}})
[IO.File]::WriteAllText((Join-Path $output 'portal.json'),($manifest|ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
$bundle=Get-Content -LiteralPath (Join-Path $output 'bundle.json') -Raw -Encoding UTF8|ConvertFrom-Json
$bundle|Add-Member -NotePropertyName PortalSha256 -NotePropertyValue (Get-FileHash -LiteralPath (Join-Path $output 'portal.json') -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $output 'bundle.json'),($bundle|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
