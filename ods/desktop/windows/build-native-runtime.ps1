# SPDX-License-Identifier: Apache-2.0
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$ToolsCache,
    [ValidateRange(1,32)][int]$Parallel=4)
$ErrorActionPreference='Stop'
$tools=& (Join-Path $PSScriptRoot 'get-build-tools.ps1') -CacheDirectory $ToolsCache -Scope Runtime
$pins=Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'native-build-pins.json') | ConvertFrom-Json
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){throw 'Use a new runtime output directory to preserve prior evidence.'}
$null=New-Item -ItemType Directory -Path $output
$sourceRoot=Join-Path $output 'source'
[IO.Compression.ZipFile]::ExtractToDirectory((Join-Path $ToolsCache $pins.source.file),$sourceRoot)
$source=Join-Path $sourceRoot $pins.source.directory
$patchFile=Join-Path $source $pins.portability_patch.file
if((Get-FileHash -LiteralPath $patchFile).Hash.ToLowerInvariant() -ne $pins.portability_patch.original_sha256){throw 'The pinned portability patch input changed.'}
$original=Get-Content -LiteralPath $patchFile -Raw -Encoding UTF8
$patched=$original.Replace('#include "ngram-mod.h"',"#include <algorithm>`n"+'#include "ngram-mod.h"')
if($original -eq $patched){throw 'The known portability patch did not apply.'}
[IO.File]::WriteAllText($patchFile,$patched,[Text.UTF8Encoding]::new($false))
$build=Join-Path $output 'build'
$compiler=Join-Path $tools.llvm 'bin'
$cmake=Join-Path $tools.cmake 'bin/cmake.exe'
$options=@('-G','Ninja','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_SYSTEM_NAME=Windows','-DCMAKE_SYSTEM_PROCESSOR=AMD64',
    "-DCMAKE_C_COMPILER=$compiler/x86_64-w64-mingw32-clang.exe","-DCMAKE_CXX_COMPILER=$compiler/x86_64-w64-mingw32-clang++.exe",
    "-DCMAKE_MAKE_PROGRAM=$($tools.ninja)/ninja.exe",'-DBUILD_SHARED_LIBS=OFF','-DGGML_BACKEND_DL=OFF',
    '-DGGML_OPENMP=OFF','-DLLAMA_OPENSSL=OFF','-DLLAMA_BUILD_TESTS=OFF','-DLLAMA_BUILD_EXAMPLES=OFF',
    '-DLLAMA_BUILD_WEBUI=OFF','-DGGML_NATIVE=OFF','-DGGML_AVX512=OFF','-DCMAKE_EXE_LINKER_FLAGS=-static',
    '-DCMAKE_C_FLAGS=-D_WIN32_WINNT=0x0A00','-DCMAKE_CXX_FLAGS=-D_WIN32_WINNT=0x0A00',
    '-DLLAMA_BUILD_NUMBER=9014',"-DLLAMA_BUILD_COMMIT=$($pins.source.commit.Substring(0,8))",
    '-DGGML_BUILD_NUMBER=9014',"-DGGML_BUILD_COMMIT=$($pins.source.commit.Substring(0,8))")
& $cmake -S $source -B $build @options
if($LASTEXITCODE -ne 0){throw 'Native runtime configuration failed.'}
& $cmake --build $build --target llama-server --parallel $Parallel
if($LASTEXITCODE -ne 0){throw 'Native runtime build failed.'}
$binary=Join-Path $build 'bin/llama-server.exe'
$imports=& (Join-Path $compiler 'llvm-objdump.exe') -p $binary
if($LASTEXITCODE -ne 0){throw 'Could not inspect native PE dependencies.'}
$dlls=@($imports | ForEach-Object {if($_ -match '^\s*DLL Name:\s*(.+?)\s*$'){$Matches[1].ToLowerInvariant()}})
$system=@('kernel32.dll','advapi32.dll','bcrypt.dll','crypt32.dll','ws2_32.dll','user32.dll','shell32.dll',
    'ole32.dll','oleaut32.dll','version.dll','shlwapi.dll','ntdll.dll','msvcrt.dll','ucrtbase.dll','psapi.dll','rpcrt4.dll')
if(-not $dlls.Count -or @($dlls | Where-Object {$_ -notin $system -and $_ -notmatch '^api-ms-win-(?:crt|core)-[a-z0-9-]+\.dll$'}).Count){throw 'Native runtime imports a non-system DLL. Do not distribute this build.'}
Copy-Item -LiteralPath $binary -Destination (Join-Path $output 'llama-server.exe')
$text=& $binary --license
if($LASTEXITCODE -ne 0){throw 'Runtime licence export failed.'}
[IO.File]::WriteAllText((Join-Path $output 'LICENSE-LLAMA-DEPENDENCIES.txt'),($text -join "`n"),[Text.UTF8Encoding]::new($false))
$notices=@('LLVM/MinGW notices for the statically linked native runtime. Tool executables are not distributed.')
$licences=@((Join-Path $tools.llvm 'LICENSE.TXT'))+@(Get-ChildItem (Join-Path $tools.llvm 'x86_64-w64-mingw32/share/mingw32') -Filter 'COPYING*' -File | Select-Object -ExpandProperty FullName)
foreach($licence in $licences){$notices+="`n=== $(Split-Path $licence -Leaf) ===`n"+(Get-Content -LiteralPath $licence -Raw -Encoding UTF8)}
[IO.File]::WriteAllText((Join-Path $output 'LICENSE-NATIVE-TOOLCHAIN.txt'),($notices -join "`n"),[Text.UTF8Encoding]::new($false))
$receipt=@{schemaVersion=1;profile='windows-x64-ucrt-static-cpu';source_commit=$pins.source.commit;source_archive_sha256=$pins.source.sha256;
    toolchain=$pins.llvm.name;toolchain_sha256=$pins.llvm.sha256;cmake_sha256=$pins.cmake.sha256;ninja_sha256=$pins.ninja.sha256;
    portability_patch=@{file=$pins.portability_patch.file;original_sha256=$pins.portability_patch.original_sha256;patched_sha256=(Get-FileHash $patchFile).Hash.ToLowerInvariant();description=$pins.portability_patch.description};
    options=$options;imports=$dlls;file='llama-server.exe';size=(Get-Item $binary).Length;sha256=(Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()}
[IO.File]::WriteAllText((Join-Path $output 'native-runtime.json'),($receipt|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
Write-Output "Native CPU runtime ready: $output"
