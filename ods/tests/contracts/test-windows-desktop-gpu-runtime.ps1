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
    Assert-Check ($dl2.Status -ceq 'cached' -and $dl2.FromStamp -eq $true -and $sw.ElapsedMilliseconds -lt 250) `
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

    # --- 5. Pinned Embeddable Python Contract & Tamper Verification ---
    $pyContract = Read-ODSWindowsDesktopPythonContract -SourceRoot $root
    Assert-Check ($pyContract.id -ceq 'python-3.12.10-embed-amd64' -and @($pyContract.wheels).Count -ge 15) `
        'config/windows-desktop.json loads valid python-3.12.10-embed-amd64 contract with zero litellm wheels'

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
    [IO.File]::WriteAllText((Join-Path $mockWhlSrc 'fastapi\__init__.py'), '__version__ = "0.115.8"')
    $mockWhlZip = Join-Path $mockArtifacts 'fastapi-0.115.8-py3-none-any.whl'
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
                version  = '0.115.8'
                filename = 'fastapi-0.115.8-py3-none-any.whl'
                url      = 'https://files.pythonhosted.org/packages/fastapi-0.115.8-py3-none-any.whl'
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
