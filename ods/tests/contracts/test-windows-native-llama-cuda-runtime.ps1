# Native llama.cpp CUDA runtime for NVIDIA GPUs on Windows on Arm (RTX Spark):
# the pin, the two-archive install, the CUDA device pick, launch options,
# exit hints, classified qualification failures and the GPU generation check.
# Nothing routes to this runtime yet; the AMD (Vulkan) behaviour it shares is
# pinned here with golden strings as well. Downloads, llama-server.exe, HTTP,
# ports, tasks and processes are fixtures and only temporary files are written.
# Real-hardware outputs come from tests/fixtures/rtx-spark/llama-cpp-b11429.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $root 'installers/windows/lib/wsl-portal-amd.ps1')
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$fixtures = Join-Path (Join-Path (Join-Path $root 'tests') 'fixtures') (Join-Path 'rtx-spark' 'llama-cpp-b11429')
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}
function Get-FailureClass([scriptblock]$Action) {
    try { & $Action; return '' } catch { return [string]$_.Exception.Data['class'] }
}
function Write-Host { param([Parameter(ValueFromRemainingArguments = $true)]$Text) $script:output += ,([string]($Text -join ' ')) }
function Read-Fixture([string]$Name) { return [IO.File]::ReadAllText((Join-Path $fixtures $Name)) }
function Get-ODSPortalUserSid([string]$UserId) { if ($UserId) { return $UserId }; return 'S-1-5-21-1000-1000-1000-1001' }
function Get-ODSNativeReasoningArgs { param($Executable, $Mode, $FallbackFormat) return @('--reasoning', 'off') }
$noPolicy = [pscustomobject]@{ SmartAppControl = 'off'; UserModeCodeIntegrity = 'off' }
$sacOn = [pscustomobject]@{ SmartAppControl = 'on'; UserModeCodeIntegrity = 'off' }
$sparkName = 'NVIDIA RTX Spark N1X (6144-core Blackwell RTX GPU)'

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-native-cuda-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$previousLocalAppData = $env:LOCALAPPDATA
$previousPort = $env:AMD_INFERENCE_PORT
$env:LOCALAPPDATA = Join-Path $fixture 'LocalAppData'
try {
    # --- Pin: nvidia.json windows_arm64, from the two real release zips ---------
    $pin = Get-ODSNativeLlamaPin -SourceRoot $root -Backend nvidia -Architecture arm64
    Check ($pin.ReleaseTag -ceq 'b11429' -and $pin.Build -eq 11429 -and $pin.Flavor -ceq 'win-cuda-13.4-arm64' -and $pin.Backend -ceq 'cuda' -and
        $pin.Architecture -ceq 'arm64' -and $pin.CudaVersion -ceq '13.4' -and $pin.PolicyKey -ceq 'windows-native-cuda') 'nvidia.json pins llama.cpp b11429 win-cuda-13.4-arm64 for NVIDIA GPUs on Windows on Arm'
    $names = @($pin.Archives | ForEach-Object { $_.Asset })
    Check (($names -join ',') -ceq 'llama-b11429-bin-win-cuda-13.4-arm64.zip,cudart-llama-bin-win-cuda-13.4-arm64.zip' -and
        @($pin.Archives | Where-Object { $_.Url -ceq "https://github.com/ggml-org/llama.cpp/releases/download/b11429/$($_.Asset)" }).Count -eq 2) 'the pin names both release archives, the llama.cpp one first, on GitHub release b11429'
    Check ($pin.Asset -ceq $names[0] -and $pin.Sha256 -ceq $pin.Archives[0].Sha256 -and $pin.Size -eq $pin.Archives[0].Size -and $pin.Url -ceq $pin.Archives[0].Url) 'Asset, Sha256, Size and Url keep describing the primary archive'
    $listing = @{}
    $current = ''
    foreach ($line in ((Read-Fixture 'zip-entries.txt') -split "`r?`n")) {
        if ($line -match '^(\S+\.zip) sha256=([0-9a-f]{64}) bytes=([0-9]+)$') {
            $current = $Matches[1]
            $listing[$current] = @{ Sha256 = $Matches[2]; Size = [long]$Matches[3]; Entries = @{} }
        } elseif ($line -match '^\s+(\S+) ([0-9]+)$') { $listing[$current].Entries[$Matches[1]] = [long]$Matches[2] }
    }
    Check (@($pin.Archives | Where-Object { $listing[$_.Asset].Sha256 -ceq $_.Sha256 -and $listing[$_.Asset].Size -eq $_.Size }).Count -eq 2) 'each archive SHA-256 and size match the zips measured on the RTX Spark (and GitHub''s digests)'
    $shipped = @{}
    foreach ($asset in $listing.Keys) {
        foreach ($entry in $listing[$asset].Entries.Keys) {
            if ($shipped.ContainsKey($entry)) { $shipped[$entry] += , $asset } else { $shipped[$entry] = @($asset) }
        }
    }
    Check ($pin.RequiredFiles.Count -eq 13 -and @($pin.RequiredFiles | Where-Object { @($shipped[$_]).Count -ne 1 }).Count -eq 0 -and
        $pin.RequiredFiles[0] -ceq 'llama-server.exe') 'every required file is shipped by exactly one of the two real zips, llama-server.exe first'
    Check (@($shipped.Keys | Where-Object { @($shipped[$_]).Count -gt 1 }).Count -eq 0) 'the two real zips share no file name, so their merge cannot collide'
    Check ($pin.MinDriverVersion -ceq '616.41' -and (@($pin.RequiredCudaArchs['12.1']) -join ',') -ceq '1210,121,121a') 'the pin needs driver 616.41 (CUDA 13.4 notes, RTX Spark) and names the sm_121 archs ods-doctor.sh accepts'
    $library = [IO.File]::ReadAllText((Join-Path $root 'config/model-library.json')) | ConvertFrom-Json
    $ids = @($library.models | ForEach-Object { [string]$_.id })
    Check (($pin.ModelLadder -join ',') -ceq 'qwen3.6-35b-a3b-ud-q4' -and ($pin.FallbackModels -join ',') -ceq 'qwen3.5-2b-q4' -and
        @(@($pin.ModelLadder) + @($pin.FallbackModels) | Where-Object { $_ -cnotin $ids }).Count -eq 0) 'the ladder and fallback ids are catalog models'
    $lock = [IO.File]::ReadAllText((Join-Path $root 'config/dependency-lock.json')) | ConvertFrom-Json
    $locked = @($lock.entries | Where-Object { $_.type -ceq 'archive' -and $_.path -ceq 'config/backends/nvidia.json' } | ForEach-Object { $_.value })
    Check ($locked.Count -eq 2 -and (($locked | Sort-Object) -join ',') -ceq ((@($pin.Archives | ForEach-Object { $_.Sha256 }) | Sort-Object) -join ',')) 'config/dependency-lock.json records exactly the two archive SHA-256s'

    # AMD keeps its pin, directory and v1 required files.
    $amd = Get-ODSNativeLlamaPin -SourceRoot $root
    Check ($amd.ReleaseTag -ceq 'b9014' -and $amd.Asset -ceq 'llama-b9014-bin-win-vulkan-x64.zip' -and $amd.Flavor -ceq 'win-vulkan-x64' -and $amd.Backend -ceq 'vulkan' -and
        $amd.Archives.Count -eq 1 -and $amd.Archives[0].Sha256 -ceq $amd.Sha256 -and ($amd.RequiredFiles -join ',') -ceq 'llama-server.exe,ggml-vulkan.dll' -and
        $amd.Source -ceq 'config/backends/amd.json') 'the AMD pin is unchanged (b9014 win-vulkan-x64 from amd.json)'
    $runtimeRoot = Get-ODSNativeLlamaRoot
    Check ((Get-ODSNativeLlamaInstallDirectory $amd) -ceq (Join-Path $runtimeRoot 'b9014-win-vulkan-x64') -and
        (Get-ODSNativeLlamaInstallDirectory $pin) -ceq (Join-Path $runtimeRoot 'b11429-win-cuda-13.4-arm64') -and
        (Get-ODSNativeLlamaInstallDirectory ([pscustomobject]@{ ReleaseTag = 'b9014' })) -ceq (Join-Path $runtimeRoot 'b9014-win-vulkan-x64')) 'runtime folders are <tag>-<flavor>; a pin without a flavor is still the Vulkan folder'
    Check ((Get-Failure { $null = Get-ODSNativeLlamaPin -SourceRoot $root -Backend amd -Architecture arm64 }) -match 'no pinned llama\.cpp build for AMD GPUs on Windows on Arm') 'there is no AMD pin for Windows on Arm'
    Check ((Get-Failure { $null = Get-ODSNativeLlamaPin -SourceRoot $root -Backend nvidia }) -match 'container in Ubuntu') 'x64 NVIDIA has no Windows llama.cpp pin (it keeps the container route)'
    Check ((Get-Failure { $null = Get-ODSNativeLlamaPin -SourceRoot $root -Backend NVIDIA -Architecture arm64 }) -ne '') 'the vendor name is case-sensitive'

    $sourceRoot = Join-Path $fixture 'source'
    $backends = Join-Path (Join-Path $sourceRoot 'config') 'backends'
    $null = New-Item -ItemType Directory -Path $backends -Force
    Check ((Get-Failure { $null = Get-ODSNativeLlamaPin -SourceRoot $sourceRoot -Backend nvidia -Architecture arm64 }) -match 'nvidia\.json is missing') 'a missing nvidia.json is refused, never replaced by a fallback'
    $contractText = [IO.File]::ReadAllText((Join-Path $root 'config/backends/nvidia.json'))
    $noPin = $contractText | ConvertFrom-Json
    $noPin.runtime.llama_server.PSObject.Properties.Remove('windows_arm64')
    [IO.File]::WriteAllText((Join-Path $backends 'nvidia.json'), ($noPin | ConvertTo-Json -Depth 10))
    Check ((Get-Failure { $null = Get-ODSNativeLlamaPin -SourceRoot $sourceRoot -Backend nvidia -Architecture arm64 }) -match 'has no runtime\.llama_server\.windows_arm64') 'an nvidia.json without the Windows on Arm pin is refused'
    $nonAscii = 'ggml-' + [char]0x00E7 + '.dll'
    $badPins = [ordered]@{
        'a non-bNNNN tag' = { param($p) $p.release_tag = 'latest' }
        'an x64 CUDA flavor' = { param($p) $p.flavor = 'win-cuda-13.4-x64' }
        'the Vulkan flavor as archives' = { param($p) $p.flavor = 'win-vulkan-x64'; $p.backend = 'vulkan'
            $p.archives[0].asset = 'llama-b11429-bin-win-vulkan-x64.zip'; $p.archives[1].asset = 'cudart-llama-bin-win-vulkan-x64.zip' }
        'a backend that is not the flavor''s' = { param($p) $p.backend = 'vulkan' }
        'another policy key' = { param($p) $p.policy_key = 'nvidia' }
        'no archives' = { param($p) $p.archives = @() }
        'three archives' = { param($p) $p.archives = @($p.archives[0], $p.archives[1], $p.archives[1]) }
        'archives in the wrong order' = { param($p) $p.archives = @($p.archives[1], $p.archives[0]) }
        'another CUDA version''s zip' = { param($p) $p.archives[0].asset = 'llama-b11429-bin-win-cuda-12.4-arm64.zip' }
        'an uppercase SHA-256' = { param($p) $p.archives[1].sha256 = $p.archives[1].sha256.ToUpperInvariant() }
        'a size in quotes' = { param($p) $p.archives[0].size = '145270624' }
        'a size under 1 MiB' = { param($p) $p.archives[1].size = 1000 }
        'required files without llama-server.exe' = { param($p) $p.required_files = @($p.required_files | Where-Object { $_ -cne 'llama-server.exe' }) }
        'a required file twice' = { param($p) $p.required_files = @($p.required_files) + 'GGML.dll' }
        'a non-ASCII required file' = { param($p) $p.required_files = @($p.required_files) + $nonAscii }
        'no minimum driver' = { param($p) $p.PSObject.Properties.Remove('min_driver_version') }
        'no CUDA arch map' = { param($p) $p.cuda_archs_by_compute = [pscustomobject]@{} }
        'an sm_ arch name' = { param($p) $p.cuda_archs_by_compute.'12.1' = @('sm_121') }
        'a ladder id with a space' = { param($p) $p.model_ladder = @('Qwen 3') }
    }
    foreach ($case in $badPins.Keys) {
        $contract = $contractText | ConvertFrom-Json
        & $badPins[$case] $contract.runtime.llama_server.windows_arm64
        [IO.File]::WriteAllText((Join-Path $backends 'nvidia.json'), ($contract | ConvertTo-Json -Depth 10))
        Check ((Get-Failure { $null = Get-ODSNativeLlamaPin -SourceRoot $sourceRoot -Backend nvidia -Architecture arm64 }) -match 'pin must name') "a pin with $case is refused"
    }
    [IO.File]::WriteAllText((Join-Path $backends 'nvidia.json'), $contractText)
    Check ((Get-ODSNativeLlamaPin -SourceRoot $sourceRoot -Backend nvidia -Architecture arm64).Sha256 -ceq $pin.Sha256) 'the unmodified pin round-trips through a copy of nvidia.json (the refusals above are the mutations)'

    # --- Install: two archives, size and SHA-256 before extraction, one merge ----
    function New-FixtureZip([string]$Path, [hashtable]$Entries) {
        if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Force }
        $archive = [IO.Compression.ZipFile]::Open($Path, [IO.Compression.ZipArchiveMode]::Create)
        try {
            foreach ($name in ($Entries.Keys | Sort-Object)) {
                $writer = [IO.StreamWriter]::new($archive.CreateEntry($name).Open())
                try { $writer.Write([string]$Entries[$name]) } finally { $writer.Dispose() }
            }
        } finally { $archive.Dispose() }
    }
    $script:sources = @{}
    $script:downloads = [Collections.Generic.List[string]]::new()
    $script:expanded = [Collections.Generic.List[string]]::new()
    function Invoke-ODSNativeLlamaDownload([string]$Url, [string]$Destination) {
        $script:downloads.Add($Url)
        Copy-Item -LiteralPath $script:sources[$Url] -Destination $Destination
    }
    $script:realExpand = ${function:Expand-ODSNativeLlamaArchive}
    function Expand-ODSNativeLlamaArchive([string]$ZipPath, [string]$Destination) {
        $script:expanded.Add([IO.Path]::GetFileName($ZipPath))
        & $script:realExpand $ZipPath $Destination
    }
    $required = @('llama-server.exe', 'llama-server-impl.dll', 'ggml-cuda.dll', 'cudart64_13.dll', 'cublasLt64_13.dll')
    $llamaEntries = @{ 'llama-server.exe' = 'server'; 'llama-server-impl.dll' = 'impl'; 'ggml-cuda.dll' = 'cuda kernels'; 'ggml.dll' = 'ggml'; 'LICENSE' = 'MIT' }
    $cudartEntries = @{ 'cudart64_13.dll' = 'cudart'; 'cublas64_13.dll' = 'cublas'; 'cublasLt64_13.dll' = 'cublasLt'; 'LICENSE' = 'MIT' }
    $llamaZip = Join-Path $fixture 'llama-b11429-bin-win-cuda-13.4-arm64.zip'
    $cudartZip = Join-Path $fixture 'cudart-llama-bin-win-cuda-13.4-arm64.zip'
    function New-CudaPin([string[]]$Required = $required) {
        $archives = @(foreach ($path in @($llamaZip, $cudartZip)) {
            $asset = [IO.Path]::GetFileName($path)
            $url = "https://github.com/ggml-org/llama.cpp/releases/download/b11429/$asset"
            $script:sources[$url] = $path
            [pscustomobject]@{ Asset = $asset; Sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
                Size = (Get-Item -LiteralPath $path).Length; Url = $url }
        })
        return [pscustomobject]@{ ReleaseTag = 'b11429'; Build = 11429; Flavor = 'win-cuda-13.4-arm64'; Backend = 'cuda'; Architecture = 'arm64'
            CudaVersion = '13.4'; Asset = $archives[0].Asset; Sha256 = $archives[0].Sha256; Size = $archives[0].Size; Url = $archives[0].Url
            Source = 'fixture'; Archives = $archives; RequiredFiles = $Required }
    }
    function Get-RootEntries { return @(Get-ChildItem -LiteralPath $runtimeRoot -Force -ErrorAction SilentlyContinue) }
    New-FixtureZip $llamaZip $llamaEntries
    New-FixtureZip $cudartZip $cudartEntries
    $cudaPin = New-CudaPin
    $wrong = New-CudaPin
    $wrong.Archives[1].Size = $wrong.Archives[1].Size + 1
    $message = Get-Failure { $null = Install-ODSNativeLlamaRuntime -Pin $wrong }
    Check ($message -match 'cudart-llama-bin-win-cuda-13\.4-arm64\.zip is \d+ bytes, not the pinned' -and ($script:expanded -join ',') -ceq 'llama-b11429-bin-win-cuda-13.4-arm64.zip') 'a size mismatch of the second archive is refused before that archive is extracted'
    $script:expanded.Clear()
    $wrong = New-CudaPin
    $wrong.Archives[0].Sha256 = 'b' * 64
    $wrong.Sha256 = 'b' * 64
    $message = Get-Failure { $null = Install-ODSNativeLlamaRuntime -Pin $wrong }
    Check ($message -match 'llama-b11429-bin-win-cuda-13\.4-arm64\.zip does not match its pinned SHA-256' -and $script:expanded.Count -eq 0) 'a SHA-256 mismatch is refused before any extraction'
    $wrong = New-CudaPin
    $wrong.Sha256 = 'c' * 64
    Check ((Get-Failure { $null = Install-ODSNativeLlamaRuntime -Pin $wrong }) -match 'llama\.cpp archive first') 'a pin whose primary SHA-256 is not its first archive is refused'
    Check ((Get-RootEntries).Count -eq 0) 'refused downloads publish nothing and leave no staging directory'
    New-FixtureZip $cudartZip (@{ 'cudart64_13.dll' = 'cudart'; 'cublasLt64_13.dll' = 'cublasLt'; 'ggml.dll' = 'a different ggml' })
    $message = Get-Failure { $null = Install-ODSNativeLlamaRuntime -Pin (New-CudaPin) }
    Check ($message -match 'archives disagree on ggml\.dll' -and $message -match 'nothing was installed' -and (Get-RootEntries).Count -eq 0) 'two archives shipping different files under one name are refused'
    New-FixtureZip $cudartZip (@{ 'cudart64_13.dll' = 'cudart' })
    $message = Get-Failure { $null = Install-ODSNativeLlamaRuntime -Pin (New-CudaPin) }
    Check ($message -match 'have no cublasLt64_13\.dll' -and $message -match 'win-cuda-13\.4-arm64 build' -and (Get-RootEntries).Count -eq 0) 'a missing required file from either archive is refused'

    New-FixtureZip $cudartZip $cudartEntries
    $cudaPin = New-CudaPin
    $script:downloads.Clear()
    $installed = Install-ODSNativeLlamaRuntime -Pin $cudaPin
    $cudaDirectory = Join-Path $runtimeRoot 'b11429-win-cuda-13.4-arm64'
    Check ($installed.Directory -ceq $cudaDirectory -and $installed.ExecutablePath -ceq (Join-Path $cudaDirectory 'llama-server.exe') -and
        $installed.ZipSha256 -ceq $cudaPin.Sha256 -and $installed.Flavor -ceq 'win-cuda-13.4-arm64' -and $script:downloads.Count -eq 2) 'both archives are downloaded and published as b11429-win-cuda-13.4-arm64'
    $record = [IO.File]::ReadAllText((Join-Path $cudaDirectory 'pin.json')) | ConvertFrom-Json
    $paths = @($record.files | ForEach-Object { $_.path } | Sort-Object)
    Check ($record.schemaVersion -eq 2 -and $record.releaseTag -ceq 'b11429' -and $record.flavor -ceq 'win-cuda-13.4-arm64' -and $record.backend -ceq 'cuda' -and
        $record.zipSha256 -ceq $cudaPin.Sha256 -and (@($record.archives | ForEach-Object { "$($_.asset)|$($_.sha256)|$($_.size)" }) -join ',') -ceq
        (@($cudaPin.Archives | ForEach-Object { "$($_.Asset)|$($_.Sha256)|$($_.Size)" }) -join ',') -and
        (@($record.requiredFiles) -join ',') -ceq ($required -join ',')) 'pin.json v2 records the flavor, both archives and the required files'
    $expectedPaths = @(@($llamaEntries.Keys) + @($cudartEntries.Keys) | Sort-Object -Unique)
    Check (($paths -join ',') -ceq ($expectedPaths -join ',') -and @($record.files | Where-Object { $_.sha256 -cnotmatch '^[0-9a-f]{64}$' }).Count -eq 0) 'every file of both archives is recorded once with its SHA-256 (the identical LICENSE is kept once)'
    Check (@(Get-RootEntries | Where-Object { $_.Name -like '.staging-*' }).Count -eq 0) 'the staging directory is removed after publication'
    $null = Install-ODSNativeLlamaRuntime -Pin $cudaPin
    Check ($script:downloads.Count -eq 2) 'a verified CUDA runtime is reused without downloading again'

    # --- pin.json v2 is re-verified, and v1 (Vulkan) still verifies --------------
    $verified = Test-ODSNativeLlamaInstall -Directory $cudaDirectory -ExpectedZipSha256 $cudaPin.Sha256 -ExpectedReleaseTag 'b11429' -ExpectedArchives $cudaPin.Archives
    Check ($verified.FileCount -eq $expectedPaths.Count) 'an intact CUDA runtime verifies against its pin.json and pinned archives'
    $otherArchives = @($cudaPin.Archives[0], [pscustomobject]@{ Asset = $cudaPin.Archives[1].Asset; Sha256 = 'd' * 64 })
    Check ((Get-Failure { $null = Test-ODSNativeLlamaInstall -Directory $cudaDirectory -ExpectedArchives $otherArchives }) -match 'different archives') 'a runtime from another CUDA runtime archive is not accepted for this pin'
    [IO.File]::WriteAllText((Join-Path $cudaDirectory 'gpu-check.log'), 'check output')
    $null = Test-ODSNativeLlamaInstall -Directory $cudaDirectory
    Check $true 'the GPU check log beside the runtime does not invalidate it'
    [IO.File]::WriteAllText((Join-Path $cudaDirectory 'cudnn64_9.dll'), 'planted')
    Check ((Get-Failure { $null = Test-ODSNativeLlamaInstall -Directory $cudaDirectory }) -match 'unpinned executable') 'an unpinned DLL beside the CUDA runtime is refused'
    Remove-Item -LiteralPath (Join-Path $cudaDirectory 'cudnn64_9.dll')
    $pinText = [IO.File]::ReadAllText((Join-Path $cudaDirectory 'pin.json'))
    $tampered = @(
        @{ Name = 'required files without llama-server.exe'; Edit = { param($r) $r.requiredFiles = @($r.requiredFiles | Where-Object { $_ -cne 'llama-server.exe' }) } },
        @{ Name = 'the Vulkan flavor'; Edit = { param($r) $r.flavor = 'win-vulkan-x64'; $r.backend = 'vulkan' } },
        @{ Name = 'a primary SHA-256 that is not the first archive'; Edit = { param($r) $r.zipSha256 = $r.archives[1].sha256 } },
        @{ Name = 'schema version 3'; Edit = { param($r) $r.schemaVersion = 3 } }
    )
    foreach ($case in $tampered) {
        $copy = $pinText | ConvertFrom-Json
        & $case.Edit $copy
        [IO.File]::WriteAllText((Join-Path $cudaDirectory 'pin.json'), ($copy | ConvertTo-Json -Depth 5))
        Check ((Get-Failure { $null = Test-ODSNativeLlamaInstall -Directory $cudaDirectory }) -match 'pin\.json is invalid') "a pin.json with $($case.Name) is refused"
    }
    [IO.File]::WriteAllText((Join-Path $cudaDirectory 'pin.json'), $pinText)
    Remove-Item -LiteralPath (Join-Path $cudaDirectory 'cublasLt64_13.dll')
    Check ((Get-Failure { $null = Test-ODSNativeLlamaInstall -Directory $cudaDirectory }) -match 'cublasLt64_13\.dll is missing \(antivirus quarantine') 'a quarantined CUDA runtime DLL is refused with guidance'
    $script:downloads.Clear()
    $repaired = Install-ODSNativeLlamaRuntime -Pin $cudaPin
    Check ($script:downloads.Count -eq 2 -and $repaired.FileCount -eq $expectedPaths.Count -and
        @(Get-RootEntries | Where-Object { $_.Name -like '.damaged-b11429-*' }).Count -eq 1) 'a damaged CUDA runtime is set aside for diagnosis and downloaded again'

    $vulkanZip = Join-Path $fixture 'llama-b9014-bin-win-vulkan-x64.zip'
    New-FixtureZip $vulkanZip @{ 'llama-server.exe' = 'vulkan server'; 'ggml-vulkan.dll' = 'vulkan' }
    $vulkanUrl = 'https://github.com/ggml-org/llama.cpp/releases/download/b9014/llama-b9014-bin-win-vulkan-x64.zip'
    $script:sources[$vulkanUrl] = $vulkanZip
    $vulkanPin = [pscustomobject]@{ ReleaseTag = 'b9014'; Build = 9014; Asset = 'llama-b9014-bin-win-vulkan-x64.zip'
        Sha256 = (Get-FileHash -LiteralPath $vulkanZip -Algorithm SHA256).Hash.ToLowerInvariant(); Size = (Get-Item -LiteralPath $vulkanZip).Length
        Url = $vulkanUrl; Source = 'fixture' }
    $vulkan = Install-ODSNativeLlamaRuntime -Pin $vulkanPin
    $vulkanRecord = [IO.File]::ReadAllText((Join-Path $vulkan.Directory 'pin.json')) | ConvertFrom-Json
    Check ($vulkan.Directory -ceq (Join-Path $runtimeRoot 'b9014-win-vulkan-x64') -and $vulkanRecord.schemaVersion -eq 1 -and
        (@($vulkanRecord.PSObject.Properties | ForEach-Object { $_.Name }) -join ',') -ceq 'schemaVersion,releaseTag,asset,zipSha256,zipSize,source,files') 'a Vulkan pin still writes pin.json v1 with the same fields in the same order'
    Check ((Test-ODSNativeLlamaInstall -Directory $vulkan.Directory -ExpectedZipSha256 $vulkanPin.Sha256).Flavor -ceq 'win-vulkan-x64') 'a v1 Vulkan runtime still verifies'
    Check ((Get-Failure { $null = Test-ODSNativeLlamaInstall -Directory $vulkan.Directory -ExpectedArchives $cudaPin.Archives }) -match 'different archives') 'a v1 runtime never satisfies a multi-archive pin'

    $old = Join-Path $runtimeRoot 'b11000-win-cuda-13.4-arm64'
    $previous = Join-Path $runtimeRoot 'b11200-win-cuda-13.4-arm64'
    $foreign = Join-Path $runtimeRoot 'b11429-win-cuda-13.4-x64'
    $notes = Join-Path $runtimeRoot 'user-notes'
    foreach ($directory in @($old, $previous, $foreign, $notes)) { $null = New-Item -ItemType Directory -Path $directory }
    Remove-ODSNativeLlamaOldRuntimes -KeepDirectory $cudaDirectory -PreviousDirectory $previous
    Check ((Test-Path -LiteralPath $cudaDirectory) -and (Test-Path -LiteralPath $previous) -and -not (Test-Path -LiteralPath $old) -and
        -not (Test-Path -LiteralPath $vulkan.Directory) -and (Test-Path -LiteralPath $foreign) -and (Test-Path -LiteralPath $notes) -and
        @(Get-RootEntries | Where-Object { $_.Name -like '.damaged-*' }).Count -eq 0) 'cleanup keeps the active and previous runtime, removes older CUDA and Vulkan copies and never unknown folders'

    # --- Qualification: the b11429 version line, CUDA devices, classified throws -
    $script:probe = @{}
    function Invoke-ODSNativeLlamaProbe([string]$ExecutablePath, [string[]]$Arguments, [int]$TimeoutSeconds = 60) {
        $result = $script:probe[$Arguments[0]]
        if (-not $result) { throw "unexpected probe $($Arguments -join ' ')" }
        return $result
    }
    function New-ProbeResult([int]$ExitCode, [string]$Output) {
        [pscustomobject]@{ Started = $true; ExitCode = $ExitCode; Output = $Output; TimedOut = $false; StartError = ''; NativeError = 0 }
    }
    $exe = Join-Path (Join-Path $fixture 'bin') 'llama-server.exe'
    $script:probe['--version'] = New-ProbeResult 0 (Read-Fixture 'llama-server-version.txt')
    $script:probe['--list-devices'] = New-ProbeResult 0 (Read-Fixture 'llama-list-devices-cuda.txt')
    $cudaQualify = @{ ExecutablePath = $exe; ExpectedBuild = 11429; AdapterName = $sparkName; CodeIntegrity = $noPolicy; Backend = 'cuda'; Architecture = 'arm64' }
    $qualified = Test-ODSNativeLlamaQualification @cudaQualify
    Check ($qualified.Build -eq 11429 -and $qualified.Commit -ceq 'd81235049' -and $qualified.Device.Name -ceq 'CUDA0' -and
        $qualified.Device.TotalMiB -eq 22950 -and $qualified.Device.FreeMiB -eq 22551 -and $qualified.Backend -ceq 'cuda') 'the RTX Spark build qualifies: "version: 0.6.0-dev (build 11429, ...)" and CUDA0 from its real device list'
    $vulkanQualify = @{ ExecutablePath = $exe; ExpectedBuild = 11429; AdapterName = $sparkName; CodeIntegrity = $noPolicy }
    Check ($null -eq (Test-ODSNativeLlamaQualification @vulkanQualify).Device) 'the Vulkan route never takes a CUDA device'
    $container = @(ConvertFrom-ODSNativeLlamaDeviceList (Read-Fixture 'llama-list-devices-container.txt'))
    Check ($container.Count -eq 1 -and $container[0].Name -ceq 'CUDA0' -and $container[0].FreeMiB -eq 11738 -and
        $container[0].Description -ceq $sparkName) 'the container build''s device list parses the same way'
    $script:probe['--version'] = New-ProbeResult 0 "version: 9014 (d4b0c22f)`nbuilt with Clang 19.1.5 for Windows x86_64"
    Check ((Test-ODSNativeLlamaQualification -ExecutablePath $exe -ExpectedBuild 9014 -AdapterName $sparkName -CodeIntegrity $noPolicy -Backend cuda).Commit -ceq 'd4b0c22f') 'the b9014 version line still parses'
    $script:probe['--version'] = New-ProbeResult 0 'version: 0.6.0-dev (build 11430, commit abcdef123)'
    Check ((Get-FailureClass { $null = Test-ODSNativeLlamaQualification @cudaQualify }) -ceq 'build' -and
        (Get-Failure { $null = Test-ODSNativeLlamaQualification @cudaQualify }) -ceq 'llama-server.exe reports build 11430, not the pinned b11429.') 'another build is refused with class build'
    $script:probe['--version'] = New-ProbeResult 0 'version: unknown'
    Check ((Get-FailureClass { $null = Test-ODSNativeLlamaQualification @cudaQualify }) -ceq 'build') 'a version without a build number has class build'
    $classCases = @(
        @{ Probe = (New-ProbeResult -1073741515 ''); Policy = $noPolicy; Class = 'dll'; Pattern = 'Redistributable for Arm64.+vc_redist\.arm64\.exe.+Microsoft\.VCRedist\.2015\+\.arm64$'; Name = '0xC0000135 names the Arm64 Visual C++ runtime' },
        @{ Probe = (New-ProbeResult -1073740760 ''); Policy = $noPolicy; Class = 'policy'; Pattern = '0xC0000428'; Name = '0xC0000428 is a code-integrity block' },
        @{ Probe = (New-ProbeResult -1073741819 ''); Policy = $noPolicy; Class = 'exit'; Pattern = 'NVIDIA graphics driver with the NVIDIA App'; Name = '0xC0000005 names the NVIDIA driver' },
        @{ Probe = (New-ProbeResult -1073741701 ''); Policy = $noPolicy; Class = 'exit'; Pattern = 'not built for this processor \(Arm64\)'; Name = '0xC000007B names the processor' },
        @{ Probe = [pscustomobject]@{ Started = $false; ExitCode = $null; Output = ''; TimedOut = $false; StartError = 'blocked'; NativeError = 4551 }; Policy = $noPolicy; Class = 'policy'; Pattern = 'Smart App Control or Windows Defender Application Control'; Name = 'a start blocked by policy (4551)' },
        @{ Probe = [pscustomobject]@{ Started = $false; ExitCode = $null; Output = ''; TimedOut = $false; StartError = 'denied'; NativeError = 5 }; Policy = $sacOn; Class = 'policy'; Pattern = 'Smart App Control is on'; Name = 'a failed start with Smart App Control on' },
        @{ Probe = [pscustomobject]@{ Started = $false; ExitCode = $null; Output = ''; TimedOut = $false; StartError = 'gone'; NativeError = 2 }; Policy = $sacOn; Class = 'start'; Pattern = 'removed by antivirus'; Name = 'a missing file stays a start failure even with Smart App Control on' },
        @{ Probe = [pscustomobject]@{ Started = $true; ExitCode = $null; Output = ''; TimedOut = $true; StartError = ''; NativeError = 0 }; Policy = $noPolicy; Class = 'timeout'; Pattern = 'did not finish within 60 seconds'; Name = 'a hung --version' }
    )
    foreach ($case in $classCases) {
        $script:probe['--version'] = $case.Probe
        $arguments = @{ ExecutablePath = $exe; ExpectedBuild = 11429; AdapterName = $sparkName; CodeIntegrity = $case.Policy; Backend = 'cuda'; Architecture = 'arm64' }
        $class = Get-FailureClass { $null = Test-ODSNativeLlamaQualification @arguments }
        $message = Get-Failure { $null = Test-ODSNativeLlamaQualification @arguments }
        Check ($class -ceq $case.Class -and $message -match $case.Pattern) "$($case.Name): class $($case.Class)"
    }
    $script:probe['--version'] = New-ProbeResult 0 (Read-Fixture 'llama-server-version.txt')
    $script:probe['--list-devices'] = New-ProbeResult -1073741502 ''
    Check ((Get-FailureClass { $null = Test-ODSNativeLlamaQualification @cudaQualify }) -ceq 'exit' -and
        (Get-Failure { $null = Test-ODSNativeLlamaQualification @cudaQualify }) -match '^llama-server\.exe --list-devices failed\. A DLL failed to initialize \(0xC0000142\)\. Restart Windows, update the NVIDIA graphics driver') 'a failed --list-devices is classified and names the NVIDIA driver'
    # The AMD messages stay byte-identical and now carry a class.
    $script:probe['--version'] = New-ProbeResult -1073741515 ''
    $amdQualify = @{ ExecutablePath = $exe; ExpectedBuild = 9014; CodeIntegrity = $noPolicy }
    Check ((Get-Failure { $null = Test-ODSNativeLlamaQualification @amdQualify }) -ceq ('llama-server.exe --version failed with exit code -1073741515. A DLL it needs is missing (0xC0000135), most likely the Microsoft Visual C++ 2015-2022 Redistributable (x64). ' +
        'Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe or with: winget install Microsoft.VCRedist.2015+.x64') -and
        (Get-FailureClass { $null = Test-ODSNativeLlamaQualification @amdQualify }) -ceq 'dll') 'the AMD 0xC0000135 message is unchanged'

    # --- Exit hints: Vulkan on x64 is byte-identical, Arm64 and NVIDIA differ ----
    # Copied from origin/main: the AMD route's hints, byte for byte.
    $golden = @(
        @(-1073741515, 'A DLL it needs is missing (0xC0000135), most likely the Microsoft Visual C++ 2015-2022 Redistributable (x64). Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe or with: winget install Microsoft.VCRedist.2015+.x64'),
        @(-1073740760, 'Windows code integrity refused one of its files (0xC0000428). Smart App Control, Windows Defender Application Control or another allow-list policy blocks the unsigned llama.cpp binaries.'),
        @(-1073741701, 'Windows reports a bad image (0xC000007B): the download is damaged or not built for x64.'),
        @(-1073741502, 'A DLL failed to initialize (0xC0000142). Restart Windows, update the AMD graphics driver, then rerun setup.'),
        @(-1073741819, 'It crashed (0xC0000005), usually inside the graphics driver. Update the AMD Adrenalin driver from amd.com/support, then rerun setup.'),
        @(0, ''),
        @(1, '')
    )
    foreach ($pair in $golden) {
        Check ((Get-ODSNativeLlamaExitHint $pair[0]) -ceq $pair[1] -and (Get-ODSNativeLlamaExitHint $pair[0] 'vulkan' 'x64') -ceq $pair[1]) "the Vulkan x64 exit hint for $($pair[0]) is unchanged"
    }
    Check ((Get-ODSNativeLlamaExitHint -1073740760 'cuda' 'arm64') -ceq $golden[1][1] -and (Get-ODSNativeLlamaExitHint 0 'cuda' 'arm64') -ceq '') 'the code-integrity hint is the same on every flavor, and a clean exit has none'
    Check ((Get-ODSNativeLlamaExitHint -1073741819 'cuda' 'arm64') -notmatch 'AMD' -and (Get-ODSNativeLlamaExitHint -1073741502 'cuda' 'arm64') -notmatch 'AMD' -and
        (Get-ODSNativeLlamaExitHint -1073741515 'cuda' 'arm64') -notmatch 'x64' -and (Get-ODSNativeLlamaExitHint -1073741701 'cuda' 'arm64') -notmatch 'x64') 'Arm64 and NVIDIA hints never name AMD drivers or the x64 runtime'
    $script:output = @()
    $exited = [pscustomobject]@{ HasExited = $true; ExitCode = -1073741515 }
    Check ((Get-Failure { Wait-ODSNativeLlamaStartup -Port 18080 -Process $exited -TimeoutSeconds 60 -Backend cuda -Architecture arm64 }) -match 'Redistributable for Arm64') 'the launcher''s startup wait explains exits for the backend and architecture it is given'

    # --- CUDA device pick: name match, a single device, never a guess ------------
    $two = @(ConvertFrom-ODSNativeLlamaDeviceList ("Available devices:`n  CUDA0: $sparkName (22950 MiB, 22551 MiB free)`n" +
        "  CUDA1: NVIDIA RTX PRO 6000 Blackwell Workstation Edition (97887 MiB, 97000 MiB free)"))
    Check ((Select-ODSNativeLlamaCudaDevice -Devices $two -AdapterName $sparkName).Name -ceq 'CUDA0' -and
        (Select-ODSNativeLlamaCudaDevice -Devices $two -AdapterName 'NVIDIA RTX PRO 6000 Blackwell').Name -ceq 'CUDA1') 'the detected adapter is matched by name among several CUDA devices'
    Check ($null -eq (Select-ODSNativeLlamaCudaDevice -Devices $two -AdapterName 'NVIDIA NPU') -and $null -eq (Select-ODSNativeLlamaCudaDevice -Devices $two -AdapterName '')) 'two CUDA devices without a name match are never guessed, and the NPU name matches none'
    $single = @(ConvertFrom-ODSNativeLlamaDeviceList (Read-Fixture 'llama-list-devices-cuda.txt'))
    Check ((Select-ODSNativeLlamaCudaDevice -Devices $single -AdapterName 'NVIDIA RTX Spark').Name -ceq 'CUDA0' -and
        (Select-ODSNativeLlamaDevice -Devices $single -AdapterName 'Other name' -Backend cuda).Name -ceq 'CUDA0') 'a single CUDA device is accepted when the driver names it differently'
    $notCuda = @(ConvertFrom-ODSNativeLlamaDeviceList "  Vulkan0: NVIDIA NPU (1024 MiB, 1024 MiB free)`n  RPC0: NVIDIA RTX Spark N1X (22950 MiB, 22551 MiB free)")
    Check ($null -eq (Select-ODSNativeLlamaCudaDevice -Devices $notCuda -AdapterName $sparkName)) 'only CUDAn devices count; an NPU or other device is never the CUDA device'

    # --- Launch options and arguments ---------------------------------------------
    $keyPath = Join-Path (Join-Path $fixture 'runtime') 'api-key'
    $modelsDir = Join-Path $fixture 'models dir'
    $plan = [pscustomobject]@{ ExecutablePath = (Join-Path $cudaDirectory 'llama-server.exe'); Port = 18080
        ModelsDir = $modelsDir; ContextSize = 65536; GgufFile = 'Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf' }
    $amdOptions = [pscustomobject]@{ schemaVersion = 1; Device = 'Vulkan1'; NGpuLayers = 'auto'; ApiKeyPath = $keyPath
        LogPath = (Join-Path (Join-Path $fixture 'runtime') 'llama-server.log'); ReleaseTag = 'b9014'; ZipSha256 = ('a' * 64)
        ReasoningArguments = @('--reasoning', 'off'); ExtraArguments = @() }
    $amdExpected = @('--model', (Join-Path $modelsDir $plan.GgufFile), '--alias', $plan.GgufFile, '--host', '127.0.0.1', '--port', '18080',
        '--ctx-size', '65536', '--parallel', '1', '--n-gpu-layers', 'auto', '--device', 'Vulkan1', '--metrics', '--no-webui',
        '--api-key-file', $keyPath, '--log-file', $amdOptions.LogPath, '--reasoning', 'off')
    Check ((@(New-ODSNativeLlamaLaunchArguments $plan $amdOptions) -join '|') -ceq ($amdExpected -join '|')) 'AMD options without Backend or Architecture launch with exactly the arguments they always had'
    $amdOptions | Add-Member -NotePropertyName Backend -NotePropertyValue 'vulkan'
    Check ((@(New-ODSNativeLlamaLaunchArguments $plan $amdOptions) -join '|') -ceq ($amdExpected -join '|')) 'an explicit vulkan backend changes nothing (no --fit)'
    $cudaOptions = [pscustomobject]@{ schemaVersion = 1; Device = 'CUDA0'; NGpuLayers = 'all'; ApiKeyPath = $keyPath; LogPath = $amdOptions.LogPath
        ReleaseTag = 'b11429'; ZipSha256 = $cudaPin.Sha256; ReasoningArguments = @('--reasoning', 'off'); ExtraArguments = @()
        Backend = 'cuda'; Architecture = 'arm64' }
    $cudaArguments = @(New-ODSNativeLlamaLaunchArguments $plan $cudaOptions)
    $fit = [array]::IndexOf($cudaArguments, '--fit')
    Check ($fit -gt 0 -and $cudaArguments[$fit + 1] -ceq 'off' -and $cudaArguments[$fit - 2] -ceq '--log-file' -and
        ($cudaArguments -join '|') -match '\|--n-gpu-layers\|all\|--device\|CUDA0\|' -and $cudaArguments[-2] -ceq '--reasoning') 'CUDA launches put every layer on CUDA0 with --fit off after the log file'
    $plan | Add-Member -NotePropertyName MmprojFile -NotePropertyValue 'mmproj-F16.gguf'
    $vision = @(New-ODSNativeLlamaLaunchArguments $plan $cudaOptions)
    Check ([array]::IndexOf($vision, '--fit') -lt [array]::IndexOf($vision, '--mmproj')) 'a CUDA vision plan keeps --fit off before the projector'
    $plan.PSObject.Properties.Remove('MmprojFile')
    $invalid = @(
        @{ Device = 'Vulkan0'; Backend = 'cuda'; Architecture = 'arm64'; NGpuLayers = 'all' },
        @{ Device = 'none'; Backend = 'cuda'; Architecture = 'arm64'; NGpuLayers = '0' },
        @{ Device = 'CUDA0'; Backend = 'vulkan'; Architecture = 'x64'; NGpuLayers = 'auto' },
        @{ Device = 'CUDA0'; Backend = 'CUDA'; Architecture = 'arm64'; NGpuLayers = 'all' },
        @{ Device = 'CUDA0'; Backend = 'rocm'; Architecture = 'arm64'; NGpuLayers = 'all' },
        @{ Device = 'CUDA0'; Backend = ''; Architecture = 'arm64'; NGpuLayers = 'all' },
        @{ Device = 'CUDA0'; Backend = 'cuda'; Architecture = 'arm'; NGpuLayers = 'all' },
        @{ Device = 'CUDA0,CPU'; Backend = 'cuda'; Architecture = 'arm64'; NGpuLayers = 'all' }
    )
    foreach ($case in $invalid) {
        $options = $cudaOptions.PSObject.Copy()
        foreach ($name in $case.Keys) { $options.$name = $case[$name] }
        Check ((Get-Failure { $null = New-ODSNativeLlamaLaunchArguments $plan $options }) -match 'launch options are invalid') "options with backend '$($case.Backend)', architecture '$($case.Architecture)' and device '$($case.Device)' are refused"
    }

    # The durable launcher explains an early exit for the options' backend and
    # architecture. Its fixtures stay inside this block.
    & {
        $readyPath = Join-Path (Join-Path $fixture 'runtime') 'ready.json'
        Write-ODSNativeLlamaApiKeyFile $keyPath ('a' * 64)
        function Test-ODSNativeLlamaInstall { param($Directory, $ExpectedZipSha256, $ExpectedReleaseTag) [pscustomobject]@{ Directory = $Directory } }
        function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) }
        function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) @() }
        function Get-ODSPortalOwnedProcessTree([object[]]$Roots, [object[]]$Nodes) { [pscustomobject]@{ Nodes = $Roots; Handles = @() } }
        function Start-Process { param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)
            $script:launched = [string]$ArgumentList
            $child = [pscustomobject]@{ Id = 4243; Handle = 1; HasExited = $true; ExitCode = -1073741515; StartTime = (Get-Date); ExitTime = (Get-Date) }
            $child | Add-Member -MemberType ScriptMethod -Name Dispose -Value { }
            return $child
        }
        $message = Get-Failure { $null = Invoke-ODSNativeLlamaRuntime $plan $cudaOptions $readyPath }
        Check ($message -match 'llama-server exited during startup with code -1073741515\. A DLL it needs is missing \(0xC0000135\), most likely the Microsoft Visual C\+\+ 2015-2022 Redistributable for Arm64' -and
            $script:launched -match '"--device" "CUDA0" "--metrics" "--no-webui" "--api-key-file" "[^"]+" "--log-file" "[^"]+" "--fit" "off" "--reasoning" "off"$') 'the launcher starts CUDA with --fit off and explains its exit with the Arm64 runtime'
    }

    # --- Portal: options, task description, cutover parameter, port wording ------
    $runtime = [pscustomobject]@{ ExecutablePath = (Join-Path $cudaDirectory 'llama-server.exe'); ReleaseTag = 'b11429'; ZipSha256 = $cudaPin.Sha256; Directory = $cudaDirectory }
    $portalDir = Get-ODSPortalRuntimeDir
    $amdQualification = [pscustomobject]@{ Device = [pscustomobject]@{ Name = 'Vulkan0' } }
    $amdJson = (New-ODSPortalRuntimeOptions $runtime $amdQualification) | ConvertTo-Json -Depth 4 -Compress
    $amdGolden = [ordered]@{ schemaVersion = 1; Device = 'Vulkan0'; NGpuLayers = 'auto'; ApiKeyPath = (Join-Path $portalDir 'api-key')
        LogPath = (Join-Path $portalDir 'llama-server.log'); ReleaseTag = 'b11429'; ZipSha256 = $cudaPin.Sha256
        ReasoningArguments = @('--reasoning', 'off'); ExtraArguments = @(); PlanFeatures = @('MmprojFile') } | ConvertTo-Json -Depth 4 -Compress
    Check ($amdJson -ceq $amdGolden) 'the AMD runtime-options.json is byte-identical (no Backend or Architecture keys)'
    $cudaPortalOptions = New-ODSPortalRuntimeOptions $runtime ([pscustomobject]@{ Device = $single[0] }) 'cuda' 'arm64'
    $roundTrip = ($cudaPortalOptions | ConvertTo-Json -Depth 4 -Compress) | ConvertFrom-Json
    Assert-ODSNativeLlamaOptions $roundTrip
    Check ($roundTrip.Backend -ceq 'cuda' -and $roundTrip.Architecture -ceq 'arm64' -and $roundTrip.NGpuLayers -ceq 'all' -and $roundTrip.Device -ceq 'CUDA0' -and
        $roundTrip.schemaVersion -eq 1) 'CUDA runtime options name the backend and architecture, all layers and CUDA0, and the launcher accepts them'
    Check ((Get-Failure { $null = New-ODSPortalRuntimeOptions $runtime $amdQualification 'rocm' }) -match 'Unknown llama\.cpp runtime options') 'an unknown backend is refused'

    $script:descriptions = @()
    function New-ScheduledTaskTrigger { param([switch]$AtLogOn, $User) [pscustomobject]@{ AtLogOn = [bool]$AtLogOn; User = $User } }
    function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, $ExecutionTimeLimit, $RestartCount, $RestartInterval) @{} }
    function New-ODSInteractiveScheduledTaskPrincipal { param($RunLevel) [pscustomobject]@{ RunLevel = $RunLevel } }
    function Register-ScheduledTask { param($TaskName, $TaskPath, $Action, $Trigger, $Settings, $Principal, $Description, [switch]$Force) $script:descriptions += $Description }
    $null = Register-ODSPortalRuntimeTask ([pscustomobject]@{ Action = 'fixture' })
    $null = Register-ODSPortalRuntimeTask ([pscustomobject]@{ Action = 'fixture' }) $script:ODSPortalRuntimeTaskDescriptions['cuda']
    Check ($script:descriptions[0] -ceq 'ODS: llama.cpp model server for the AMD GPU (127.0.0.1, API key). Starts at sign-in.' -and
        $script:descriptions[1] -ceq 'ODS: llama.cpp model server for the NVIDIA GPU (127.0.0.1, API key). Starts at sign-in.') 'the task keeps the AMD description unless the NVIDIA one is passed'
    $script:cutoverDescriptions = @()
    function New-ODSPortalRuntimeAction($Plan, $Options, [string]$ApiKey, [string]$WslDistro = '', [string]$WslInstallDir = '') { [pscustomobject]@{ Plan = $Plan } }
    function Register-ODSPortalRuntimeTask($Registration, [string]$Description = '') { $script:cutoverDescriptions += ,$Description; return 'fixture-task' }
    function Start-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) }
    function Wait-ODSPortalRuntimeReady($Registration, [int]$Seconds = 1020) { return 'model' }
    $none = [pscustomobject]@{ Kind = 'none'; Launches = @() }
    # The cutover takes the model-change lock and re-reads what ODS runs; this
    # case is only about the task description, so both see nothing installed.
    function Enter-ODSPortalRuntimeChange([hashtable]$Lock) { }
    function Exit-ODSPortalRuntimeChange([hashtable]$Lock) { }
    function Get-ODSPortalExistingRuntime { return $none }
    $null = Invoke-ODSPortalRuntimeCutover -Existing $none -Plan $plan -Options $cudaOptions -ApiKey ('a' * 64) -KeepPort $false
    $null = Invoke-ODSPortalRuntimeCutover -Existing $none -Plan $plan -Options $cudaOptions -ApiKey ('a' * 64) -KeepPort $false -TaskDescription $script:ODSPortalRuntimeTaskDescriptions['cuda']
    Check ($script:cutoverDescriptions.Count -eq 2 -and $script:cutoverDescriptions[0] -ceq '' -and $script:cutoverDescriptions[1] -cmatch 'NVIDIA GPU') 'the cutover passes its task description through, empty (AMD) by default'

    $script:owners = @{}
    $script:blocked = @()
    function Get-ODSPortalPortOwner([int]$Port) { return $script:owners[$Port] }
    function Test-ODSPortalPortBindable([int]$Port) { return $Port -notin $script:blocked }
    $script:owners = @{ 8080 = 'Other' }
    $owned = Get-Failure { Assert-ODSPortalPortAvailable 8080 }
    $script:owners = @{}; $script:blocked = @(8080)
    $reserved = Get-Failure { Assert-ODSPortalPortAvailable 8080 }
    $script:owners = @{ 8080 = 'a'; 18080 = 'b'; 28080 = 'c' }; $script:blocked = @()
    $env:AMD_INFERENCE_PORT = ''
    $noPort = Get-Failure { $null = Select-ODSPortalRuntimePort }
    Check ($owned -match "^Port 8080 is used by 'Other'\. Choose a free port with the AMD_INFERENCE_PORT environment variable \(ODS uses that name for its Windows model server on every GPU\), then rerun\.$" -and
        $reserved -match 'reserved after a restart\).+AMD_INFERENCE_PORT environment variable.+every GPU' -and
        $noPort -match '8080 \(a\).+28080 \(c\).+AMD_INFERENCE_PORT environment variable.+every GPU') 'port messages explain that AMD_INFERENCE_PORT names the model server port on every GPU'
    $script:owners = @{}
    Check ((Select-ODSPortalCheckPort) -eq 18090) 'the GPU check uses port 18090 when it is free'
    $script:owners = @{ 18090 = 'Other' }; $script:blocked = @(28090)
    Check ((Select-ODSPortalCheckPort) -eq 38090) 'a busy or unbindable check port moves to the next quiet one, never a model-server port'
    $script:owners = @{ 18090 = 'a'; 38090 = 'c' }
    Check ((Get-Failure { $null = Select-ODSPortalCheckPort }) -match '18090 \(a\), 28090 \(reserved or unavailable for binding\), 38090 \(c\)') 'no free check port names every candidate'
    $script:owners = @{}; $script:blocked = @()

    # --- CUDA targets: the doctor's line, the pin's names, derived names ---------
    $bad = @(Get-ODSNativeLlamaCudaArchs (Read-Fixture 'cuda-archs-bad.txt'))
    $gb10 = @(Get-ODSNativeLlamaCudaArchs (Read-Fixture 'cuda-archs-gb10-sm121a.txt'))
    # Synthesized from ggml-cuda-targets.txt (sm_86, sm_89, sm_120a, sm_121a).
    $sparkLog = 'system_info: n_threads = 20 (n_threads_batch = 20) / 20 | CUDA : ARCHS = 860,890,1200,1210 | USE_GRAPHS = 1 |'
    Check (($bad -join ',') -ceq '500,610,700,750,800,860,890,1200' -and -not (Test-ODSNativeLlamaCudaArchsCover $bad '12.1' $pin.RequiredCudaArchs)) 'the GB10 CUDA 12.8 image''s archs do not cover compute capability 12.1'
    Check (($gb10 -join ',') -ceq '1210' -and (Test-ODSNativeLlamaCudaArchsCover $gb10 '12.1' $pin.RequiredCudaArchs)) 'a 121a build reports 1210, which covers 12.1'
    Check ((Test-ODSNativeLlamaCudaArchsCover @(Get-ODSNativeLlamaCudaArchs $sparkLog) '12.1' $pin.RequiredCudaArchs) -and
        (Test-ODSNativeLlamaCudaArchsCover @('121a') '12.1' $pin.RequiredCudaArchs) -and -not (Test-ODSNativeLlamaCudaArchsCover @('120a', '1200') '12.1' $pin.RequiredCudaArchs)) 'sm_121 in any accepted spelling covers 12.1; sm_120 alone does not'
    Check (@(Get-ODSNativeLlamaCudaArchs 'no system info here').Count -eq 0 -and
        (@(Get-ODSNativeLlamaCudaArchs "CUDA : ARCHS = 500 |`nCUDA : ARCHS = 860, 1210 |") -join ',') -ceq '860,1210') 'a missing line gives no archs, and the last line wins'
    Check (((Get-ODSNativeLlamaRequiredCudaArchs '12.1' $null) -join ',') -ceq ($pin.RequiredCudaArchs['12.1'] -join ',') -and
        (Test-ODSNativeLlamaCudaArchsCover @('860') '8.6' $pin.RequiredCudaArchs) -and (Test-ODSNativeLlamaCudaArchsCover @('1220') '12.2' $pin.RequiredCudaArchs) -and
        -not (Test-ODSNativeLlamaCudaArchsCover @('1210') '12.2' $pin.RequiredCudaArchs)) 'a compute capability the pin does not list is checked by its own arch, not refused by a list'

    # --- GPU generation check: stub server, real-hardware answers ----------------
    $model = Join-Path (Join-Path $fixture 'models') 'Qwen3.5-2B-Q4_K_M.gguf'
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $model) -Force
    [IO.File]::WriteAllText($model, 'gguf')
    $checkLog = Join-Path $cudaDirectory 'gpu-check.log'
    $script:listening = @{}
    function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) if ($script:listening[[int]$LocalPort]) { [pscustomobject]@{ LocalPort = $LocalPort } } }
    function Start-Sleep { param($Seconds, $Milliseconds) }
    function New-FakeServer([int]$ExitCode = 0, [bool]$Exited = $false, [bool]$KeepsPort = $false) {
        $server = [pscustomobject]@{ Id = 4242; Handle = 1; HasExited = $Exited; ExitCode = $ExitCode; Killed = $false; Disposed = $false; KeepsPort = $KeepsPort }
        $server | Add-Member -MemberType ScriptMethod -Name Kill -Value { $this.Killed = $true; $this.HasExited = $true; if (-not $this.KeepsPort) { $script:listening.Clear() } }
        $server | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($Milliseconds) return $this.HasExited }
        $server | Add-Member -MemberType ScriptMethod -Name Dispose -Value { $this.Disposed = $true }
        return $server
    }
    function Start-Process { param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru, $ErrorAction)
        $script:started = [string]$ArgumentList
        $script:keyFiles = @(Get-ChildItem -LiteralPath $cudaDirectory -Filter 'check-api-key-*' | ForEach-Object { $_.FullName })
        $script:keyText = if ($script:keyFiles.Count -eq 1) { [IO.File]::ReadAllText($script:keyFiles[0]) } else { '' }
        if ($script:startError) { throw $script:startError }
        [IO.File]::WriteAllText($checkLog, $script:serverLog)
        if (-not $script:server.HasExited) { $script:listening[$script:checkPort] = $true }
        return $script:server
    }
    function Invoke-ODSNativeLlamaHttp { param([int]$Port, [string]$Path, [string]$ApiKey = '', [int]$TimeoutMilliseconds = 5000, [string]$Method = 'GET', [string]$JsonBody = '')
        if ($Path -eq '/health') { return [pscustomobject]@{ StatusCode = $script:health; Body = ''; Error = '' } }
        $script:chatRequest = [pscustomobject]@{ Port = $Port; Path = $Path; ApiKey = $ApiKey; Method = $Method; Body = $JsonBody }
        if ($script:crashOnAnswer) {
            $script:server.HasExited = $true; $script:server.ExitCode = $script:crashOnAnswer; $script:listening.Clear()
            return [pscustomobject]@{ StatusCode = 0; Body = ''; Error = 'ConnectFailure' }
        }
        return $script:chat
    }
    function Reset-Check([string]$Answer = 'generation-good.json', [string]$Log = $sparkLog) {
        $script:checkPort = 18090
        $script:listening = @{}
        $script:health = 200
        $script:server = New-FakeServer
        $script:startError = $null
        $script:crashOnAnswer = 0
        $script:serverLog = $Log
        $script:chatRequest = $null
        $script:chat = [pscustomobject]@{ StatusCode = 200; Body = (Read-Fixture $Answer); Error = '' }
    }
    $checkArguments = @{ ExecutablePath = $runtime.ExecutablePath; ModelPath = $model; Device = 'CUDA0'; Port = 18090; LogPath = $checkLog
        ComputeCap = '12.1'; ArchsByCompute = $pin.RequiredCudaArchs; ReasoningArguments = @('--reasoning', 'off'); Architecture = 'arm64'; CodeIntegrity = $noPolicy }
    Reset-Check
    $good = Invoke-ODSNativeLlamaGenerationProbe @checkArguments
    $body = $script:chatRequest.Body | ConvertFrom-Json
    Check ($good.Passed -and $good.Content -ceq '4' -and $good.Class -ceq '' -and ($good.Archs -join ',') -ceq '860,890,1200,1210') 'the RTX Spark answer "4" with sm_121 targets passes the GPU check'
    Check ($script:started -match '"--host" "127\.0\.0\.1" "--port" "18090" "--ctx-size" "4096" "--parallel" "1" "--n-gpu-layers" "all" "--device" "CUDA0" "--fit" "off" "--no-webui"' -and
        $script:started -match '"--api-key-file" "[^"]+check-api-key-[0-9a-f]{32}"' -and $script:started -match '"--reasoning" "off"$' -and
        $script:started.Contains('"' + $model + '"')) 'the check server is loopback-only with 4096 tokens, one slot, every layer on CUDA0, --fit off and thinking off'
    Check ($script:keyFiles.Count -eq 1 -and $script:keyText -cmatch '^[0-9a-f]{64}$' -and -not $script:started.Contains($script:keyText) -and
        $script:chatRequest.ApiKey -ceq $script:keyText -and -not (Test-Path -LiteralPath $script:keyFiles[0])) 'its API key lives in a private file only for the check, is sent as the header, and never reaches the command line'
    Check ($script:chatRequest.Method -ceq 'POST' -and $script:chatRequest.Path -ceq '/v1/chat/completions' -and $body.messages[0].content -ceq 'What is 2+2? Answer with the number only.' -and
        $body.temperature -eq 0 -and $body.max_tokens -eq 8 -and $body.messages.Count -eq 1) 'it asks "What is 2+2?" at temperature 0 with 8 tokens'
    Check ($script:server.Killed -and $script:server.Disposed -and $script:listening.Count -eq 0) 'the check server is stopped through its own handle and its port is free again'
    Reset-Check 'generation-bad.json'
    $result = Invoke-ODSNativeLlamaGenerationProbe @checkArguments
    Check (-not $result.Passed -and $result.Class -ceq 'generation' -and $result.Reason -match 'answered "\?\?\?\?\?\?\?\?" to a test question \(expected 4\)' -and
        $result.Reason -match 'Update the NVIDIA driver') 'the GB10 "????" answer fails as a generation problem with the driver step'
    Reset-Check 'generation-good.json' 'system_info: n_threads = 20 | USE_GRAPHS = 1 |'
    $result = Invoke-ODSNativeLlamaGenerationProbe @checkArguments
    Check (-not $result.Passed -and $result.Class -ceq 'build' -and $result.Reason -match "no 'CUDA : ARCHS =' line" -and $result.Content -ceq '4') 'a build that does not report its CUDA targets is refused even when it answers 4'
    Reset-Check 'generation-good.json' (Read-Fixture 'cuda-archs-bad.txt')
    $result = Invoke-ODSNativeLlamaGenerationProbe @checkArguments
    Check (-not $result.Passed -and $result.Class -ceq 'build' -and $result.Reason -match 'not for this GPU \(compute capability 12\.1\)' -and
        $result.Reason -match 'ODS did not run a model with it') 'a build without sm_121 is refused before its answer is considered'
    Reset-Check 'generation-good.json' (Read-Fixture 'cuda-archs-bad.txt')
    $checkArguments.ComputeCap = ''
    $result = Invoke-ODSNativeLlamaGenerationProbe @checkArguments
    Check ($result.Passed) 'without a compute capability only the answer is checked'
    $checkArguments.ComputeCap = '12.1'
    Reset-Check 'context-overflow-400.json'
    $script:chat = [pscustomobject]@{ StatusCode = 400; Body = (Read-Fixture 'context-overflow-400.json'); Error = '' }
    $result = Invoke-ODSNativeLlamaGenerationProbe @checkArguments
    Check ($result.Class -ceq 'generation' -and $result.Reason -match 'HTTP 400 .+exceeds the available context size') 'an HTTP error answer names the status and llama.cpp''s own message'
    Reset-Check
    $script:startError = [ComponentModel.Win32Exception]::new(4551)
    $result = Invoke-ODSNativeLlamaGenerationProbe @checkArguments
    Check ($result.Class -ceq 'policy' -and $result.Reason -match 'Smart App Control or Windows Defender Application Control' -and
        @(Get-ChildItem -LiteralPath $cudaDirectory -Filter 'check-api-key-*').Count -eq 0) 'a start blocked by policy is classified and leaves no key file'
    Reset-Check
    $script:startError = [ComponentModel.Win32Exception]::new(2)
    Check ((Invoke-ODSNativeLlamaGenerationProbe @checkArguments).Class -ceq 'start') 'a server that cannot start for another reason has class start'
    Reset-Check
    $script:server = New-FakeServer -ExitCode -1073741515 -Exited $true
    $result = Invoke-ODSNativeLlamaGenerationProbe @checkArguments
    Check ($result.Class -ceq 'dll' -and $result.Reason -match 'exited during the GPU check with code -1073741515\. .+Redistributable for Arm64') 'a server that exits at once with 0xC0000135 is class dll with the Arm64 runtime step'
    Reset-Check
    $script:health = 503
    $result = Invoke-ODSNativeLlamaGenerationProbe @checkArguments -TimeoutSeconds 0
    Check ($result.Class -ceq 'timeout' -and $script:server.Killed -and $script:listening.Count -eq 0) 'a model that never loads times out and the server is stopped'
    Reset-Check
    $script:crashOnAnswer = -1073741819
    $result = Invoke-ODSNativeLlamaGenerationProbe @checkArguments
    Check ($result.Class -ceq 'exit' -and $result.Reason -match '0xC0000005.+NVIDIA graphics driver') 'a crash while answering is classified by its exit code'
    Reset-Check
    $script:listening[18090] = $true
    Check ((Get-Failure { $null = Invoke-ODSNativeLlamaGenerationProbe @checkArguments }) -match 'Port 18090 is already in use') 'a busy check port stops before any server starts'
    Reset-Check
    $script:server = New-FakeServer -KeepsPort $true
    $message = Get-Failure { $null = Invoke-ODSNativeLlamaGenerationProbe @checkArguments }
    Check ($message -match 'Port 18090 is still in use after the llama\.cpp GPU check stopped' -and
        @(Get-ChildItem -LiteralPath $cudaDirectory -Filter 'check-api-key-*').Count -eq 0) 'a port still held after the stop is an error, and the key file is still removed'
    Reset-Check
    $vulkanDevice = $checkArguments.Clone(); $vulkanDevice.Device = 'Vulkan0'
    Check ((Get-Failure { $null = Invoke-ODSNativeLlamaGenerationProbe @vulkanDevice }) -match 'needs a CUDA device') 'the check runs only on a CUDA device'

    # --- Driver minimum, before any download --------------------------------------
    $null = Assert-ODSNativeLlamaDriver $pin '616.62'
    $null = Assert-ODSNativeLlamaDriver $pin ' 616.41 '
    $null = Assert-ODSNativeLlamaDriver $amd ''
    Check $true 'the RTX Spark driver 616.62 (and 616.41) passes, and the AMD pin has no driver minimum'
    $olderDriver = Get-Failure { Assert-ODSNativeLlamaDriver $pin '616.40' }
    Check ($olderDriver -ceq ('The NVIDIA driver (616.40) is older than 616.41, which the llama.cpp CUDA 13.4 build needs. Update it with the NVIDIA App or from nvidia.com, ' +
        'restart Windows and rerun setup. Do not install NVIDIA drivers inside Ubuntu. Nothing was downloaded or changed.') -and
        (Get-FailureClass { Assert-ODSNativeLlamaDriver $pin '580.97' }) -ceq 'driver') 'an older driver is refused with the update steps (class driver)'
    Check ((Get-Failure { Assert-ODSNativeLlamaDriver $pin '' }) -match 'could not read the NVIDIA driver version' -and
        (Test-ODSNativeLlamaDriverVersion '1000.10' '616.41') -and -not (Test-ODSNativeLlamaDriverVersion '616.4x' '616.41')) 'an unreadable driver version is refused; versions compare as numbers'

    # --- The whole qualification: build, CUDA device, then the GPU check --------
    $script:probe['--version'] = New-ProbeResult 0 (Read-Fixture 'llama-server-version.txt')
    $script:probe['--list-devices'] = New-ProbeResult 0 "ggml_cuda_init: found 0 CUDA devices`nAvailable devices:"
    function Invoke-ODSNativeLlamaGenerationProbe { throw 'The GPU check must not run without a CUDA device.' }
    $noDevice = Invoke-ODSPortalCudaQualification -Pin $pin -Runtime $runtime -AdapterName $sparkName -ComputeCap '12.1' -TestModelPath $model -CodeIntegrity $noPolicy
    Check (-not $noDevice.Passed -and $noDevice.Class -ceq 'device' -and $noDevice.Reason -match 'found no NVIDIA CUDA GPU \(it lists: no devices\)\. The NVIDIA NPU is not used by ODS') 'no CUDA device is class device with the driver step, before any GPU check'
    $script:probe['--list-devices'] = New-ProbeResult 0 (Read-Fixture 'llama-list-devices-cuda.txt')
    $script:qualifiedCheck = $null
    function Invoke-ODSNativeLlamaGenerationProbe { param($ExecutablePath, $ModelPath, $Device, $Port, $LogPath, $ComputeCap, $ArchsByCompute, $ReasoningArguments, $Architecture, $CodeIntegrity)
        $script:qualifiedCheck = [pscustomobject]@{ Device = $Device; Port = $Port; LogPath = $LogPath; ComputeCap = $ComputeCap; Map = $ArchsByCompute
            Reasoning = ($ReasoningArguments -join ' '); Architecture = $Architecture; ModelPath = $ModelPath }
        return [pscustomobject]@{ Passed = $true; Content = '4'; Archs = @('1210'); Class = ''; Reason = '' } }
    $passed = Invoke-ODSPortalCudaQualification -Pin $pin -Runtime $runtime -AdapterName $sparkName -ComputeCap '12.1' -TestModelPath $model -CodeIntegrity $noPolicy
    Check ($passed.Passed -and $passed.Qualification.Device.Name -ceq 'CUDA0' -and $script:qualifiedCheck.Device -ceq 'CUDA0' -and $script:qualifiedCheck.Port -eq 18090 -and
        $script:qualifiedCheck.LogPath -ceq (Join-Path $cudaDirectory 'gpu-check.log') -and $script:qualifiedCheck.ComputeCap -ceq '12.1' -and
        (@($script:qualifiedCheck.Map['12.1']) -join ',') -ceq '1210,121,121a' -and $script:qualifiedCheck.Reasoning -ceq '--reasoning off' -and
        $script:qualifiedCheck.Architecture -ceq 'arm64' -and $script:qualifiedCheck.ModelPath -ceq $model) 'the Portal qualification checks CUDA0 on port 18090 with the pin''s archs and the small model'
    Check ((Get-Failure { $null = Invoke-ODSPortalCudaQualification -Pin $amd -Runtime $runtime -AdapterName $sparkName -ComputeCap '12.1' -TestModelPath $model }) -match 'for CUDA llama\.cpp builds') 'the GPU check refuses a Vulkan pin'
} finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    $env:AMD_INFERENCE_PORT = $previousPort
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to clean a fixture outside the temporary directory.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks Windows native llama.cpp CUDA runtime contracts."
