# SPDX-License-Identifier: Apache-2.0
# Build on Windows with the inbox .NET Framework compiler. No SDK/NuGet needed.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [Parameter(Mandatory = $true)][string]$RuntimeArchive,
    [switch]$IncludeModel,
    [string]$ModelPath
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) { throw 'Use a new output directory so an earlier build is preserved.' }
$archive = Get-Item -LiteralPath $RuntimeArchive
# Official ggml-org release-asset digest and size, b9014, CPU x64. This is
# intentionally separate from ODS's Vulkan runtime; no GPU driver is required.
if ($archive.Length -ne 15917797 -or (Get-FileHash -LiteralPath $archive.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne
    '3503cc30154266ac0fbedd38e39e35a313a13412415738ae8f9e23e4fcb44da2') {
    throw 'The llama.cpp b9014 CPU x64 archive failed size/SHA-256 verification.'
}
$catalog = Get-Content -Raw -Encoding UTF8 (Join-Path $repoRoot 'config/model-library.json') | ConvertFrom-Json
$model = @($catalog.models | Where-Object id -EQ 'qwen3.5-2b-q4')
if ($model.Count -ne 1) { throw 'The catalog must contain exactly one qwen3.5-2b-q4 pin.' }
$model = $model[0]
if ($IncludeModel) {
    if (-not $ModelPath) { throw 'IncludeModel needs ModelPath.' }
    $modelFile = Get-Item -LiteralPath $ModelPath
    if ($modelFile.Length -ne $model.size_bytes -or (Get-FileHash -LiteralPath $modelFile.FullName).Hash.ToLowerInvariant() -ne $model.gguf_sha256) {
        throw 'The supplied model failed the ODS catalog size/SHA-256 check.'
    }
}
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$wpf = Join-Path (Split-Path $compiler) 'WPF'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Build on Windows x64 with .NET Framework 4.8.' }
$null = New-Item -ItemType Directory -Path $output
$runtime = Join-Path ([IO.Path]::GetTempPath()) ('ods-desktop-pin-' + [guid]::NewGuid().ToString('N'))
try {
    Expand-Archive -LiteralPath $archive.FullName -DestinationPath $runtime
    $files = @(Get-ChildItem -LiteralPath $runtime -File | Where-Object { $_.Extension -eq '.dll' -or $_.Name -eq 'llama-server.exe' } | Sort-Object Name | ForEach-Object {
        @{ Name = $_.Name; Sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
    if (-not ($files | Where-Object { $_.Name -eq 'llama-server.exe' })) { throw 'The archive has no llama-server.exe at its root.' }
    $info = [Diagnostics.ProcessStartInfo]::new((Join-Path $runtime 'llama-server.exe'), '--license')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($info)
    try {
        $notices = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Upstream licence export failed: $($errors.Result)" }
        [IO.File]::WriteAllText((Join-Path $output 'LICENSE-LLAMA-DEPENDENCIES.txt'), $notices.Result, [Text.UTF8Encoding]::new($false))
    } finally { $process.Dispose() }
} finally {
    if ((Split-Path $runtime -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\')) { throw 'Unexpected temporary runtime path.' }
    if (Test-Path -LiteralPath $runtime) { Remove-Item -LiteralPath $runtime -Recurse -Force }
}
$bundle = @{
    Runtime = @{ Name = 'llama.cpp b9014 CPU x64'; File = 'llama-b9014-bin-win-cpu-x64.zip'; Size = 15917797
        Url = 'https://github.com/ggml-org/llama.cpp/releases/download/b9014/llama-b9014-bin-win-cpu-x64.zip'
        Sha256 = '3503cc30154266ac0fbedd38e39e35a313a13412415738ae8f9e23e4fcb44da2' }
    Model = @{ Name = $model.name; File = $model.gguf_file; Url = $model.gguf_url; Sha256 = $model.gguf_sha256; Size = $model.size_bytes }
    Files = $files
}
[IO.File]::WriteAllText((Join-Path $output 'bundle.json'), ($bundle | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
$icon = Join-Path $repoRoot 'extensions/services/dashboard/public/osmantic-os.ico'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ "/out:$output/ODS Windows Preview.exe" "/win32icon:$icon" `
    /reference:System.Net.Http.dll /reference:System.Web.Extensions.dll /reference:System.Xaml.dll `
    /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/reference:$wpf/WindowsBase.dll" `
    "/reference:$wpf/PresentationFramework.dll" "/reference:$wpf/PresentationCore.dll" `
    (Join-Path $PSScriptRoot 'Runtime.cs') (Join-Path $PSScriptRoot 'RuntimeDownload.cs') (Join-Path $PSScriptRoot 'App.cs')
if ($LASTEXITCODE -ne 0) { throw 'The desktop application did not compile.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination $output
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'VALIDATION.md') -Destination $output
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'docs') -Destination $output -Recurse
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD-PARTY-NOTICES.txt') -Destination $output
Copy-Item -LiteralPath (Join-Path $repoRoot '../LICENSE') -Destination (Join-Path $output 'LICENSE-ODS.txt')
if ($IncludeModel) {
    # Model weights may be seeded. Runtime executables/DLLs are always
    # acquired directly from upstream on first start, not redistributed here.
    $null = New-Item -ItemType Directory -Path (Join-Path $output 'models')
    Copy-Item -LiteralPath $modelFile.FullName -Destination (Join-Path (Join-Path $output 'models') $model.gguf_file)
}
Write-Output (Join-Path $output 'ODS Windows Preview.exe')
