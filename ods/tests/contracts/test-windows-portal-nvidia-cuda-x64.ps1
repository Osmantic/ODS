# Fixture-only coverage of the public Windows Portal backend selection.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-setup.ps1')
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$installerRoot = Join-Path $sourceRoot 'installers'
function Check($Value, [string]$Message) { if (-not $Value) { throw $Message }; Write-Host "PASS $Message" }
function Refuses([scriptblock]$Action, [string]$Pattern) {
    $message = ''; try { & $Action } catch { $message = $_.Exception.Message }
    Check ($message -match $Pattern) "refuses $Pattern"
}
$script:gpu = @{ Backend='nvidia'; Name='NVIDIA GeForce RTX 3060'; VramMB=12288; MemoryType='discrete'; Count=1 }
$script:architecture = 'x64'
$script:existing = [pscustomobject]@{ Kind='none' }
function Get-GpuInfo { $script:gpu }
function Get-SystemRamGB { 64 }
function Get-ODSPortalWindowsArchitecture { $script:architecture }
function Get-ODSPortalExistingRuntime { $script:existing }
function Get-ODSPortalRuntimeTask { $script:savedTask }
function Get-ODSSparkNvidiaRows { @([pscustomobject]@{Name=$script:gpu.Name; IsCudaGpu=$true; ComputeCap=$script:compute; DriverVersion=$script:driver}) }
$script:compute='8.6'; $script:driver='616.92'
function Get-ODSPortalRuntimeDir { Join-Path $script:fixture 'runtime' }
$script:fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-nvidia-route-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $script:fixture | Out-Null
try {
    Check ((Resolve-ODSPortalWindowsLlmRoute @{}) -eq '') 'default NVIDIA keeps WSL CUDA'
    $script:savedTask=@{Description='ODS: llama.cpp CUDA x64 model server for the NVIDIA GPU (127.0.0.1, API key). Starts at sign-in.'}
    Refuses { Resolve-ODSPortalWindowsLlmRoute @{} } 'missing NVIDIA CUDA metadata'
    $script:savedTask=$null
    Check ((Resolve-ODSPortalWindowsLlmRoute @{WindowsLlmBackend='cuda'}) -eq 'nvidia') 'explicit NVIDIA CUDA route'
    Refuses { Resolve-ODSPortalWindowsLlmRoute @{WindowsLlmBackend='cuda'; Cloud=$true} } 'Cloud'
    Refuses { Resolve-ODSPortalWindowsLlmRoute @{WindowsLlmBackend='cuda'; ExternalLlmUrl='http://localhost:1234'} } 'External'
    $script:architecture = 'arm64'
    Refuses { Resolve-ODSPortalWindowsLlmRoute @{WindowsLlmBackend='cuda'} } 'x64'
    $script:architecture = 'x64'; $script:gpu.Backend = 'amd'
    Refuses { Resolve-ODSPortalWindowsLlmRoute @{WindowsLlmBackend='cuda'} } 'NVIDIA'
    $script:gpu.Backend = 'nvidia'
    Check ($null -eq (Get-ODSPortalAmdPlan $sourceRoot)) 'default AMD planner still excludes NVIDIA'
    $plan = Get-ODSPortalAmdPlan $sourceRoot -GpuVendor nvidia
    Check ($plan.GpuName -eq $script:gpu.Name -and $plan.GgufFile -match '\.gguf$') 'explicit NVIDIA uses catalog model plan'
    $script:gpu.VramMB=8192
    Refuses { Get-ODSPortalAmdPlan $sourceRoot -GpuVendor nvidia } 'specialized runtime profile'
    $script:gpu.VramMB=12288
    $oldSelector=$env:ODS_DISABLE_CATALOG_MODEL_SELECTOR
    try {
        $env:ODS_DISABLE_CATALOG_MODEL_SELECTOR='true'
        Refuses { Get-ODSPortalAmdPlan $sourceRoot -GpuVendor nvidia } 'catalog fit'
    } finally { $env:ODS_DISABLE_CATALOG_MODEL_SELECTOR=$oldSelector }
    # A persisted choice is honored only with the complete owned runtime plan.
    New-Item -ItemType Directory -Path (Get-ODSPortalRuntimeDir) | Out-Null
    $optionsPath = Join-Path (Get-ODSPortalRuntimeDir) 'runtime-options.json'
    $saved = @{schemaVersion=1; GpuVendor='nvidia'; Device='CUDA0'; NGpuLayers='all'; Backend='cuda'; Architecture='x64';
        ApiKeyPath=(Join-Path $script:fixture 'key'); LogPath=(Join-Path $script:fixture 'log');
        ReleaseTag='b9014'; ZipSha256=('a' * 64); ReasoningArguments=@(); ExtraArguments=@()}
    $saved | ConvertTo-Json | Set-Content -LiteralPath $optionsPath
    Refuses { Resolve-ODSPortalWindowsLlmRoute @{} } 'owned runtime task'
    $runtimePlan = @{ExecutablePath=(Join-Path $script:fixture 'llama-server.exe'); ModelsDir=$script:fixture;
        GgufFile='fixture.gguf'; Port=18080; ContextSize=4096}
    $script:existing = [pscustomobject]@{Kind='llama-server'; Launch=@{Plan=$runtimePlan}}
    function Test-ODSNativeLlamaInstall { param($Directory,$ExpectedZipSha256,$ExpectedReleaseTag) @{Directory=$Directory} }
    Refuses { Resolve-ODSPortalWindowsLlmRoute @{} } 'model is missing'
    Set-Content -LiteralPath (Join-Path $script:fixture 'fixture.gguf') -Value 'fixture'
    Check ((Resolve-ODSPortalWindowsLlmRoute @{}) -eq 'nvidia') 'omitted choice preserves installed NVIDIA CUDA'
    Refuses { Resolve-ODSPortalWindowsLlmRoute @{WindowsLlmBackend='auto'} } 'not supported'
    $runtimePlan.Port = 0
    Refuses { Resolve-ODSPortalWindowsLlmRoute @{} } 'saved llama-server plan'
    $runtimePlan.Port = 18080
    $saved.Device = 'none'; $saved.NGpuLayers='0'
    $saved | ConvertTo-Json | Set-Content -LiteralPath $optionsPath
    Refuses { Resolve-ODSPortalWindowsLlmRoute @{} } 'invalid'
    $saved.GpuVendor=$null
    $saved | ConvertTo-Json | Set-Content -LiteralPath $optionsPath
    Refuses { Resolve-ODSPortalWindowsLlmRoute @{} } 'invalid'
    Remove-Item -LiteralPath $optionsPath

    # Shared preparation executes real producer logic, with all mutators stubbed.
    $script:existing = [pscustomobject]@{Kind='none'}
    $script:downloaded = $false; $script:cutover = $false; $script:accept = $false
    function Get-ODSNativeLlamaInstallDirectory { Join-Path $script:fixture 'absent' }
    function Confirm-ODSPortalPreparation { param($Message,$NonInteractive) $script:prompt=$Message; $script:accept }
    function Install-ODSNativeLlamaRuntime { param($Pin) $script:downloaded=$true
        @{ExecutablePath=(Join-Path $script:fixture 'llama-server.exe'); Directory=$script:fixture; ReleaseTag=$Pin.ReleaseTag; ZipSha256=$Pin.Sha256} }
    $script:device = @{Name='CUDA0';Description='NVIDIA GeForce RTX 3060';TotalMiB=12288}
    function Test-ODSNativeLlamaQualification { param($ExecutablePath,$ExpectedBuild,$AdapterName,$GpuVendor,$Backend,$Architecture)
        Check ($GpuVendor -eq 'nvidia' -and $Backend -eq 'cuda' -and $Architecture -eq 'x64') 'qualification selects NVIDIA x64 CUDA explicitly'; @{Device=$script:device} }
    function Resolve-ODSPortalRuntimeSelection { param($Existing,$Plan) @{Carried=$false;GgufFile='fixture.gguf';ContextSize=4096} }
    function Get-ODSPortalModel { $script:modelDownloaded=$true }
    function Write-ODSPortalFitWarning { }
    function Select-ODSPortalRuntimePort { 18080 }
    function Get-ODSPortalApiKey { 'fixture-key' }
    function Get-ODSNativeReasoningArgs { @() }
    function Invoke-ODSPortalRuntimeCutover { param($Existing,$Plan,$Options,$ApiKey,$KeepPort,$WslDistro,$WslInstallDir,$TaskDescription)
        $script:cutover=$true; $script:writtenOptions=$Options; $script:description=$TaskDescription
        if ($script:failCutover) { throw 'fixture rollback retained previous route' } }
    function Remove-ODSNativeLlamaOldRuntimes { }
    Refuses { Initialize-ODSPortalAmdRuntime $plan $sourceRoot $false -GpuVendor nvidia } 'cancelled'
    Check (-not $script:downloaded -and -not $script:cutover) 'cancellation does not download or cut over'
    Check ($script:prompt -match 'NVIDIA') 'download consent names NVIDIA'
    $script:accept=$true; $script:device=$null
    Refuses { Initialize-ODSPortalAmdRuntime $plan $sourceRoot $true -GpuVendor nvidia } 'no usable CUDA device'
    Check (-not $script:cutover -and -not $script:modelDownloaded) 'missing NVIDIA device refuses before model download and cutover'
    $script:device=@{Name='CUDA0';Description='NVIDIA GeForce RTX 3060';TotalMiB=12288}
    $script:failCutover=$true
    Refuses { Initialize-ODSPortalAmdRuntime $plan $sourceRoot $true -GpuVendor nvidia } 'rollback retained'
    $script:failCutover=$false
    $route = Initialize-ODSPortalAmdRuntime $plan $sourceRoot $true -GpuVendor nvidia
    function Get-ODSNativeLlamaInstallDirectory { return $script:fixture }
    $script:rejectCachedRuntime=$false
    function Test-ODSNativeLlamaInstall {
        param($Directory,$ExpectedZipSha256,$ExpectedReleaseTag,$ExpectedArchives)
        $script:checkedCacheArchives=@($ExpectedArchives)
        if ($script:rejectCachedRuntime) { throw 'cached CUDA dependency archive differs' }
        return @{Directory=$Directory;ExecutablePath=(Join-Path $Directory 'llama-server.exe');ReleaseTag=$ExpectedReleaseTag;ZipSha256=$ExpectedZipSha256}
    }
    $script:downloaded=$false
    $null=Initialize-ODSPortalAmdRuntime $plan $sourceRoot $true -GpuVendor nvidia
    $expectedPin=Get-ODSNativeLlamaPin -SourceRoot $sourceRoot -Backend nvidia -Architecture x64
    Check ($script:checkedCacheArchives.Count -eq 2 -and $script:checkedCacheArchives[1].Sha256 -ceq $expectedPin.Archives[1].Sha256 -and -not $script:downloaded) 'cached CUDA reuse verifies both pinned archives before skipping acquisition'
    $script:rejectCachedRuntime=$true; $script:downloaded=$false
    $null=Initialize-ODSPortalAmdRuntime $plan $sourceRoot $true -GpuVendor nvidia
    Check ($script:downloaded) 'a changed cached CUDA dependency requires verified acquisition'
    Check ($script:writtenOptions.GpuVendor -eq 'nvidia' -and $script:writtenOptions.Backend -eq 'cuda' -and
        $script:writtenOptions.Architecture -eq 'x64' -and $script:writtenOptions.NGpuLayers -eq 'all' -and
        $script:description -match 'CUDA x64') 'successful retry persists NVIDIA x64 CUDA choice and task description'
    Check (($route.Arguments -join ' ') -match '--native-llm-url http://localhost:18080 --native-llm-host-transport model-router' -and
        ($route.Arguments -join ' ') -match '--native-llm-model fixture.gguf' -and $route.Environment.Count -eq 1) 'NVIDIA emits existing authenticated Linux native model binding'
    # The public setup boundary bypasses only the NVIDIA WSL/CUDA gate.
    function Test-ODSPortalAdministrator { $false }
    function Test-ODSNativeWindowsInstall { $false }
    $script:foundation=0; $script:cudaChecks=0
    function Initialize-ODSPortalWindowsFoundation { $script:foundation++; $null }
    function Assert-ODSPortalStateRoot { }
    function Assert-ODSPortalWslVersion { }
    function Get-ODSPortalDistroNames { @('Ubuntu-24.04') }
    function Assert-ODSPortalDistroRelease { }
    function Get-ODSPortalInitProcess { 'systemd' }
    function Initialize-ODSPortalDocker { $null }
    function Get-ODSPortalDistroDrive { 'C' }
    function Assert-ODSPortalWslBinding { }
    function Get-ODSPortalWindowsNvidiaDriver { '616.92' }
    function Assert-ODSPortalNvidiaReady { $script:cudaChecks++ }
    function Get-ODSPortalDockerDesktop { @{Exe='fixture-docker.exe'} }
    function Invoke-ODSPortalWsl { param($Arguments)
        $output = '1000'
        if (($Arguments -join ' ') -match '--list --verbose') { $output='Ubuntu-24.04 Running 2' }
        if (($Arguments -join ' ') -match 'printenv HOME') { $output='/home/fixture' }
        @{Code=0; Output=$output} }
    function Invoke-ODSPortalLinuxInstaller { param($InstallerRoot,$Distro,$LinuxArgs,$InstallDir,$Interactive,$Docker,$StateRoot,$NewArguments,$Environment)
        $script:linuxArguments=$LinuxArgs; $script:linuxEnvironment=$Environment; 0 }
    $originalOS=$env:OS; $env:OS='Windows_NT'
    try {
        Refuses { Invoke-ODSPortalSetup @{WindowsLlmBackend='cuda';Cloud=$true} $installerRoot } 'Cloud'
        Check ($script:foundation -eq 0) 'conflicting choice refuses before foundation mutation'
        $script:driver='100.00'
        Refuses { Invoke-ODSPortalSetup @{WindowsLlmBackend='cuda'} $installerRoot } 'older than'
        Check ($script:foundation -eq 0) 'old NVIDIA driver refuses before foundation mutation'
        $script:driver='616.92'; $script:compute='99.0'
        Refuses { Invoke-ODSPortalSetup @{WindowsLlmBackend='cuda'} $installerRoot } 'compute capability'
        Check ($script:foundation -eq 0) 'unsupported CUDA compute capability refuses before foundation mutation'
        $script:compute='8.6'
        $script:gpu.VramMB=128
        Refuses { Invoke-ODSPortalSetup @{WindowsLlmBackend='cuda'} $installerRoot } 'No catalog model fits'
        Check ($script:foundation -eq 0) 'no-fit NVIDIA choice refuses before foundation mutation'
        $script:gpu.VramMB=12288
        $script:accept=$false
        Refuses { Invoke-ODSPortalSetup @{WindowsLlmBackend='cuda'} $installerRoot } 'cancelled'
        Check ($script:foundation -eq 0) 'declined NVIDIA choice refuses before foundation mutation'
        $script:accept=$true
        Check ((Invoke-ODSPortalSetup @{WindowsLlmBackend='cuda'} $installerRoot) -eq 0) 'explicit NVIDIA completes public Portal setup'
        Check ($script:foundation -eq 1 -and $script:cudaChecks -eq 0) 'NVIDIA CUDA preserves WSL prerequisites and bypasses WSL CUDA check'
        Check (($script:linuxArguments -join ' ') -match '--native-llm-host-transport model-router' -and
            $script:linuxEnvironment.Count -eq 1) 'public setup forwards authenticated native binding'
        Check ((Invoke-ODSPortalSetup @{} $installerRoot) -eq 0 -and $script:cudaChecks -eq 1) 'unselected NVIDIA still checks WSL CUDA'
    } finally { $env:OS=$originalOS }
} finally { Remove-Item -LiteralPath $script:fixture -Recurse -Force }
