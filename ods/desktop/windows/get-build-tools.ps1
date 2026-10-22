# SPDX-License-Identifier: Apache-2.0
# Build-only tooling. No system installer, PATH or registry changes.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$CacheDirectory,
    [ValidateSet('Runtime','Installer','All')][string]$Scope='All')
$ErrorActionPreference='Stop'
if($PSVersionTable.PSVersion.Major -lt 7){throw 'Build tooling requires PowerShell 7; the installed app does not.'}
$pins=Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'native-build-pins.json') | ConvertFrom-Json
$cache=[IO.Path]::GetFullPath($CacheDirectory)
$null=New-Item -ItemType Directory -Path $cache -Force
if((Get-Item -LiteralPath $cache).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Use a normal build-tool cache directory.'}
$names=if($Scope -eq 'Runtime'){@('source','llvm','cmake','ninja')}elseif($Scope -eq 'Installer'){@('inno','unpacker','webview')}else{@('source','llvm','cmake','ninja','inno','unpacker','webview')}
$paths=@{}
foreach($name in $names){
    $pin=$pins.$name
    $archive=Join-Path $cache $pin.file
    if(-not (Test-Path -LiteralPath $archive)){
        $partial=$archive+'.download-'+[guid]::NewGuid().ToString('N')
        Invoke-WebRequest -Uri $pin.url -OutFile $partial -TimeoutSec 300
        if((Get-Item -LiteralPath $partial).Length -ne $pin.size -or (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pin.sha256){throw 'Downloaded build tool failed its pinned size/hash. Partial bytes are retained.'}
        Move-Item -LiteralPath $partial -Destination $archive
    }
    if((Get-Item -LiteralPath $archive).Length -ne $pin.size -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pin.sha256){throw 'Existing build-tool archive failed verification; preserve it and use a new cache.'}
    if($name -eq 'inno'){$paths.innoArchive=$archive;continue}
    if($name -eq 'webview'){$paths.webview=$archive;continue}
    $expanded=Join-Path $cache ('unpacked-'+$name+'-'+$pin.sha256.Substring(0,12))
    $receipt=Join-Path $expanded '.ods-extracted'
    if(-not (Test-Path -LiteralPath $expanded)){
        [IO.Compression.ZipFile]::ExtractToDirectory($archive,$expanded)
        [IO.File]::WriteAllText($receipt,$pin.sha256,[Text.UTF8Encoding]::new($false))
    }
    if(-not (Test-Path -LiteralPath $receipt) -or (Get-Content -LiteralPath $receipt -Raw) -ne $pin.sha256){throw 'Incomplete build-tool extraction was preserved. Use a new cache.'}
    $paths[$name]=Join-Path $expanded $pin.directory
}
if($paths.innoArchive){
    $expanded=Join-Path $cache ('unpacked-inno-'+$pins.inno.sha256.Substring(0,12))
    $receipt=Join-Path $expanded '.ods-extracted'
    if(-not (Test-Path -LiteralPath $expanded)){
        # Read the compiler's archive; do not execute its installer on the host.
        & (Join-Path $paths.unpacker 'innounp.exe') -x -q -m "-d$expanded" $paths.innoArchive | Out-Null
        if($LASTEXITCODE -ne 0){throw 'Compiler archive extraction failed.'}
        [IO.File]::WriteAllText($receipt,$pins.inno.sha256,[Text.UTF8Encoding]::new($false))
    }
    if(-not (Test-Path -LiteralPath $receipt) -or (Get-Content -LiteralPath $receipt -Raw) -ne $pins.inno.sha256){throw 'Incomplete compiler extraction was preserved. Use a new cache.'}
    $paths.inno=Join-Path $expanded '{app}/ISCC.exe'
}
[pscustomobject]$paths
