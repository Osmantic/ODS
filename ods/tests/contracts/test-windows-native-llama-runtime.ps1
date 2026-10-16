# Native llama.cpp runtime for AMD GPUs on Windows (Round F): pin, acquire,
# qualify, launch arguments, API key file and readiness proof. Downloads,
# llama-server.exe and HTTP are replaced by fixtures; only temporary files are
# written. No process, task, socket or network call reaches the real system.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
# constants.ps1 is deliberately not loaded: the Portal reads its pin literals
# with the parser, which is also what lets this contract run on Linux pwsh.
. (Join-Path $root 'installers/windows/lib/native-llama-runtime.ps1')
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}
function Write-Host { param([Parameter(ValueFromRemainingArguments = $true)]$Text) $script:output += ,([string]($Text -join ' ')) }
$onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-native-llama-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$previousLocalAppData = $env:LOCALAPPDATA
$env:LOCALAPPDATA = Join-Path $fixture 'LocalAppData'
try {
    # --- Pin: amd.json first, constants.ps1 fallback, never a malformed pin ---
    $sourceRoot = Join-Path $fixture 'source'
    $backends = Join-Path (Join-Path $sourceRoot 'config') 'backends'
    $null = New-Item -ItemType Directory -Path $backends -Force
    $fallback = Get-ODSNativeLlamaPin -SourceRoot $sourceRoot
    Check ($fallback.ReleaseTag -eq 'b9014' -and $fallback.Build -eq 9014 -and $fallback.Size -eq 33541404 -and
        $fallback.Sha256 -eq '6cd4bc7a44256e674458b0c5ea2ae3461dca29ee87876c8d410ecc78652a3b0f' -and
        $fallback.Asset -eq 'llama-b9014-bin-win-vulkan-x64.zip' -and
        $fallback.Url -eq 'https://github.com/ggml-org/llama.cpp/releases/download/b9014/llama-b9014-bin-win-vulkan-x64.zip') 'without amd.json the constants pin b9014 with its GitHub size and digest'
    Set-Content -LiteralPath (Join-Path $backends 'amd.json') -Encoding UTF8 -Value '{"id":"amd","runtime":{"lemonade":{"windows_version":"10.0.0"}}}'
    Check ((Get-ODSNativeLlamaPin -SourceRoot $sourceRoot).Source -match 'constants\.ps1') 'an amd.json without runtime.llama_server.windows keeps the constants fallback'
    $amdPin = '{"id":"amd","runtime":{"llama_server":{"windows":{"release_tag":"b9100","asset":"llama-b9100-bin-win-vulkan-x64.zip","sha256":"' + ('a' * 64) + '","size":34000000}}}}'
    Set-Content -LiteralPath (Join-Path $backends 'amd.json') -Encoding UTF8 -Value $amdPin
    $fromContract = Get-ODSNativeLlamaPin -SourceRoot $sourceRoot
    Check ($fromContract.ReleaseTag -eq 'b9100' -and $fromContract.Build -eq 9100 -and $fromContract.Size -eq 34000000 -and
        $fromContract.Source -eq 'config/backends/amd.json') 'runtime.llama_server.windows in amd.json is the primary pin'
    foreach ($bad in @(
        @{ release_tag = 'latest'; asset = 'llama-latest-bin-win-vulkan-x64.zip'; sha256 = ('a' * 64); size = 34000000 },
        @{ release_tag = 'b9100'; asset = 'llama-b9100-bin-win-cpu-x64.zip'; sha256 = ('a' * 64); size = 34000000 },
        @{ release_tag = 'b9100'; asset = 'llama-b9100-bin-win-vulkan-x64.zip'; sha256 = ('A' * 64); size = 34000000 },
        @{ release_tag = 'b9100'; asset = 'llama-b9100-bin-win-vulkan-x64.zip'; sha256 = ('a' * 64); size = '34000000' })) {
        $json = @{ id = 'amd'; runtime = @{ llama_server = @{ windows = $bad } } } | ConvertTo-Json -Depth 5
        Set-Content -LiteralPath (Join-Path $backends 'amd.json') -Encoding UTF8 -Value $json
        $message = Get-Failure { $null = Get-ODSNativeLlamaPin -SourceRoot $sourceRoot }
        Check ($message -match 'pin must name') "a malformed amd.json pin is refused, not replaced by the fallback ($($bad.release_tag) $($bad.asset))"
    }
    Remove-Item -LiteralPath (Join-Path $backends 'amd.json')
    # The legacy installer loads constants.ps1 and a tier may override its tag.
    $script:LLAMA_CPP_VULKAN_SHA256 = @{ 'b9014' = '6cd4bc7a44256e674458b0c5ea2ae3461dca29ee87876c8d410ecc78652a3b0f' }
    $script:LLAMA_CPP_VULKAN_SIZE = @{ 'b9014' = 33541404 }
    $script:LLAMA_CPP_RELEASE_TAG = 'b9014'
    Check ((Get-ODSNativeLlamaPin -SourceRoot $sourceRoot).ReleaseTag -eq 'b9014') 'loaded installer constants are used as the fallback'
    $script:LLAMA_CPP_RELEASE_TAG = 'b9999'
    Check ((Get-Failure { $null = Get-ODSNativeLlamaPin -SourceRoot $sourceRoot }) -match 'No pinned SHA-256 and size') 'an unpinned tag override is refused before any download'
    Remove-Variable -Name LLAMA_CPP_RELEASE_TAG, LLAMA_CPP_VULKAN_SHA256, LLAMA_CPP_VULKAN_SIZE -Scope Script

    # --- Acquire: size and SHA-256 before extraction, traversal-safe extraction ---
    function New-FixtureZip([string]$Path, [hashtable]$Entries) {
        if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Force }
        $archive = [IO.Compression.ZipFile]::Open($Path, [IO.Compression.ZipArchiveMode]::Create)
        try {
            foreach ($name in ($Entries.Keys | Sort-Object)) {
                $entry = $archive.CreateEntry($name)
                $writer = [IO.StreamWriter]::new($entry.Open())
                try { $writer.Write([string]$Entries[$name]) } finally { $writer.Dispose() }
            }
        } finally { $archive.Dispose() }
    }
    function New-FixturePin([string]$Path, [string]$Tag = 'b9014') {
        [pscustomobject]@{ ReleaseTag = $Tag; Build = [int]$Tag.Substring(1); Asset = "llama-$Tag-bin-win-vulkan-x64.zip"
            Sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant(); Size = (Get-Item -LiteralPath $Path).Length
            Url = "https://github.com/ggml-org/llama.cpp/releases/download/$Tag/llama-$Tag-bin-win-vulkan-x64.zip"; Source = 'fixture' }
    }
    $goodEntries = @{ 'llama-server.exe' = 'fixture server'; 'ggml-vulkan.dll' = 'fixture vulkan'; 'ggml-base.dll' = 'fixture base'; 'LICENSE-llama' = 'MIT' }
    $goodZip = Join-Path $fixture 'good.zip'
    New-FixtureZip $goodZip $goodEntries
    $script:downloadSource = $goodZip
    $script:downloads = [Collections.Generic.List[string]]::new()
    $script:expansions = 0
    function Invoke-ODSNativeLlamaDownload([string]$Url, [string]$Destination) {
        $script:downloads.Add($Url)
        Copy-Item -LiteralPath $script:downloadSource -Destination $Destination
    }
    $script:realExpand = ${function:Expand-ODSNativeLlamaArchive}
    function Expand-ODSNativeLlamaArchive([string]$ZipPath, [string]$Destination) {
        $script:expansions++
        & $script:realExpand $ZipPath $Destination
    }
    $runtimeRoot = Get-ODSNativeLlamaRoot
    Check ($runtimeRoot -eq (Join-Path (Join-Path $env:LOCALAPPDATA 'ODS') 'llama.cpp')) 'the runtime lives under %LOCALAPPDATA%\ODS\llama.cpp'
    $pin = New-FixturePin $goodZip

    $wrongSize = $pin.PSObject.Copy(); $wrongSize.Size = $pin.Size + 1
    $message = Get-Failure { $null = Install-ODSNativeLlamaRuntime -Pin $wrongSize }
    Check ($message -match 'not the pinned' -and $script:expansions -eq 0) 'a size mismatch is refused before extraction'
    $wrongSha = $pin.PSObject.Copy(); $wrongSha.Sha256 = 'b' * 64
    $message = Get-Failure { $null = Install-ODSNativeLlamaRuntime -Pin $wrongSha }
    Check ($message -match 'does not match its pinned SHA-256' -and $script:expansions -eq 0) 'a SHA-256 mismatch is refused before extraction'
    Check (-not (Test-Path -LiteralPath (Get-ODSNativeLlamaInstallDirectory $pin)) -and
        @(Get-ChildItem -LiteralPath $runtimeRoot -Force).Count -eq 0) 'refused downloads publish nothing and leave no staging directory'

    $evilZip = Join-Path $fixture 'evil.zip'
    New-FixtureZip $evilZip @{ 'llama-server.exe' = 'x'; 'ggml-vulkan.dll' = 'x'; '../escaped.txt' = 'outside' }
    $script:downloadSource = $evilZip
    $message = Get-Failure { $null = Install-ODSNativeLlamaRuntime -Pin (New-FixturePin $evilZip) }
    Check ($message -match 'unsafe entry name' -and -not (Test-Path -LiteralPath (Join-Path $runtimeRoot 'escaped.txt')) -and
        -not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'ODS/escaped.txt'))) 'a parent-directory archive entry is refused and nothing lands outside staging'
    foreach ($unsafe in @('C:/rooted.txt', '/rooted.txt', 'dir/../../x.txt', 'stream.txt:ads')) {
        New-FixtureZip $evilZip @{ 'llama-server.exe' = 'x'; 'ggml-vulkan.dll' = 'x'; $unsafe = 'outside' }
        $message = Get-Failure { $null = Install-ODSNativeLlamaRuntime -Pin (New-FixturePin $evilZip) }
        Check ($message -match 'unsafe entry name') "archive entry '$unsafe' is refused"
    }
    $cpuZip = Join-Path $fixture 'cpu.zip'
    New-FixtureZip $cpuZip @{ 'llama-server.exe' = 'cpu build'; 'ggml-cpu-x64.dll' = 'cpu' }
    $script:downloadSource = $cpuZip
    $message = Get-Failure { $null = Install-ODSNativeLlamaRuntime -Pin (New-FixturePin $cpuZip) }
    Check ($message -match 'no ggml-vulkan\.dll') 'an archive without the Vulkan backend is refused'
    Check (@(Get-ChildItem -LiteralPath $runtimeRoot -Force).Count -eq 0) 'every refused archive leaves the runtime root empty'

    $script:downloadSource = $goodZip
    $script:downloads.Clear()
    $installed = Install-ODSNativeLlamaRuntime -Pin $pin
    $expectedDirectory = Join-Path $runtimeRoot 'b9014-win-vulkan-x64'
    Check ($installed.Directory -eq $expectedDirectory -and $installed.ExecutablePath -eq (Join-Path $expectedDirectory 'llama-server.exe') -and
        $installed.ReleaseTag -eq 'b9014' -and $installed.ZipSha256 -eq $pin.Sha256) 'a verified archive is published as <tag>-win-vulkan-x64'
    $record = Get-Content -LiteralPath (Join-Path $expectedDirectory 'pin.json') -Raw | ConvertFrom-Json
    $serverRecord = @($record.files | Where-Object { $_.path -eq 'llama-server.exe' })
    $serverHash = (Get-FileHash -LiteralPath (Join-Path $expectedDirectory 'llama-server.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
    Check ($record.releaseTag -eq 'b9014' -and $record.asset -eq $pin.Asset -and $record.zipSha256 -eq $pin.Sha256 -and $record.zipSize -eq $pin.Size -and
        @($record.files).Count -eq 4 -and $serverRecord.Count -eq 1 -and $serverRecord[0].sha256 -eq $serverHash) 'pin.json records the tag, asset, zip SHA-256 and a SHA-256 for every file'
    Check (@(Get-ChildItem -LiteralPath $runtimeRoot -Force | Where-Object { $_.Name -like '.staging-*' }).Count -eq 0) 'the staging directory is removed after publication'
    $null = Install-ODSNativeLlamaRuntime -Pin $pin
    Check ($script:downloads.Count -eq 1) 'a verified runtime is reused without downloading again'

    # --- pin.json is re-verified: quarantine, tampering and planted code ---
    $verified = Test-ODSNativeLlamaInstall -Directory $expectedDirectory -ExpectedZipSha256 $pin.Sha256 -ExpectedReleaseTag 'b9014'
    Check ($verified.FileCount -eq 4) 'an intact runtime verifies against its pin.json'
    Check ((Get-Failure { $null = Test-ODSNativeLlamaInstall -Directory $expectedDirectory -ExpectedZipSha256 ('c' * 64) }) -match 'different archive') 'a runtime from another archive is not accepted for this pin'
    Check ((Get-Failure { $null = Test-ODSNativeLlamaInstall -Directory $expectedDirectory -ExpectedReleaseTag 'b9100' }) -match 'not the pinned b9100') 'a runtime from another tag is not accepted for this pin'
    [IO.File]::WriteAllText((Join-Path $expectedDirectory 'llama-server.log'), 'runtime output')
    $null = Test-ODSNativeLlamaInstall -Directory $expectedDirectory
    Check $true 'unlisted non-executable files (logs) do not invalidate the runtime'
    [IO.File]::WriteAllText((Join-Path $expectedDirectory 'version.dll'), 'planted')
    Check ((Get-Failure { $null = Test-ODSNativeLlamaInstall -Directory $expectedDirectory }) -match 'unpinned executable') 'a planted DLL beside llama-server.exe is refused'
    Remove-Item -LiteralPath (Join-Path $expectedDirectory 'version.dll')
    [IO.File]::WriteAllText((Join-Path $expectedDirectory 'ggml-base.dll'), 'tampered')
    Check ((Get-Failure { $null = Test-ODSNativeLlamaInstall -Directory $expectedDirectory }) -match 'no longer matches its pinned SHA-256') 'a modified pinned file is refused'
    Remove-Item -LiteralPath (Join-Path $expectedDirectory 'ggml-base.dll')
    Check ((Get-Failure { $null = Test-ODSNativeLlamaInstall -Directory $expectedDirectory }) -match 'missing \(antivirus quarantine') 'a quarantined pinned file is refused with guidance'
    $script:downloads.Clear()
    $repaired = Install-ODSNativeLlamaRuntime -Pin $pin
    Check ($script:downloads.Count -eq 1 -and $repaired.FileCount -eq 4 -and
        @(Get-ChildItem -LiteralPath $runtimeRoot -Force | Where-Object { $_.Name -like '.damaged-b9014-*' }).Count -eq 1) 'a damaged runtime is set aside for diagnosis and downloaded again'
    $older = Join-Path $runtimeRoot 'b8000-win-vulkan-x64'
    $previous = Join-Path $runtimeRoot 'b8500-win-vulkan-x64'
    $foreign = Join-Path $runtimeRoot 'user-notes'
    foreach ($directory in @($older, $previous, $foreign)) { $null = New-Item -ItemType Directory -Path $directory }
    Remove-ODSNativeLlamaOldRuntimes -KeepDirectory $expectedDirectory -PreviousDirectory $previous
    Check ((Test-Path -LiteralPath $expectedDirectory) -and (Test-Path -LiteralPath $previous) -and (Test-Path -LiteralPath $foreign) -and
        -not (Test-Path -LiteralPath $older) -and
        @(Get-ChildItem -LiteralPath $runtimeRoot -Force | Where-Object { $_.Name -like '.damaged-*' }).Count -eq 0) 'cleanup keeps the active and previous runtime and never touches unknown folders'

    # --- Qualification: --version build, --list-devices, never silent CPU ---
    $script:probe = @{}
    function Invoke-ODSNativeLlamaProbe([string]$ExecutablePath, [string[]]$Arguments, [int]$TimeoutSeconds = 60) {
        $result = $script:probe[$Arguments[0]]
        if (-not $result) { throw "unexpected probe $($Arguments -join ' ')" }
        return $result
    }
    function New-ProbeResult([int]$ExitCode, [string]$Output) {
        [pscustomobject]@{ Started = $true; ExitCode = $ExitCode; Output = $Output; TimedOut = $false; StartError = ''; NativeError = 0 }
    }
    $noPolicy = [pscustomobject]@{ SmartAppControl = 'off'; UserModeCodeIntegrity = 'off' }
    $sacOn = [pscustomobject]@{ SmartAppControl = 'on'; UserModeCodeIntegrity = 'off' }
    $versionText = "load_backend: loaded Vulkan backend`nversion: 9014 (d4b0c22f)`nbuilt with Clang 19.1.5 for Windows x86_64"
    $deviceText = "Available devices:`n  Vulkan0: AMD Radeon(TM) 8060S Graphics (98304 MiB, 97000 MiB free)"
    $script:probe['--version'] = New-ProbeResult 0 $versionText
    $script:probe['--list-devices'] = New-ProbeResult 0 $deviceText
    $qualified = Test-ODSNativeLlamaQualification -ExecutablePath 'C:\fixture\llama-server.exe' -ExpectedBuild 9014 `
        -AdapterName 'AMD Radeon(TM) 8060S Graphics' -CodeIntegrity $noPolicy
    Check ($qualified.Build -eq 9014 -and $qualified.Device.Name -eq 'Vulkan0' -and $qualified.Device.TotalMiB -eq 98304 -and
        $qualified.Device.FreeMiB -eq 97000) 'a pinned build with the AMD Vulkan device qualifies and reports its memory'
    $script:probe['--version'] = New-ProbeResult 0 'version: 9100 (abcdef12)'
    Check ((Get-Failure { $null = Test-ODSNativeLlamaQualification -ExecutablePath 'C:\fixture\llama-server.exe' -ExpectedBuild 9014 -CodeIntegrity $noPolicy }) -match 'reports build 9100, not the pinned b9014') 'a binary reporting another build is refused'
    $script:probe['--version'] = New-ProbeResult -1073741515 ''
    $message = Get-Failure { $null = Test-ODSNativeLlamaQualification -ExecutablePath 'C:\fixture\llama-server.exe' -ExpectedBuild 9014 -CodeIntegrity $noPolicy }
    Check ($message -match '0xC0000135' -and $message -match 'Visual C\+\+ 2015-2022' -and $message -match 'vc_redist\.x64\.exe') 'exit 0xC0000135 explains the missing Visual C++ runtime'
    $script:probe['--version'] = [pscustomobject]@{ Started = $false; ExitCode = $null; Output = ''; TimedOut = $false; StartError = 'blocked'; NativeError = 4551 }
    $message = Get-Failure { $null = Test-ODSNativeLlamaQualification -ExecutablePath 'C:\fixture\llama-server.exe' -ExpectedBuild 9014 -CodeIntegrity $sacOn }
    Check ($message -match 'Smart App Control' -and $message -match 'does not change these settings') 'a code-integrity block names Smart App Control without changing it'
    $script:probe['--version'] = New-ProbeResult -1073740760 ''
    Check ((Get-Failure { $null = Test-ODSNativeLlamaQualification -ExecutablePath 'C:\fixture\llama-server.exe' -ExpectedBuild 9014 -CodeIntegrity $noPolicy }) -match '0xC0000428') 'exit 0xC0000428 explains a code-integrity refusal'
    $script:probe['--version'] = New-ProbeResult 0 $versionText
    $script:probe['--list-devices'] = New-ProbeResult 0 "Available devices:"
    $cpuOnly = Test-ODSNativeLlamaQualification -ExecutablePath 'C:\fixture\llama-server.exe' -ExpectedBuild 9014 -AdapterName 'AMD Radeon RX 9070 XT' -CodeIntegrity $sacOn
    Check ($null -eq $cpuOnly.Device -and $cpuOnly.PolicyHint -match 'Smart App Control is on') 'no Vulkan device is reported as such (callers take the CPU route with guidance), never a silent CPU launch'

    $devices = @(ConvertFrom-ODSNativeLlamaDeviceList ("Available devices:`n  Vulkan0: AMD Radeon(TM) 780M Graphics (512 MiB, 400 MiB free)`n" +
        "  Vulkan1: AMD Radeon RX 7900 XTX (24560 MiB, 24000 MiB free)`nggml_vulkan: noise line"))
    Check ($devices.Count -eq 2 -and $devices[1].Name -eq 'Vulkan1' -and $devices[1].TotalMiB -eq 24560) 'the device list is parsed from llama.cpp output and ignores log noise'
    Check ((Select-ODSNativeLlamaDevice -Devices $devices -AdapterName 'AMD Radeon RX 7900 XTX').Name -eq 'Vulkan1') 'an iGPU plus dGPU host uses the detected discrete adapter'
    Check ((Select-ODSNativeLlamaDevice -Devices $devices -AdapterName 'AMD Radeon(TM) 780M Graphics').Name -eq 'Vulkan0') 'the integrated adapter is chosen when it is the detected one'
    Check ($null -eq (Select-ODSNativeLlamaDevice -Devices $devices -AdapterName 'AMD Radeon PRO W7900')) 'two AMD devices with no name match are not guessed'
    $nvidia = @(ConvertFrom-ODSNativeLlamaDeviceList "  Vulkan0: NVIDIA GeForce RTX 4070 (12282 MiB, 11000 MiB free)")
    Check ($null -eq (Select-ODSNativeLlamaDevice -Devices $nvidia -AdapterName 'AMD Radeon RX 9070 XT')) 'another vendor GPU is never used for the AMD route'
    $software = @(ConvertFrom-ODSNativeLlamaDeviceList "  Vulkan0: llvmpipe (LLVM 17.0.6, 256 bits) (32000 MiB, 32000 MiB free)")
    Check ($null -eq (Select-ODSNativeLlamaDevice -Devices $software -AdapterName 'AMD Radeon RX 9070 XT')) 'a software Vulkan rasterizer is never treated as a GPU'
    $single = @(ConvertFrom-ODSNativeLlamaDeviceList "  Vulkan0: AMD Radeon Graphics (16384 MiB, 16000 MiB free)")
    Check ((Select-ODSNativeLlamaDevice -Devices $single -AdapterName 'AMD Radeon(TM) 890M').Name -eq 'Vulkan0') 'a single AMD Vulkan device is used when the driver names it differently'

    # --- API key file: owner-only, exactly 64 hex characters, no BOM/newline ---
    $key = New-ODSNativeLlamaApiKey
    Check ($key -cmatch '^[0-9a-f]{64}$' -and $key -ne (New-ODSNativeLlamaApiKey)) 'each generated API key is 32 random bytes in lowercase hex'
    $keyPath = Join-Path (Join-Path $fixture 'runtime') 'api-key'
    Write-ODSNativeLlamaApiKeyFile $keyPath $key
    $keyBytes = [IO.File]::ReadAllBytes($keyPath)
    Check ($keyBytes.Length -eq 64 -and $keyBytes[0] -ne 0xEF -and $keyBytes[-1] -ne 10 -and $keyBytes[-1] -ne 13 -and
        (Read-ODSNativeLlamaApiKey $keyPath) -ceq $key) 'the key file holds the key with no BOM and no trailing newline (std::getline-safe)'
    if ($onWindows) {
        $acl = Get-Acl -LiteralPath $keyPath
        $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
        Check ($acl.AreAccessRulesProtected -and $rules.Count -eq 1 -and $rules[0].IdentityReference -eq [Security.Principal.WindowsIdentity]::GetCurrent().User) 'the key file is private to the Windows user'
    }
    foreach ($bad in @(("$key`r`n"), ([string][char]0xFEFF + $key), $key.ToUpperInvariant(), $key.Substring(1))) {
        [IO.File]::WriteAllText($keyPath, $bad, [Text.UTF8Encoding]::new($false))
        Check ((Get-Failure { $null = Read-ODSNativeLlamaApiKey $keyPath }) -match 'malformed') 'a key file with CR/LF, a BOM, uppercase or a short key is refused'
    }
    Check ((Get-Failure { Write-ODSNativeLlamaApiKeyFile $keyPath 'not-a-key' }) -match '64 lowercase hex') 'a non-hex key is never written'

    # --- Launch arguments: loopback, alias, one slot, device, key file, log ---
    $modelsDir = Join-Path $fixture 'models dir'
    $runtimeDir = Join-Path $fixture 'runtime'
    $plan = [pscustomobject]@{ ExecutablePath = (Join-Path $expectedDirectory 'llama-server.exe'); Port = 18080
        ModelsDir = $modelsDir; ContextSize = 65536; GgufFile = 'Qwen3.6-35B-A3B-Q4_K_M.gguf' }
    $options = [pscustomobject]@{ schemaVersion = 1; Device = 'Vulkan1'; NGpuLayers = 'auto'
        ApiKeyPath = $keyPath; LogPath = (Join-Path $runtimeDir 'llama-server.log'); ReleaseTag = 'b9014'; ZipSha256 = $pin.Sha256
        ReasoningArguments = @('--reasoning', 'off'); ExtraArguments = @() }
    Write-ODSNativeLlamaApiKeyFile $keyPath $key
    $arguments = @(New-ODSNativeLlamaLaunchArguments $plan $options)
    $expected = @('--model', (Join-Path $modelsDir 'Qwen3.6-35B-A3B-Q4_K_M.gguf'), '--alias', 'Qwen3.6-35B-A3B-Q4_K_M.gguf',
        '--host', '127.0.0.1', '--port', '18080', '--ctx-size', '65536', '--parallel', '1', '--n-gpu-layers', 'auto',
        '--device', 'Vulkan1', '--metrics', '--no-webui', '--api-key-file', $keyPath, '--log-file', $options.LogPath,
        '--reasoning', 'off')
    Check (($arguments -join '|') -ceq ($expected -join '|')) 'launch arguments follow the contract: alias, loopback, one slot, named device, metrics, no web UI, key file and log'
    $commandLine = ConvertTo-ODSNativeLlamaArgumentString $arguments
    Check (-not $commandLine.Contains($key) -and $commandLine -notmatch '--api-key "' -and $commandLine.Contains('"' + (Join-Path $modelsDir 'Qwen3.6-35B-A3B-Q4_K_M.gguf') + '"')) 'the key never appears on the command line, and paths with spaces stay one argument'
    Check ((ConvertTo-ODSNativeLlamaArgumentString @('C:\dir\', 'a"b')) -ceq '"C:\dir\\" "a\"b"') 'arguments use the Windows CRT quoting rules'
    $options.ExtraArguments = @('--flash-attn', 'on', '--cache-type-k', 'q8_0', '--spec-type', 'ngram-mod', '--no-cache-prompt')
    Check ((@(New-ODSNativeLlamaLaunchArguments $plan $options) -join ' ') -match '--flash-attn on --cache-type-k q8_0 --spec-type ngram-mod --no-cache-prompt$') 'allow-listed tuning options are appended'
    foreach ($extra in @(@('--host', '0.0.0.0'), @('--api-key', 'x'), @('--flash-attn', 'on; calc'), @('--webui'))) {
        $options.ExtraArguments = $extra
        Check ((Get-Failure { $null = New-ODSNativeLlamaLaunchArguments $plan $options }) -match 'not allowed') "tuning option '$($extra -join ' ')' cannot widen the launch contract"
    }
    $options.ExtraArguments = @()
    $options.NGpuLayers = '99999'
    Check ((@(New-ODSNativeLlamaLaunchArguments $plan $options) -join '|') -match '\|--n-gpu-layers\|99999\|') 'any N_GPU_LAYERS count .env.schema.json allows reaches llama-server'
    $options.NGpuLayers = 'auto'
    foreach ($case in @(@{ Name = 'device'; Value = 'Vulkan0,CPU' }, @{ Name = 'device'; Value = 'none' },
            @{ Name = 'gpu'; Value = '12345678901' }, @{ Name = 'gpu'; Value = '-1' }, @{ Name = 'gpu'; Value = '8 --host 0.0.0.0' })) {
        $saved = $options.Device; $savedLayers = $options.NGpuLayers
        if ($case.Name -eq 'device') { $options.Device = $case.Value } else { $options.NGpuLayers = $case.Value }
        Check ((Get-Failure { $null = New-ODSNativeLlamaLaunchArguments $plan $options }) -match 'launch options are invalid') "an invalid $($case.Name) option ($($case.Value)) is refused"
        $options.Device = $saved; $options.NGpuLayers = $savedLayers
    }
    foreach ($gguf in @('../model.gguf', ('Model-' + [char]0x00E9 + '.gguf'), '.gguf', 'model.bin', ' model.gguf')) {
        $plan.GgufFile = $gguf
        Check ((Get-Failure { $null = New-ODSNativeLlamaLaunchArguments $plan $options }) -match 'plan is invalid') "GGUF name '$gguf' is refused (llama.cpp needs an ASCII alias)"
    }
    $plan.GgufFile = 'Qwen3.6-35B-A3B-Q4_K_M.gguf'
    $plan.ExecutablePath = 'C:\Users\x\AppData\Local\lemonade_server\bin\LemonadeServer.exe'
    Check ((Get-Failure { $null = New-ODSNativeLlamaLaunchArguments $plan $options }) -match 'plan is invalid') 'only llama-server.exe can be launched by a plan'
    $plan.ExecutablePath = Join-Path $expectedDirectory 'llama-server.exe'
    $script:shortPaths = @{}
    function Get-ODSNativeLlamaShortPath([string]$Path) { return $script:shortPaths[$Path] }
    $unicodeModels = Join-Path $fixture ('Jos' + [char]0x00E9)
    $plan.ModelsDir = $unicodeModels
    $script:shortPaths[(Join-Path $unicodeModels $plan.GgufFile)] = 'C:\Users\JOS~1\models\QWEN36~1.GGU'
    Check ((@(New-ODSNativeLlamaLaunchArguments $plan $options))[1] -ceq 'C:\Users\JOS~1\models\QWEN36~1.GGU') 'a non-ASCII model path uses its 8.3 short name (llama.cpp opens argv paths as UTF-8)'
    $script:shortPaths.Clear()
    Check ((Get-Failure { $null = New-ODSNativeLlamaLaunchArguments $plan $options }) -match 'non-ASCII characters and no 8\.3 short name') 'a non-ASCII path without a short name stops with an explanation'
    $plan.ModelsDir = $modelsDir

    # --- Vision projector (WP2): --mmproj from the same store, same name rule ---
    Check (@(New-ODSNativeLlamaLaunchArguments $plan $options) -notcontains '--mmproj') 'a plan without a projector launches without --mmproj'
    $plan | Add-Member -NotePropertyName MmprojFile -NotePropertyValue 'mmproj-F16.gguf'
    $arguments = @(New-ODSNativeLlamaLaunchArguments $plan $options)
    $at = [array]::IndexOf($arguments, '--mmproj')
    Check ($at -gt 1 -and $arguments[$at + 1] -ceq (Join-Path $modelsDir 'mmproj-F16.gguf') -and
        $arguments[1] -ceq (Join-Path $modelsDir $plan.GgufFile) -and $arguments[-2] -ceq '--reasoning') 'a planned projector loads with --mmproj beside the model, before the reasoning and tuning options'
    foreach ($projector in @('../mmproj.gguf', ('mmproj-' + [char]0x00E9 + '.gguf'), '.gguf', 'mmproj.bin', ' mmproj.gguf', $plan.GgufFile.ToUpperInvariant())) {
        $plan.MmprojFile = $projector
        Check ((Get-Failure { $null = New-ODSNativeLlamaLaunchArguments $plan $options }) -match 'invalid vision projector') "projector name '$projector' is refused"
    }
    $plan.PSObject.Properties.Remove('MmprojFile')

    # --- Readiness proof: /health, /v1/models identity, /props path and n_ctx ---
    $script:http = @{}
    $script:httpKeys = [Collections.Generic.List[string]]::new()
    function Invoke-ODSNativeLlamaHttp([int]$Port, [string]$Path, [string]$ApiKey = '', [int]$TimeoutMilliseconds = 5000) {
        $script:httpKeys.Add("${Path}:$([bool]$ApiKey)")
        if ($Path -eq '/props' -and $ApiKey -cne $script:expectedKey) { return [pscustomobject]@{ StatusCode = 401; Body = '{}'; Error = '' } }
        $value = $script:http[$Path]
        if ($value -is [scriptblock]) { $value = & $value }
        return $value
    }
    function Set-Fixture([string]$Id, [string]$ModelPath, [long]$Context, [int]$Slots = 1) {
        $script:http['/v1/models'] = [pscustomobject]@{ StatusCode = 200; Error = ''
            Body = (@{ object = 'list'; data = @(@{ id = $Id; aliases = @($Id); meta = @{ n_ctx_train = 262144 } }) } | ConvertTo-Json -Depth 5) }
        $script:http['/props'] = [pscustomobject]@{ StatusCode = 200; Error = ''
            Body = (@{ model_path = $ModelPath; total_slots = $Slots; default_generation_settings = @{ n_ctx = $Context } } | ConvertTo-Json -Depth 5) }
    }
    $script:expectedKey = $key
    $modelPath = Join-Path $modelsDir 'Qwen3.6-35B-A3B-Q4_K_M.gguf'
    $proofArgs = @{ Port = 18080; GgufFile = 'Qwen3.6-35B-A3B-Q4_K_M.gguf'; ModelPaths = @($modelPath); ContextSize = 65536; ApiKey = $key }
    Set-Fixture 'Qwen3.6-35B-A3B-Q4_K_M.gguf' $modelPath 65536
    $proof = Get-ODSNativeLlamaModelProof @proofArgs
    Check ($proof.ContextVerified -and $proof.ContextLength -eq 65536 -and $proof.ModelId -ceq 'Qwen3.6-35B-A3B-Q4_K_M.gguf') 'an exact /v1/models id, /props path and n_ctx verify the model'
    Check ($script:httpKeys -contains '/v1/models:False' -and $script:httpKeys -contains '/props:True') '/v1/models is read without credentials and /props with the API key'
    Set-Fixture 'Qwen3.6-35B-A3B-Q4_K_M.gguf' $modelPath.ToUpperInvariant() (65536 + 255)
    $proof = Get-ODSNativeLlamaModelProof @proofArgs
    Check ($proof.ContextVerified -and $proof.ContextLength -eq 65536 -and $proof.RuntimeContext -eq 65791) 'up to 255 extra cells of alignment report the requested context (path case-insensitive)'
    Set-Fixture 'Qwen3.6-35B-A3B-Q4_K_M.gguf' $modelPath (65536 + 256)
    $proof = Get-ODSNativeLlamaModelProof @proofArgs
    Check (-not $proof.ContextVerified -and $proof.ContextLength -eq 65792) 'a full 256-cell drift is not verified and reports the runtime n_ctx'
    Set-Fixture 'Qwen3.6-35B-A3B-Q4_K_M.gguf' $modelPath 32768
    $proof = Get-ODSNativeLlamaModelProof @proofArgs
    Check (-not $proof.ContextVerified -and $proof.ContextLength -eq 32768 -and $proof.Message -match 'training context is 262144') 'a capped context is not verified and names the training context'
    Set-Fixture 'Other.gguf' $modelPath 65536
    Check ((Get-Failure { $null = Get-ODSNativeLlamaModelProof @proofArgs }) -match "serves 'Other.gguf' instead") 'a different served model id is never accepted'
    Set-Fixture 'Qwen3.6-35B-A3B-Q4_K_M.gguf' 'C:\other\model.gguf' 65536
    Check ((Get-Failure { $null = Get-ODSNativeLlamaModelProof @proofArgs }) -match 'not the planned model file') 'a different model path is never accepted'
    Set-Fixture 'Qwen3.6-35B-A3B-Q4_K_M.gguf' $modelPath 65536 4
    Check ((Get-Failure { $null = Get-ODSNativeLlamaModelProof @proofArgs }) -match 'exactly one slot') 'more than one slot cannot prove the context'
    Set-Fixture 'Qwen3.6-35B-A3B-Q4_K_M.gguf' $modelPath 65536
    $script:expectedKey = 'f' * 64
    Check ((Get-Failure { $null = Get-ODSNativeLlamaModelProof @proofArgs }) -match 'rejected the ODS API key') 'a wrong API key fails the proof visibly'
    $script:expectedKey = $key
    Check ((Get-Failure { $null = Get-ODSNativeLlamaModelProof @proofArgs -Vision }) -match 'did not load the planned vision projector') 'a vision plan needs /props to report the vision modality'
    $script:http['/props'] = [pscustomobject]@{ StatusCode = 200; Error = ''
        Body = (@{ model_path = $modelPath; total_slots = 1; default_generation_settings = @{ n_ctx = 65536 }
            modalities = @{ vision = $true; audio = $false } } | ConvertTo-Json -Depth 5) }
    Check ((Get-ODSNativeLlamaModelProof @proofArgs -Vision).ContextVerified) 'a loaded projector (/props modalities.vision) verifies a vision plan'

    $script:healthSequence = [Collections.Generic.Queue[int]]::new()
    $script:http['/health'] = { [pscustomobject]@{ StatusCode = $script:healthSequence.Dequeue(); Body = ''; Error = '' } }
    function Start-Sleep { param($Seconds, $Milliseconds) }
    foreach ($code in @(0, 503, 503, 200)) { $script:healthSequence.Enqueue($code) }
    $alive = [pscustomobject]@{ HasExited = $false; ExitCode = 0 }
    Wait-ODSNativeLlamaStartup -Port 18080 -Process $alive -TimeoutSeconds 60
    Check ($script:healthSequence.Count -eq 0) 'startup waits through connection refusal and 503 (loading) until /health is 200'
    $crashed = [pscustomobject]@{ HasExited = $true; ExitCode = -1073741515 }
    $message = Get-Failure { Wait-ODSNativeLlamaStartup -Port 18080 -Process $crashed -TimeoutSeconds 60 }
    Check ($message -match 'exited during startup with code -1073741515' -and $message -match 'Visual C\+\+') 'a process that exits during startup fails at once with its exit code explained'
    Check ((Get-ODSNativeLlamaExitHint 0) -eq '') 'a clean exit carries no hint'
} finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to clean a fixture outside the temporary directory.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

# A stopped llama-server holding a large model can take far longer than five
# seconds to exit while the driver releases its memory (Strixy: the 22 GB 35B
# on the 8060S failed one model switch in three). The stop waits for it.
$slow = [pscustomobject]@{ Id = 4242; HasExited = $false; Killed = $false; LongestWait = 0 }
$slow | Add-Member -MemberType ScriptMethod -Name Kill -Value { $this.Killed = $true }
$slow | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {
    param($Milliseconds)
    if ($Milliseconds -gt $this.LongestWait) { $this.LongestWait = $Milliseconds }
    return ($Milliseconds -ge 30000)
}
$stopError = $null
try { Stop-ODSPortalOwnedProcesses @($slow) } catch { $stopError = $_.Exception.Message }
Check ($null -eq $stopError -and $slow.Killed -and $slow.LongestWait -ge 30000) 'a killed runtime that needs more than five seconds to exit does not fail the stop'
$stuck = [pscustomobject]@{ Id = 4243; HasExited = $false }
$stuck | Add-Member -MemberType ScriptMethod -Name Kill -Value { }
$stuck | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($Milliseconds) return $false }
$stopError = $null
try { Stop-ODSPortalOwnedProcesses @($stuck) } catch { $stopError = $_.Exception.Message }
Check ($stopError -match 'did not exit within 60 seconds') 'a runtime that never exits still fails the stop, after a bounded wait'
Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks Windows native llama.cpp runtime contracts."
