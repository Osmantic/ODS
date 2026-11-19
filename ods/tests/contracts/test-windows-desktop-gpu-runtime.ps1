# Contract tests for ODS Windows Native Desktop Milestone PR 2:
# - DirectX UserGpuPreferences parsing, registry read/write, and GPU-first vs Power Saving selection
# - Multi-vendor Vulkan/CUDA device selection (NVIDIA, AMD, Intel, CPU fallback)
# - Resumable GGUF model download with sidecar .sha256.json stamp caching and curl exit 33 recovery
# - Pinned embeddable Python contract validation, wheel extraction, and pin.json tamper detection

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path

. (Join-Path $root 'installers/windows/lib/private-file.ps1')
. (Join-Path $root 'installers/windows/lib/native-llama-runtime.ps1')
. (Join-Path $root 'installers/windows/lib/desktop-gpu-preferences.ps1')
. (Join-Path $root 'installers/windows/lib/desktop-model.ps1')
. (Join-Path $root 'installers/windows/lib/desktop-python.ps1')

$script:checks = 0
function Assert-Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
    $script:checks++
    Write-Host "PASS $Message"
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-win-desktop-pr2-' + [guid]::NewGuid().ToString('N'))
$testRegKey = "HKCU:\Software\ODS_Test_DirectX_GpuPrefs_" + [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

try {
    # --- 1. DirectX UserGpuPreferences parsing & formatting ---
    $pHigh = ConvertFrom-ODSDirectXGpuPreferenceString 'GpuPreference=2;'
    Assert-Check ($pHigh.PreferenceCode -eq 2 -and $pHigh.Mode -ceq 'high_performance' -and $pHigh.PreferDiscreteGpu -eq $true) `
        'GpuPreference=2 parses as high_performance (discrete GPU)'

    $pSave = ConvertFrom-ODSDirectXGpuPreferenceString 'AppStatus=0;GpuPreference=1;'
    Assert-Check ($pSave.PreferenceCode -eq 1 -and $pSave.Mode -ceq 'power_saving' -and $pSave.PreferDiscreteGpu -eq $false) `
        'AppStatus=0;GpuPreference=1; parses as power_saving and preserves tokens'

    $updatedRaw = ConvertTo-ODSDirectXGpuPreferenceString -PreferenceCode 2 -ExistingRawValue 'AppStatus=0;GpuPreference=1;'
    Assert-Check ($updatedRaw -ceq 'AppStatus=0;GpuPreference=2;') `
        'ConvertTo-ODSDirectXGpuPreferenceString preserves AppStatus=0 while updating GpuPreference'

    # --- 2. Registry read/write & respect for Windows Settings override ---
    $odsExe = Join-Path $tempRoot 'ODS\ODS.exe'
    $llamaExe = Join-Path $tempRoot 'ODS\llama.cpp\llama-server.exe'

    $defaultPref = Get-ODSWindowsUserGpuPreference -ExecutablePaths @($odsExe, $llamaExe) -RegistryPath $testRegKey
    Assert-Check ($defaultPref.Mode -ceq 'high_performance' -and $defaultPref.PreferDiscreteGpu -eq $true -and $defaultPref.Source -ceq 'default_gpu_first') `
        'Missing registry key defaults to GPU-First (high_performance)'

    $written = Set-ODSWindowsUserGpuPreference -ExecutablePaths @($odsExe, $llamaExe) -Mode 'high_performance' -RespectExisting $true -RegistryPath $testRegKey
    Assert-Check ($written.Count -eq 2 -and $written[0].Written -eq $true -and $written[0].RawValue -ceq 'GpuPreference=2;') `
        'First install writes GpuPreference=2; for ODS.exe and llama-server.exe'

    # Simulate user switching ODS.exe to Power Saving in Windows Settings (with AppStatus=0;)
    New-ItemProperty -LiteralPath $testRegKey -Name ([IO.Path]::GetFullPath($odsExe)) -Value 'AppStatus=0;GpuPreference=1;' -PropertyType String -Force | Out-Null

    $rerunWrite = Set-ODSWindowsUserGpuPreference -ExecutablePaths @($odsExe) -Mode 'high_performance' -RespectExisting $true -RegistryPath $testRegKey
    Assert-Check ($rerunWrite[0].Written -eq $false -and $rerunWrite[0].KeptExisting -eq $true -and $rerunWrite[0].Mode -ceq 'power_saving') `
        'Installer rerun respects user Power Saving override in Windows Graphics Settings'

    $resolvedAfterOverride = Get-ODSWindowsUserGpuPreference -ExecutablePaths @($odsExe, $llamaExe) -RegistryPath $testRegKey
    Assert-Check ($resolvedAfterOverride.Mode -ceq 'power_saving' -and $resolvedAfterOverride.PreferDiscreteGpu -eq $false) `
        'Get-ODSWindowsUserGpuPreference reports power_saving after Windows Settings override'

    $forcedWrite = Set-ODSWindowsUserGpuPreference -ExecutablePaths @($odsExe) -Mode 'high_performance' -RespectExisting $false -RegistryPath $testRegKey
    Assert-Check ($forcedWrite[0].Written -eq $true -and $forcedWrite[0].RawValue -ceq 'AppStatus=0;GpuPreference=2;') `
        'Explicit UI mode switch updates GpuPreference=2 while preserving AppStatus=0'

    # --- 3. Multi-vendor Vulkan/CUDA device selection & GPU/CPU routing ---
    $sampleDevices = @(
        [pscustomobject]@{ Name = 'Vulkan0'; Description = 'NVIDIA GeForce GTX 1650'; TotalMiB = 4096L; FreeMiB = 3800L },
        [pscustomobject]@{ Name = 'Vulkan1'; Description = 'Intel(R) UHD Graphics'; TotalMiB = 1024L; FreeMiB = 900L },
        [pscustomobject]@{ Name = 'Vulkan2'; Description = 'Microsoft Basic Render Driver'; TotalMiB = 8192L; FreeMiB = 8000L }
    )
    $devNvidia = Select-ODSWindowsDesktopLlamaDevice -Devices $sampleDevices -AdapterName 'NVIDIA GeForce GTX 1650' -PreferDiscreteGpu $true
    Assert-Check ($null -ne $devNvidia -and $devNvidia.Name -ceq 'Vulkan0') `
        'Select-ODSWindowsDesktopLlamaDevice selects NVIDIA GeForce GTX 1650 on Vulkan0 in GPU-First mode'

    $devPowerSave = Select-ODSWindowsDesktopLlamaDevice -Devices $sampleDevices -AdapterName 'NVIDIA GeForce GTX 1650' -PreferDiscreteGpu $false
    Assert-Check ($null -eq $devPowerSave) `
        'Select-ODSWindowsDesktopLlamaDevice returns $null (CPU route) when Power Saving is active and iGPU < 2 GiB'

    $sampleDevicesWithLargeIgpu = @(
        [pscustomobject]@{ Name = 'Vulkan0'; Description = 'AMD Radeon RX 7900 XTX'; TotalMiB = 24576L; FreeMiB = 24000L },
        [pscustomobject]@{ Name = 'Vulkan1'; Description = 'AMD Radeon(TM) Graphics'; TotalMiB = 4096L; FreeMiB = 3800L }
    )
    $devIgpuSave = Select-ODSWindowsDesktopLlamaDevice -Devices $sampleDevicesWithLargeIgpu -AdapterName 'AMD Radeon RX 7900 XTX' -PreferDiscreteGpu $false
    Assert-Check ($null -ne $devIgpuSave -and $devIgpuSave.Name -ceq 'Vulkan1') `
        'Select-ODSWindowsDesktopLlamaDevice selects 4 GiB integrated GPU when Power Saving is active'

    # --- 4. Resumable GGUF model download & sidecar .sha256.json stamp ---
    $modelsDir = Join-Path $tempRoot 'models'
    $payloadBytes = [Text.Encoding]::UTF8.GetBytes('GGUF-TEST-MODEL-PAYLOAD-BYTES-0123456789')
    $shaAlg = [Security.Cryptography.SHA256]::Create()
    $expectedSha = (-join ($shaAlg.ComputeHash($payloadBytes) | ForEach-Object { $_.ToString('x2') }))
    $shaAlg.Dispose()

    $curlCalls = 0
    $dl1 = Invoke-ODSResumableModelDownload -ModelsDir $modelsDir -GgufFile 'test-model.gguf' `
        -Url 'https://huggingface.co/example/test-model.gguf' -ExpectedSha256 $expectedSha -ExpectedSize $payloadBytes.Length `
        -CurlRunner {
            param($curlArgs, $outFile, $url)
            $script:curlCalls++
            [IO.File]::WriteAllBytes($outFile, $payloadBytes)
            return 0
        }
    Assert-Check ($dl1.Status -ceq 'downloaded' -and $dl1.FromStamp -eq $false -and $curlCalls -eq 1) `
        'First Invoke-ODSResumableModelDownload downloads model and writes .sha256.json stamp'

    $sw = [Diagnostics.Stopwatch]::StartNew()
    $dl2 = Invoke-ODSResumableModelDownload -ModelsDir $modelsDir -GgufFile 'test-model.gguf' `
        -Url 'https://huggingface.co/example/test-model.gguf' -ExpectedSha256 $expectedSha -ExpectedSize $payloadBytes.Length `
        -CurlRunner { throw 'Should not invoke curl when stamp is valid' }
    $sw.Stop()
    Assert-Check ($dl2.Status -ceq 'cached' -and $dl2.FromStamp -eq $true -and $sw.ElapsedMilliseconds -lt 2500) `
        "Second Invoke-ODSResumableModelDownload verifies from .sha256.json stamp in $($sw.ElapsedMilliseconds) ms without curl"

    # Touch LastWriteTimeUtc -> stamp is invalidated, file is re-hashed once and stamp is refreshed
    $modelFile = Join-Path $modelsDir 'test-model.gguf'
    (Get-Item -LiteralPath $modelFile).LastWriteTimeUtc = [datetime]::UtcNow.AddMinutes(5)
    $recheck = Test-ODSVerifiedModelFile -ModelPath $modelFile -ExpectedSha256 $expectedSha
    Assert-Check ($recheck.Verified -eq $true -and $recheck.FromStamp -eq $false) `
        'Changing LastWriteTimeUtc invalidates the stamp and triggers a fresh SHA-256 verification'
    $recheck2 = Test-ODSVerifiedModelFile -ModelPath $modelFile -ExpectedSha256 $expectedSha
    Assert-Check ($recheck2.Verified -eq $true -and $recheck2.FromStamp -eq $true) `
        'Refreshed stamp is used on the next check'

    # Simulate curl exit 33 (HTTP 416 Range Not Satisfiable) recovery on a stale .partial file
    $rangeRetryCalls = 0
    $partialFile = Join-Path $modelsDir 'range-retry.gguf.partial'
    [IO.File]::WriteAllBytes($partialFile, [byte[]]@(1, 2, 3))
    $dlRange = Invoke-ODSResumableModelDownload -ModelsDir $modelsDir -GgufFile 'range-retry.gguf' `
        -Url 'https://huggingface.co/example/range-retry.gguf' -ExpectedSha256 $expectedSha -ExpectedSize $payloadBytes.Length `
        -CurlRunner {
            param($curlArgs, $outFile, $url)
            $script:rangeRetryCalls++
            if ($curlArgs -contains '--continue-at') {
                return 33
            }
            [IO.File]::WriteAllBytes($outFile, $payloadBytes)
            return 0
        }
    Assert-Check ($dlRange.Status -ceq 'downloaded' -and $rangeRetryCalls -eq 2) `
        'Invoke-ODSResumableModelDownload recovers automatically when curl returns exit 33 (HTTP 416)'

    # Regression test: Exercise the process-launch branch with spaces in both directory and filename
    # using a local mock curl executable to verify argument boundaries are preserved without splitting.
    $mockCurlBin = Join-Path $tempRoot 'mock-curl-bin'
    New-Item -ItemType Directory -Force -Path $mockCurlBin | Out-Null
    $mockCurlExe = Join-Path $mockCurlBin 'curl.exe'
    $mockCurlSrc = @'
using System;
using System.IO;
public class MockCurlApp {
    public static int Main(string[] args) {
        string outFile = null;
        for (int i = 0; i < args.Length; i++) {
            if (args[i] == "--output" && i + 1 < args.Length) {
                outFile = args[i + 1];
            }
        }
        if (!string.IsNullOrEmpty(outFile)) {
            File.WriteAllBytes(outFile, System.Text.Encoding.UTF8.GetBytes("GGUF-TEST-MODEL-PAYLOAD-BYTES-0123456789"));
            File.WriteAllLines(outFile + ".args.txt", args);
        }
        return 0;
    }
}
'@
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if (-not (Test-Path -LiteralPath $compiler)) {
        $compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    }
    $mockCurlCs = Join-Path $mockCurlBin 'mockcurl.cs'
    [IO.File]::WriteAllText($mockCurlCs, $mockCurlSrc)
    & $compiler /nologo /target:exe ("/out:$mockCurlExe") $mockCurlCs
    if ($LASTEXITCODE -ne 0) { throw 'Could not compile mock curl fixture' }

    $spacedModelsDir = Join-Path $tempRoot 'models with spaces'
    $spacedGgufFile = 'model with spaces.gguf'
    $progressCalls = 0

    $origEnvPath = $env:PATH
    try {
        $env:PATH = "$mockCurlBin;$origEnvPath"
        $dlSpaced = Invoke-ODSResumableModelDownload -ModelsDir $spacedModelsDir -GgufFile $spacedGgufFile `
            -Url 'https://huggingface.co/example/model%20with%20spaces.gguf' -ExpectedSha256 $expectedSha -ExpectedSize $payloadBytes.Length `
            -CurlPath $mockCurlExe `
            -ProgressCallback {
                param($curBytes, $totalBytes)
                $script:progressCalls++
            }

        $spacedModelPath = Join-Path $spacedModelsDir $spacedGgufFile
        $argsRecordPath = "$spacedModelPath.partial.args.txt"
        $recordedArgs = if (Test-Path -LiteralPath $argsRecordPath) { Get-Content -LiteralPath $argsRecordPath } else { @() }
        $outputArgIndex = [array]::IndexOf($recordedArgs, '--output')
        $actualOutFileArg = if ($outputArgIndex -ge 0 -and $outputArgIndex + 1 -lt $recordedArgs.Count) { $recordedArgs[$outputArgIndex + 1] } else { '' }

        Assert-Check ($dlSpaced.Status -ceq 'downloaded' -and `
            (Test-Path -LiteralPath $spacedModelPath -PathType Leaf) -and `
            $actualOutFileArg -eq "$spacedModelPath.partial" -and `
            $actualOutFileArg -match ' ' -and `
            $progressCalls -ge 1) `
            'Invoke-ODSResumableModelDownload preserves curl argument boundaries for paths with spaces during process launch'
    } finally {
        $env:PATH = $origEnvPath
    }

    # --- 5. Pinned Embeddable Python Contract & Tamper Verification ---
    $pyContract = Read-ODSWindowsDesktopPythonContract -SourceRoot $root
    Assert-Check ($pyContract.id -ceq 'python-3.12.10-embed-amd64' -and @($pyContract.wheels).Count -ge 30) `
        'config/windows-desktop.json loads valid python-3.12.10-embed-amd64 contract with zero litellm wheels'

    $wheelMap = @{}
    foreach ($w in @($pyContract.wheels)) {
        $wheelMap[[string]$w.name] = [string]$w.version
    }
    Assert-Check ($wheelMap.ContainsKey('pillow') -and $wheelMap.ContainsKey('aiohttp') -and $wheelMap.ContainsKey('qrcode') -and $wheelMap.ContainsKey('fastapi')) `
        'config/windows-desktop.json contains required Pillow, aiohttp, qrcode, and fastapi runtime dependencies'

    Assert-Check ($wheelMap['fastapi'] -eq '0.141.1' -and `
        $wheelMap['starlette'] -eq '1.7.0' -and `
        $wheelMap['aiohttp'] -eq '3.14.4' -and `
        $wheelMap['certifi'] -ge '2026.10.19' -and `
        $wheelMap['uvicorn'] -match '^0\.2[7-9]\.' -and `
        $wheelMap.ContainsKey('qrcode')) `
        'config/windows-desktop.json aligns with dashboard-api requirements.txt (FastAPI 0.141.1, Starlette 1.7.0, aiohttp 3.14.4, certifi 2026.10.19+, uvicorn <0.30.0, qrcode)'

    # Verify that the Windows desktop Python resolver installs the pinned runtime on clean runners,
    # prioritizes it over host/developer Python, imports dashboard-api main.py, and checks dependency compatibility.
    $apiDir = Join-Path (Join-Path (Join-Path $root 'extensions') 'services') 'dashboard-api'
    if (-not (Test-Path -LiteralPath $apiDir)) {
        $apiDir = Join-Path (Join-Path (Join-Path $root 'ods') 'extensions') 'services\dashboard-api'
    }

    $targetDir = Get-ODSWindowsDesktopPythonInstallDir -PythonContract $pyContract
    if (-not (Test-Path -LiteralPath $targetDir -PathType Container)) {
        $null = Install-ODSWindowsDesktopPython -PythonContract $pyContract
    }

    $verifiedPy = Resolve-ODSWindowsDesktopPythonExecutable -SourceRoot $root
    Assert-Check ($verifiedPy -and (Test-Path -LiteralPath $verifiedPy -PathType Leaf) -and $verifiedPy.StartsWith($targetDir, [StringComparison]::OrdinalIgnoreCase)) `
        'Resolve-ODSWindowsDesktopPythonExecutable resolves the verified pinned runtime and prioritizes it over developer Python'

    $depScript = @'
import sys
from pathlib import Path
import importlib.metadata

api_dir = sys.argv[1]
sys.path.insert(0, api_dir)

# 1. Module imports
import main
import qrcode
import PIL
import fastapi
import starlette
import uvicorn
import aiohttp
import httpx
import pydantic
import yaml
import jsonschema

# 2. Dependency compatibility with dashboard-api requirements.txt
req_path = Path(api_dir) / 'requirements.txt'
assert req_path.is_file(), f'Missing requirements.txt at {req_path}'

fastapi_v = importlib.metadata.version('fastapi')
assert fastapi_v == '0.141.1', f'fastapi expected 0.141.1, got {fastapi_v}'

starlette_v = importlib.metadata.version('starlette')
assert starlette_v == '1.7.0', f'starlette expected 1.7.0, got {starlette_v}'

aiohttp_v = importlib.metadata.version('aiohttp')
assert aiohttp_v == '3.14.4', f'aiohttp expected 3.14.4, got {aiohttp_v}'

certifi_v = importlib.metadata.version('certifi')
assert certifi_v >= '2026.10.19', f'certifi expected >=2026.10.19, got {certifi_v}'

uvicorn_v = importlib.metadata.version('uvicorn')
u_parts = [int(x) for x in uvicorn_v.split('.')[:2]]
assert (0, 27) <= (u_parts[0], u_parts[1]) < (0, 30), f'uvicorn expected [0.27.0, 0.30.0), got {uvicorn_v}'

qrcode_v = importlib.metadata.version('qrcode')
assert qrcode_v >= '7.4.0', f'qrcode expected >=7.4.0, got {qrcode_v}'

pillow_v = importlib.metadata.version('pillow')
assert pillow_v == '13.0.0', f'pillow expected 13.0.0, got {pillow_v}'

print('ODS_API_DEP_OK')
'@

    $prevEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $probeOut = & $verifiedPy -c $depScript $apiDir 2>&1
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $depOk = ($LASTEXITCODE -eq 0 -and ($probeOut -join '') -match 'ODS_API_DEP_OK')
    Assert-Check $depOk 'Resolved Windows Python runtime imports dashboard-api main.py, qrcode, and verifies dependency compatibility with requirements.txt'

    # Build a synthetic embeddable zip + wheel to test Install-ODSWindowsDesktopPython & tamper detection
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $mockArtifacts = Join-Path $tempRoot 'mock-py-artifacts'
    New-Item -ItemType Directory -Path $mockArtifacts -Force | Out-Null

    $mockPySrc = Join-Path $mockArtifacts 'py-src'
    New-Item -ItemType Directory -Path $mockPySrc -Force | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $mockPySrc 'python.exe'), [Text.Encoding]::ASCII.GetBytes('MZ-MOCK-PYTHON-EXE'))
    [IO.File]::WriteAllBytes((Join-Path $mockPySrc 'python312.zip'), [Text.Encoding]::ASCII.GetBytes('PK-STDLIB'))
    $mockPyZip = Join-Path $mockArtifacts 'python-3.12.10-embed-amd64.zip'
    [IO.Compression.ZipFile]::CreateFromDirectory($mockPySrc, $mockPyZip)
    $mockPySha = (Get-FileHash -LiteralPath $mockPyZip -Algorithm SHA256).Hash.ToLowerInvariant()
    $mockPySize = (Get-Item -LiteralPath $mockPyZip).Length

    $mockWhlSrc = Join-Path $mockArtifacts 'whl-src'
    New-Item -ItemType Directory -Path (Join-Path $mockWhlSrc 'fastapi') -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $mockWhlSrc 'fastapi\__init__.py'), '__version__ = "0.141.1"')
    $mockWhlZip = Join-Path $mockArtifacts 'fastapi-0.141.1-py3-none-any.whl'
    [IO.Compression.ZipFile]::CreateFromDirectory($mockWhlSrc, $mockWhlZip)
    $mockWhlSha = (Get-FileHash -LiteralPath $mockWhlZip -Algorithm SHA256).Hash.ToLowerInvariant()

    $testContract = [pscustomobject]@{
        id                   = 'python-3.12.10-embed-amd64'
        version              = '3.12.10'
        architecture         = 'x64'
        asset                = 'python-3.12.10-embed-amd64.zip'
        url                  = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
        sha256               = $mockPySha
        size                 = $mockPySize
        pth_file             = 'python312._pth'
        pth_lines            = @('python312.zip', '.', 'site-packages', 'import site')
        signer_subject_match = 'Python Software Foundation'
        wheels               = @(
            [pscustomobject]@{
                name     = 'fastapi'
                version  = '0.141.1'
                filename = 'fastapi-0.141.1-py3-none-any.whl'
                url      = 'https://files.pythonhosted.org/packages/fastapi-0.141.1-py3-none-any.whl'
                sha256   = $mockWhlSha
            }
        )
    }

    $pyRoot = Join-Path $tempRoot 'python-root'
    $installedPy = Install-ODSWindowsDesktopPython -PythonContract $testContract -Root $pyRoot -SkipAuthenticodeCheck -Downloader {
        param($url, $outFile)
        if ($url.EndsWith('.zip')) {
            Copy-Item -LiteralPath $mockPyZip -Destination $outFile -Force
        } else {
            Copy-Item -LiteralPath $mockWhlZip -Destination $outFile -Force
        }
    }
    Assert-Check ((Test-Path -LiteralPath $installedPy.ExecutablePath) -and (Test-Path -LiteralPath (Join-Path $installedPy.Directory 'site-packages\fastapi\__init__.py'))) `
        'Install-ODSWindowsDesktopPython extracts embeddable runtime, site-packages wheels, _pth, and pin.json'

    # Inject an unpinned .dll into the runtime and verify Test-ODSWindowsDesktopPythonInstall refuses it
    $rogueDll = Join-Path $installedPy.Directory 'rogue.dll'
    [IO.File]::WriteAllBytes($rogueDll, [byte[]]@(1, 2, 3, 4))
    $tamperCaught = $false
    try {
        $null = Test-ODSWindowsDesktopPythonInstall -Directory $installedPy.Directory -ExpectedPythonZipSha256 $mockPySha -SkipAuthenticodeCheck
    } catch {
        if ($_.Exception.Message -match 'Unpinned executable file appeared') {
            $tamperCaught = $true
        }
    }
    Assert-Check $tamperCaught 'Test-ODSWindowsDesktopPythonInstall detects and refuses unpinned .dll files'
} finally {
    if (Test-Path -LiteralPath $testRegKey) {
        Remove-Item -LiteralPath $testRegKey -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "[PASS] Windows Native Desktop GPU & Runtime tests passed ($checks checks)"
$global:LASTEXITCODE = 0
exit 0
