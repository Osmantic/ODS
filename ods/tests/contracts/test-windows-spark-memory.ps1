# RTX Spark memory planner (PR B3): the layout reader, the model ladder and
# fallback list, the commit, GPU and disk fits, the page-file target and its
# elevated setter, the page-file entries, and the consent flow for the WSL
# limit and the page file. WMI, the registry, nvidia-smi, UAC, WSL, Docker
# Desktop and prompts are fixtures; only temporary files are written. The
# memory layouts come from tests/fixtures/rtx-spark/memory-*.json.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $root 'installers/windows/lib/wsl-portal-setup.ps1')
. (Join-Path $root 'installers/windows/lib/spark-memory.ps1')
$fixtures = Join-Path (Join-Path (Join-Path $root 'tests') 'fixtures') 'rtx-spark'
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
function Get-Output { return (@($script:output) -join "`n") }
function Near([double]$Actual, [double]$Expected) { return [math]::Abs($Actual - $Expected) -lt 0.011 }
# Nothing on this machine is ever asked: the real nvidia-smi, WMI, prompts,
# WSL, Docker Desktop and UAC fail the test if a path reaches them.
function Get-ODSNativeGpuMemoryState([string]$GpuName = '') { throw 'nvidia-smi must not run in the planner tests' }
function Get-ODSSparkNvidiaRows { throw 'nvidia-smi must not run in the planner tests' }
function Read-Host { throw 'Read-Host must not run in the planner tests' }
function Invoke-ODSPortalWsl([string[]]$Arguments) { throw "WSL must not run here: $($Arguments -join ' ')" }
function Start-ODSElevatedProcess([string]$FilePath, [string]$Arguments) { throw 'UAC must not be requested here' }
function Get-ODSPortalDockerDesktop { throw 'Docker Desktop must not be queried here' }
function Get-ODSWindowsLastBootUtc { throw 'the boot time must not be read from WMI here' }
$helpLine = 'Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC'

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-spark-memory-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$previous = @{ LOCALAPPDATA = $env:LOCALAPPDATA; USERPROFILE = $env:USERPROFILE; HOST_ARCH = $env:HOST_ARCH; SystemDrive = $env:SystemDrive }
$env:LOCALAPPDATA = Join-Path $fixture 'LocalAppData'
$env:USERPROFILE = Join-Path $fixture 'Profile'
$env:SystemDrive = 'C:'
Remove-Item Env:\HOST_ARCH -ErrorAction SilentlyContinue
$null = New-Item -ItemType Directory -Path $env:LOCALAPPDATA, $env:USERPROFILE -Force
try {
    $catalog = Get-Content -LiteralPath (Join-Path $root 'config/model-library.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $pin = Get-ODSNativeLlamaPin -SourceRoot $root -Backend nvidia -Architecture arm64
    $testModel = Get-CatalogModelById -Catalog $catalog -ModelId 'qwen3.5-2b-q4'
    $setup = @($pin.Archives | ForEach-Object { [pscustomobject]@{ File = $_.Asset; SizeBytes = $_.Size } }) +
        @([pscustomobject]@{ File = [string]$testModel.gguf_file; SizeBytes = [long]$testModel.size_bytes })
    $modelsDir = 'C:\ODS-fixture\models'
    $room = [pscustomobject]@{ 'C:' = 300 }
    function Read-Layout([string]$Name, [hashtable]$Change = @{}) {
        $layout = Get-Content -LiteralPath (Join-Path $fixtures $Name) -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($key in $Change.Keys) { $layout.$key = $Change[$key] }
        return $layout
    }
    function Get-Plan($Layout, [bool]$PageFile = $true, [bool]$Wsl = $true, [string[]]$Ladder = $pin.ModelLadder,
        [string[]]$Fallback = $pin.FallbackModels, [object[]]$Present = @(), $Catalog = $catalog, [int]$MinContext = 65536,
        [string]$ModelsDir = $modelsDir) {
        return (Select-ODSSparkModelPlan -Catalog $Catalog -Ladder $Ladder -Fallback $Fallback -Layout $Layout -ModelsDir $ModelsDir `
            -PresentFiles $Present -AllowPageFile $PageFile -AllowWslBound $Wsl -MinContext $MinContext -SetupArtifacts $setup)
    }
    function Get-Skip($Plan, [string]$Id) { return (@($Plan.Skipped | Where-Object { $_.Id -ceq $Id } | ForEach-Object { $_.Reason }) -join ',') }
    $top = 'qwen3.6-35b-a3b-ud-q4'
    $small = 'qwen3.5-2b-q4'
    $nine = 'qwen3.5-9b-q4'
    $four = 'qwen3.5-4b-q4'
    Check ((@($pin.ModelLadder) -join ',') -ceq $top -and (@($pin.FallbackModels) -join ',') -ceq "$nine,$four,$small") 'the pinned ladder is the 35B-A3B floor and the fallback list the 9B, 4B and the 2B test model, best first (larger rungs need their own hardware proof)'

    # --- The layouts of PR B design 6.4, run through the planner ----------------
    $raised = Read-Layout 'memory-raised-96.json' @{ FreeGiB = $room }
    $plan = Get-Plan $raised
    Check ($plan.Model -ceq $top -and $plan.Source -ceq 'ladder' -and $null -eq $plan.Offer -and $plan.ContextSize -eq 131072 -and
        (Near $plan.DeviceNeedGiB 23.83) -and (Near $plan.CommitNeedGiB 25.8) -and (Near $plan.CommitCapacityGiB 93.25) -and
        $plan.WslSource -ceq 'default' -and (Near $plan.WslGiB 15.25)) 'the RTX Spark as measured (96 GB split, 96 GB page file) runs 35B-A3B at 131072 tokens: 23.83 GiB on the GPU, 25.80 GiB of commit, 93.25 GiB lendable'
    $record = Get-ODSSparkPlanMemoryRecord $plan
    Check ($record.GgufFile -ceq 'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf' -and $record.ContextSize -eq 131072 -and $record.DeviceNeedGiB -eq 23.83 -and
        $record.CommitNeedGiB -eq 25.8 -and (Near $record.WslGiB 15.25) -and $record.Source -ceq 'catalog' -and
        (Test-ODSNativeMemoryPlanRecord ([pscustomobject]$record))) 'the chosen plan becomes the memory-plan.json record the launcher checks at every start'
    $measured = Get-Plan (Read-Layout 'memory-raised-96.json')
    Check ($measured.Model -ceq $four -and $measured.Source -ceq 'fallback' -and (Get-Skip $measured $top) -ceq 'disk' -and (Get-Skip $measured $nine) -ceq 'disk' -and
        $null -eq $measured.Offer) 'with the 26.9 GB it has free on C:, the measured Spark skips 35B-A3B and the 9B for disk and plans the 4B fallback (no memory change can help)'
    $carried = Get-Plan (Read-Layout 'memory-raised-96.json') -Present @('Qwen3.6-35B-A3B-UD-Q4_K_M.gguf')
    Check ($carried.Model -ceq $top -and $carried.DiskNeedGiB -lt 25.05) 'a model already in the store needs no download, so the same disk fits it'
    $factory = Read-Layout 'memory-factory-64.json'
    $plan = Get-Plan $factory
    Check ((Near $plan.CommitCapacityGiB 13.25) -and $plan.Model -ceq $nine -and $plan.Source -ceq 'fallback' -and (Get-Skip $plan $top) -ceq 'commit' -and
        $plan.Offer.Model -ceq $top -and $plan.Offer.WslBoundGiB -eq 16 -and $null -eq $plan.Offer.PageFileGiB -and $plan.Offer.Reason -ceq 'wsl-bound' -and
        (Near $plan.Offer.CommitCapacityGiB 28.5)) 'a factory Spark (64 GB split, no page file) lends 13.25 GiB: the 9B (9.34 GiB) now, and an offer of 35B-A3B with a 16 GB WSL limit (28.5 GiB, no UAC)'
    $plan = Get-Plan $factory -Wsl $false
    Check ($plan.Offer.Model -ceq $top -and $plan.Offer.PageFileGiB -eq 16 -and $null -eq $plan.Offer.WslBoundGiB -and (Near $plan.Offer.CommitCapacityGiB 29.25)) 'without the WSL limit the factory offer is a 16 GB page file (29.25 GiB)'
    $plan = Get-Plan (Read-Layout 'memory-factory-64-pf64.json')
    Check ($plan.Model -ceq $top -and $null -eq $plan.Offer -and (Near $plan.CommitCapacityGiB 92.5) -and $plan.WslSource -ceq 'ods') 'the factory split with a 64 GB page file and ODS''s WSL limit runs 35B-A3B (92.5 GiB lendable)'
    $noPageFile = Read-Layout 'memory-96-nopf.json'
    $plan = Get-Plan $noPageFile
    Check ($plan.Source -ceq 'none' -and $null -eq $plan.Model -and (Near $plan.CommitCapacityGiB -2.75) -and $plan.Offer.Model -ceq $top -and
        $plan.Offer.PageFileGiB -eq 32 -and $plan.Offer.Reason -ceq 'page-file' -and (Get-Skip $plan $top) -ceq 'commit' -and
        (Get-Skip $plan $small) -ceq 'commit') 'the 96 GB split without a page file fits nothing now (-2.75 GiB) and offers 35B-A3B with a 32 GB page file; its default WSL limit is already under 16 GB'
    $plan = Get-Plan $noPageFile -PageFile $false
    Check ($plan.Source -ceq 'none' -and $null -eq $plan.Offer) 'with no page-file change allowed and nothing fitting, the plan is none: the caller offers the CPU route'
    $plan = Get-Plan (Read-Layout 'memory-pending.json')
    Check ($plan.Model -ceq $top -and (Near $plan.CommitCapacityGiB 45.25)) 'a page file waiting for its restart is planned at the configured size (32 GB)'
    foreach ($name in @(Get-ChildItem -LiteralPath $fixtures -Filter 'memory-*.json' | ForEach-Object { $_.Name })) {
        $layout = Read-Layout $name
        Check ([string]$layout._source -match '^(Measured|Synthesized)' -and $null -ne (Get-Plan $layout).Source) "fixture $name says how it was made and plans"
    }

    # --- The commit rule and the page-file target ----------------------------------
    $fit = Get-ODSNativeCommitFit -CommitNeedGiB 25.8 -CommitLimitGiB 62.5 -OtherCommitGiB 16 -WslGiB 31.25
    Check ((Near $fit.CapacityGiB 13.25) -and -not $fit.Fits) 'the shared commit rule: limit - other commit - WSL limit - 2 GiB'
    Check ((Get-ODSNativeOtherCommitGiB 40 0 $null) -eq 16 -and (Get-ODSNativeOtherCommitGiB 40 0 2) -eq 38 -and
        (Get-ODSNativeOtherCommitGiB 40 25 2) -eq 16) 'other commit is the live commit less the ODS model and WSL, never under 16 GiB, and 16 GiB when WSL''s own commit is unknown'
    Check ($null -eq (Get-ODSSparkPageFileTargetGiB $raised 25.8 15.25)) 'no page-file target when the configured one is enough'
    $forty = Read-Layout 'memory-96-nopf.json' @{ PageFileConfiguredGiB = 40; PageFileActiveGiB = 40 }
    Check ((Get-ODSSparkPageFileTargetGiB $forty 50 15.245) -eq 64) 'the target is the smallest multiple of 16 GiB above the configured 40 GiB that fits (64)'
    $large = Read-Layout 'memory-96-nopf.json' @{ PageFileConfiguredGiB = 100; PageFileActiveGiB = 100 }
    Check ((Get-ODSSparkPageFileTargetGiB $large 120 15.245) -eq 128 -and (Get-ODSSparkPageFileTargetGiB $large 1000 15.245) -eq -1) 'the target never shrinks the configured page file, and is -1 when even 256 GiB is not enough'
    $busy = Read-Layout 'memory-96-nopf.json' @{ CommittedGiB = 40; WslNowGiB = 2 }
    Check ((Get-ODSSparkPageFileTargetGiB $busy 25.8 15.245) -eq 64 -and (Get-ODSSparkPageFileTargetGiB $noPageFile 25.8 15.245) -eq 32) 'Windows programs committing 38 GiB (above the 16 GiB floor) raise the page-file target from 32 to 64 GB'
    $model = Read-Layout 'memory-96-nopf.json' @{ CommittedGiB = 40; WslNowGiB = 2; RunningModelGiB = 25 }
    Check ((Get-ODSSparkPageFileTargetGiB $model 25.8 15.245) -eq 32) 'the running ODS model''s own commit is not counted against the next plan'

    # --- GPU fit uses the device figure, commit fit the total ------------------------
    $tight = Read-Layout 'memory-raised-96.json' @{ CarveOutMiB = 24000; FreeGiB = $room }
    $plan = Get-Plan $tight
    Check ((Get-Skip $plan $top) -ceq 'device' -and $plan.Model -ceq $nine) 'a carve-out under 35B-A3B''s 23.83 GiB (with the fit margin) skips it for the GPU'
    $between = Read-Layout 'memory-96-nopf.json' @{ PageFileConfiguredGiB = 27.26; PageFileActiveGiB = 27.26 }
    $plan = Get-Plan $between
    Check (($plan.CommitCapacityGiB -gt 23.83) -and ($plan.CommitCapacityGiB -lt 25.8) -and (Get-Skip $plan $top) -ceq 'commit' -and
        $plan.Model -ceq $nine -and $plan.Offer.PageFileGiB -eq 32) 'with 24.5 GiB lendable, 35B-A3B fits its GPU need (23.83) but not its commit (25.80), so the commit decides'

    # --- Disk ------------------------------------------------------------------------
    $plan = Get-Plan (Read-Layout 'memory-factory-64.json' @{ FreeGiB = [pscustomobject]@{ 'C:' = 80 } }) -Wsl $false
    Check ($plan.Model -ceq $nine -and $null -eq $plan.Offer) 'a page-file offer needs room for its growth: 80 GiB free holds 35B-A3B and a fresh stack but not 16 GB more'
    $plan = Get-Plan (Read-Layout 'memory-factory-64.json' @{ FreeGiB = [pscustomobject]@{ 'C:' = 90 } }) -Wsl $false
    Check ($plan.Offer.PageFileGiB -eq 16) 'with 90 GiB free the same offer is made'
    $twoDrives = Read-Layout 'memory-factory-64.json' @{ FreeGiB = [pscustomobject]@{ 'C:' = 5; 'D:' = 300 }; ModelsDrive = 'D:' }
    $plan = Get-Plan $twoDrives -Wsl $false -ModelsDir 'D:\ODS-fixture\models'
    Check ($plan.Model -ceq $nine -and $null -eq $plan.Offer) 'models on D: with room, but no room on C: for the page file: no page-file offer'
    $disk = Get-ODSSparkDiskNeedGiB -Layout $factory -Artifacts @([pscustomobject]@{ File = 'a.gguf'; SizeBytes = 10GB }) -ModelsDir $modelsDir `
        -PageFileGrowthGiB 16 -StackPresent $false
    Check ((Near $disk.DownloadGiB 10) -and (Near $disk.NeedGiB 76) -and $disk.Fits) 'disk need: download + page-file growth on the same drive + 40 GiB for a fresh stack + 10 GiB margin'
    Check ((Near (Get-ODSSparkDiskNeedGiB -Layout $factory -Artifacts @() -ModelsDir $modelsDir -StackPresent $true).NeedGiB 20)) 'an installed stack (Docker Desktop data present) needs a 10 GiB reserve'

    # --- Candidates are only the ladder and the fallback list ----------------------
    $plan = Get-Plan $raised -Ladder @('no-such-model', 'llama4-scout-q4', $top)
    Check ((Get-Skip $plan 'no-such-model') -ceq 'missing' -and (Get-Skip $plan 'llama4-scout-q4') -ceq 'artifacts' -and $plan.Model -ceq $top) 'a ladder id missing from the catalog, or whose files have no size and SHA-256, is skipped with its reason'
    $plan = Get-Plan $raised -Ladder @($small) -Fallback @() -MinContext 131072
    Check ((Get-Skip $plan $small) -ceq 'context' -and $plan.Source -ceq 'none') 'a ladder rung below the minimum context is skipped'
    $plan = Get-Plan $raised -Ladder @() -Fallback @($small) -MinContext 131072
    Check ($plan.Model -ceq $small -and $plan.ContextSize -eq 65536) 'a fallback model may run at its own catalog context'
    $plan = Get-Plan $raised -Ladder @() -Fallback @()
    Check ($plan.Source -ceq 'none' -and $null -eq $plan.Model) 'nothing outside the two lists is ever a candidate'

    # --- Host architecture: an emulated x64 shell still plans as arm64 ----------
    $synthetic = [pscustomobject]@{ models = @([pscustomobject]@{ id = 'arm-profile-test'; name = 'Arm profile test'; family = 'qwen'
        gguf_file = 'Arm-Test.gguf'; gguf_url = 'https://huggingface.co/fixture/arm/resolve/0/Arm-Test.gguf'; gguf_sha256 = ('a' * 64)
        size_bytes = 10737418240; size_mb = 10240; vram_required_gb = 12; context_length = 65536
        runtime_profiles = @(
            [pscustomobject]@{ id = 'arm64-measured'; backend = 'nvidia'; host_arch = @('arm64'); context_length = 131072; estimated_required_gb = 70; estimated_host_gb = 17 },
            [pscustomobject]@{ id = 'x64-measured'; backend = 'nvidia'; host_arch = @('amd64'); context_length = 65536; estimated_required_gb = 30 }) }) }
    & {
        function Get-HostArchitecture { return 'amd64' }
        $gpu = @{ Backend = 'nvidia'; MemoryType = 'discrete'; VramMB = 98240 }
        $emulated = Get-CatalogRuntimeProfile -Model $synthetic.models[0] -GpuInfo $gpu -SystemRamGB 30
        $native = Get-CatalogRuntimeProfile -Model $synthetic.models[0] -GpuInfo $gpu -SystemRamGB 30 -HostArchitecture 'arm64'
        Check ($emulated.id -ceq 'x64-measured' -and $native.id -ceq 'arm64-measured') 'an emulated x64 PowerShell matches x64 profiles; -HostArchitecture arm64 matches the arm64 profile'
        $plan = Get-Plan $raised -Ladder @('arm-profile-test') -Fallback @() -Catalog $synthetic
        Check ($plan.Model -ceq 'arm-profile-test' -and $plan.ContextSize -eq 131072 -and $plan.DeviceNeedGiB -eq 70 -and $plan.CommitNeedGiB -eq 87) 'the planner uses the arm64 profile, its measured GPU figure (70) and GPU plus host for commit (87)'
    }

    # --- Installable check ---------------------------------------------------------
    $single = Test-ODSCatalogModelArtifacts -Model (Get-CatalogModelById -Catalog $catalog -ModelId $top)
    Check (@($single).Count -eq 1 -and $single[0].File -ceq 'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf' -and $single[0].SizeBytes -eq 22134528992 -and
        $single[0].Sha256 -cmatch '^[0-9a-f]{64}$' -and $single[0].Url -like 'https://huggingface.co/*') 'a single-file model is one artifact with its URL, SHA-256 and size'
    $parts = Test-ODSCatalogModelArtifacts -Model (Get-CatalogModelById -Catalog $catalog -ModelId 'qwen2.5-7b-instruct-q4')
    Check (@($parts).Count -eq 2 -and $parts[0].File -like '*-00001-of-00002.gguf' -and $parts[1].File -like '*-00002-of-00002.gguf') 'a split model with every part pinned is all its parts'
    Check ($null -eq (Test-ODSCatalogModelArtifacts -Model (Get-CatalogModelById -Catalog $catalog -ModelId 'llama4-scout-q4'))) 'a split model whose parts lack a size is not installable'
    $noSha = [pscustomobject]@{ gguf_file = 'x.gguf'; gguf_url = 'https://huggingface.co/x/resolve/0/x.gguf'; size_bytes = 1048576 }
    Check ($null -eq (Test-ODSCatalogModelArtifacts -Model $noSha)) 'a model without a SHA-256 is not installable'
    $vision = [pscustomobject]@{ gguf_file = 'v.gguf'; gguf_url = 'https://huggingface.co/v/resolve/0/v.gguf'; gguf_sha256 = ('b' * 64); size_bytes = 1048576
        mmproj_file = 'mmproj-v.gguf'; mmproj_url = 'https://huggingface.co/v/resolve/0/mmproj-v.gguf'; mmproj_sha256 = ('c' * 64); mmproj_size_bytes = 2048 }
    $withProjector = Test-ODSCatalogModelArtifacts -Model $vision
    Check (@($withProjector).Count -eq 2 -and $withProjector[1].File -ceq 'mmproj-v.gguf' -and $withProjector[1].SizeBytes -eq 2048) 'the flat mmproj_* fields add the vision projector'
    $vision.mmproj_sha256 = ''
    Check ($null -eq (Test-ODSCatalogModelArtifacts -Model $vision)) 'a projector without its SHA-256 makes the model not installable'

    # --- Page-file entries (ported from the RTX Spark branch) -------------------------
    Check (((Get-ODSPagingFilesEntries @('?:\pagefile.sys') 'C:' 98304) -join '|') -ceq 'C:\pagefile.sys 98304 98304') 'the automatic all-drives entry becomes one fixed system-drive page file'
    Check (((Get-ODSPagingFilesEntries @('c:\pagefile.sys 0 0', 'D:\pagefile.sys 65536 65536') 'C:' 98304) -join '|') -ceq 'C:\pagefile.sys 98304 98304|D:\pagefile.sys 65536 65536') 'the system drive page file is replaced and other drives keep theirs'
    Check (((Get-ODSPagingFilesEntries $null 'C:' 98304) -join '|') -ceq 'C:\pagefile.sys 98304 98304') 'no page file at all gets the system drive one'
    Check ((Get-Failure { $null = Get-ODSPagingFilesEntries @('C:\pagefile.sys 131072 131072') 'C:' 98304 }) -match 'never makes it smaller') 'a larger fixed page file on C: is never shrunk'
    Check (((Get-ODSPagingFilesEntries @('C:\pagefile.sys 98304 98304') 'C:' 98304) -join '|') -ceq 'C:\pagefile.sys 98304 98304') 'the same size is accepted'
    Check ((Get-ODSSystemPagingFile @() 'C:').Kind -ceq 'none' -and (Get-ODSSystemPagingFile @('C:\pagefile.sys 98304 98304') 'C:').MaximumMB -eq 98304 -and
        (Get-ODSSystemPagingFile @('C:\pagefile.sys 0 0') 'C:').Kind -ceq 'system' -and (Get-ODSSystemPagingFile @('?:\pagefile.sys') 'C:').Kind -ceq 'automatic' -and
        (Get-ODSSystemPagingFile @('D:\pagefile.sys 4096 4096') 'C:').Kind -ceq 'none') 'the system drive''s page file is read as none, fixed, system managed or automatic'
    $setter = [IO.File]::ReadAllText((Join-Path $root 'installers/windows/set-pagefile.ps1'))
    $tokens = $null; $errors = $null
    $null = [Management.Automation.Language.Parser]::ParseInput($setter, [ref]$tokens, [ref]$errors)
    Check ($errors.Count -eq 0 -and $setter.Contains('[ValidateRange(16, 256)]') -and $setter.Contains('IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)') -and
        $setter.Contains('never makes it smaller') -and $setter.Contains('AutomaticManagedPagefile = $false')) 'the elevated setter parses, takes 16-256 GB, needs administrator rights, never shrinks and turns automatic management off'

    # --- Layout reader (unelevated WMI, registry and process reads) -----------------
    & {
        $script:pagingValue = @('C:\pagefile.sys 98304 98304')
        $script:usage = @([pscustomobject]@{ Name = 'C:\pagefile.sys'; AllocatedBaseSize = 98304 })
        $script:automatic = $false
        $script:vmmem = @()
        function Get-CimInstance { param([string]$ClassName, $ErrorAction)
            switch ($ClassName) {
                # Level B read on the RTX Spark: kilobytes.
                'Win32_OperatingSystem' { return [pscustomobject]@{ TotalVisibleMemorySize = 31974840; TotalVirtualMemorySize = 132638136; FreeVirtualMemory = 11394904 } }
                'Win32_PageFileUsage' { return $script:usage }
                'Win32_ComputerSystem' { return [pscustomobject]@{ AutomaticManagedPagefile = $script:automatic } }
                default { throw "unexpected WMI class $ClassName" }
            }
        }
        function Get-ODSPagingFilesValue { return $script:pagingValue }
        function Get-ODSDriveFreeGiB([string]$Drive) { return 25.05 }
        function Get-Process { param($Name, $Id, $ErrorAction) return $script:vmmem }
        $layout = Get-ODSSparkMemoryLayout -CarveOutMiB 98240 -ModelsDir $modelsDir
        $shape = @((Read-Layout 'memory-raised-96.json').PSObject.Properties | ForEach-Object { $_.Name } | Where-Object { $_ -cne '_source' })
        Check ((@($layout.PSObject.Properties | ForEach-Object { $_.Name }) -join ',') -ceq ($shape -join ',')) 'the layout reader returns exactly the fixture shape'
        Check ($layout.VisibleGiB -eq 30.49 -and $layout.CommitLimitGiB -eq 126.49 -and (Near $layout.CommittedGiB 115.63) -and $layout.PageFileActiveGiB -eq 96 -and
            $layout.PageFileConfiguredGiB -eq 96 -and -not $layout.PageFilePending -and $null -eq $layout.WslNowGiB -and $layout.RunningModelGiB -eq 0 -and
            $null -eq $layout.WslConfig.GiB -and -not $layout.StackPresent) 'the Level B figures read as 30.49 GiB visible, a 126.49 GiB limit, 115.63 GiB committed and a 96 GB page file, nothing pending'
        $script:pagingValue = @('C:\pagefile.sys 131072 131072')
        $pending = Get-ODSSparkMemoryLayout -CarveOutMiB 98240 -ModelsDir $modelsDir
        Check ($pending.PageFileConfiguredGiB -eq 128 -and $pending.PageFileActiveGiB -eq 96 -and $pending.PageFilePending) 'a configured size other than the active one is a change waiting for a restart'
        $script:pagingValue = @('?:\pagefile.sys'); $script:automatic = $true
        $auto = Get-ODSSparkMemoryLayout -CarveOutMiB 98240 -ModelsDir $modelsDir
        Check ($auto.PageFileAutomatic -and $auto.PageFileConfiguredGiB -eq 96 -and -not $auto.PageFilePending) 'an automatic page file is planned at what Windows allocated and is never pending'
        $script:vmmem = @([pscustomobject]@{ PagedMemorySize64 = 15GB })
        $null = New-Item -ItemType Directory -Path ([IO.Path]::Combine($env:LOCALAPPDATA, 'Docker', 'wsl', 'disk')) -Force
        [IO.File]::WriteAllText([IO.Path]::Combine($env:LOCALAPPDATA, 'Docker', 'wsl', 'disk', 'docker_data.vhdx'), 'fixture')
        $withWsl = Get-ODSSparkMemoryLayout -CarveOutMiB 98240 -ModelsDir $modelsDir
        Check ($withWsl.WslNowGiB -eq 15 -and $withWsl.StackPresent) 'the WSL commit is read when Windows shows it, and Docker Desktop''s data disk means an installed stack'
        # A limit ODS wrote while WSL kept running (the owner said no to the
        # WSL restart) is not in effect until WSL restarts.
        $script:lastBoot = [datetime]::UtcNow.AddHours(-1)
        function Get-ODSWindowsLastBootUtc { return $script:lastBoot }
        $null = Write-ODSWslConfig 16
        $waiting = Get-ODSSparkMemoryLayout -CarveOutMiB 98240 -ModelsDir $modelsDir
        $nested = @((Read-Layout 'memory-raised-96.json').WslConfig.PSObject.Properties | ForEach-Object { $_.Name })
        Check ((@($waiting.WslConfig.PSObject.Properties | ForEach-Object { $_.Name }) -join ',') -ceq ($nested -join ',') -and
            (@($layout.WslConfig.PSObject.Properties | ForEach-Object { $_.Name }) -join ',') -ceq ($nested -join ',')) 'the WSL part of the layout has the fixture shape too (GiB, Owner, PendingGiB)'
        Check ($null -eq $waiting.WslConfig.GiB -and $null -eq $waiting.WslConfig.Owner -and $waiting.WslConfig.PendingGiB -eq 16 -and
            (Get-ODSSparkWslMemory $waiting).Source -ceq 'default') 'a WSL limit ODS wrote that waits for WSL to restart is PendingGiB; the layout plans with the limit in effect (WSL''s default)'
        $script:lastBoot = [datetime]::UtcNow.AddMinutes(5)
        $restarted = Get-ODSSparkMemoryLayout -CarveOutMiB 98240 -ModelsDir $modelsDir
        Check ($restarted.WslConfig.GiB -eq 16 -and $restarted.WslConfig.Owner -ceq 'ods' -and $null -eq $restarted.WslConfig.PendingGiB -and
            (Get-ODSSparkWslMemory $restarted).Source -ceq 'ods') 'once Windows has started again the limit ODS wrote is the one in effect'
        foreach ($path in @((Join-Path $env:USERPROFILE '.wslconfig'), (Get-ODSWindowsSettingsPath), (Get-ODSWindowsRestoreScriptPath))) { Remove-Item -LiteralPath $path -Force }
    }

    # --- Page-file setter: one UAC prompt, read back, original recorded ------------
    & {
        $script:pagingValue = @('?:\pagefile.sys')
        $script:elevated = @()
        $script:elevation = { throw [ComponentModel.Win32Exception]::new(1223) }
        function Get-ODSPagingFilesValue { return $script:pagingValue }
        function Get-CimInstance { param([string]$ClassName, $ErrorAction)
            if ($ClassName -ne 'Win32_ComputerSystem') { throw "unexpected WMI class $ClassName" }
            return [pscustomobject]@{ AutomaticManagedPagefile = ($script:pagingValue -contains '?:\pagefile.sys') }
        }
        function Start-ODSElevatedProcess([string]$FilePath, [string]$Arguments) {
            $script:elevated += ,@($FilePath, $Arguments)
            return (& $script:elevation)
        }
        function New-ExitedProcess([int]$Code) {
            $process = [pscustomobject]@{ ExitCode = $Code }
            $process | Add-Member ScriptMethod WaitForExit { }
            $process | Add-Member ScriptMethod Dispose { }
            return $process
        }
        $settingsPath = Get-ODSWindowsSettingsPath
        $restorePath = Get-ODSWindowsRestoreScriptPath
        $canceled = Set-ODSSparkPageFile 32
        $record = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        Check ($canceled.Result -ceq 'canceled' -and $null -eq $canceled.ExitCode -and (@($record.PageFile.Original) -join '|') -ceq '?:\pagefile.sys' -and
            $record.PageFile.OriginalAutomatic -and $null -eq $record.PageFile.SetGB -and -not (Test-Path -LiteralPath $restorePath)) 'a cancelled UAC prompt (Win32 error 1223) is canceled, never a throw; the original page file is recorded first'
        Check ($script:elevated[0][0] -like '*WindowsPowerShell*v1.0*powershell.exe' -and $script:elevated[0][1] -match '-File "[^"]*set-pagefile\.ps1" -SizeGB 32$') 'the elevated process is Windows PowerShell running set-pagefile.ps1 with the size'
        $script:elevation = { $script:pagingValue = @('C:\pagefile.sys 32768 32768'); return (New-ExitedProcess 0) }
        $set = Set-ODSSparkPageFile 32
        $record = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        Check ($set.Result -ceq 'set' -and $record.PageFile.SetGB -eq 32 -and (@($record.PageFile.Original) -join '|') -ceq '?:\pagefile.sys' -and
            (Test-Path -LiteralPath $restorePath)) 'a confirmed change records its size, keeps the first original and writes the restore script'
        $script:elevation = { $script:pagingValue = @('C:\pagefile.sys 49152 49152'); return (New-ExitedProcess 0) }
        $null = Set-ODSSparkPageFile 48
        $record = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        Check ($record.PageFile.SetGB -eq 48 -and (@($record.PageFile.Original) -join '|') -ceq '?:\pagefile.sys') 'a later change keeps the page file the PC had before ODS'
        $script:elevation = { return (New-ExitedProcess 1) }
        $failed = Set-ODSSparkPageFile 64
        Check ($failed.Result -ceq 'failed' -and $failed.ExitCode -eq 1) 'a nonzero exit of the setter is failed with its code'
        $script:elevation = { return (New-ExitedProcess 0) }
        $unconfirmed = Set-ODSSparkPageFile 64
        Check ($unconfirmed.Result -ceq 'failed' -and $unconfirmed.ExitCode -eq 0) 'exit 0 without the size in the registry is failed (read back unelevated)'
        $script:elevation = { throw [ComponentModel.Win32Exception]::new(2) }
        Check ((Set-ODSSparkPageFile 64).Result -ceq 'failed') 'another start failure is failed'
    }

    # --- Consent flow: the WSL limit (M8, M9) and the page file (M2) ---------------
    & {
        $script:answers = [Collections.Generic.Queue[bool]]::new()
        $script:prompts = [Collections.Generic.List[string]]::new()
        $script:pageFileCalls = [Collections.Generic.List[int]]::new()
        $script:pageFileResult = [pscustomobject]@{ Result = 'set'; ExitCode = 0 }
        $script:wslCalls = [Collections.Generic.List[string]]::new()
        $script:wslPresent = $true
        $script:dockerStarts = 0
        function Confirm-ODSPortalPreparation([string]$Message, [bool]$NonInteractive) {
            $script:prompts.Add($Message)
            if ($NonInteractive) { return $false }
            if (-not $script:answers.Count) { throw "unexpected prompt: $Message" }
            return $script:answers.Dequeue()
        }
        function Set-ODSSparkPageFile([int]$SizeGiB) { $script:pageFileCalls.Add($SizeGiB); return $script:pageFileResult }
        function Test-ODSSparkWslPresent { return $script:wslPresent }
        $script:shutdownCode = 0
        function Invoke-ODSPortalWsl([string[]]$Arguments) { $script:wslCalls.Add($Arguments -join ' '); return [pscustomobject]@{ Code = $script:shutdownCode; Output = ''; Error = '' } }
        function Get-ODSPortalDockerDesktop { return [pscustomobject]@{ Installed = $true; Exe = 'C:\fixture\Docker Desktop.exe' } }
        function Test-ODSPortalDockerEngine($Desktop) { return $false }
        function Start-ODSPortalDockerDesktop($Desktop) { $script:dockerStarts++ }
        $wslConfig = Join-Path $env:USERPROFILE '.wslconfig'
        function Reset-Offer([bool[]]$Answers = @()) {
            $script:answers.Clear(); foreach ($answer in $Answers) { $script:answers.Enqueue($answer) }
            $script:prompts.Clear(); $script:pageFileCalls.Clear(); $script:wslCalls.Clear(); $script:dockerStarts = 0; $script:output = @()
            $script:pageFileResult = [pscustomobject]@{ Result = 'set'; ExitCode = 0 }; $script:wslPresent = $true; $script:shutdownCode = 0
            foreach ($path in @($wslConfig, (Get-ODSWindowsSettingsPath), (Get-ODSWindowsRestoreScriptPath))) { if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force } }
        }
        function Read-Settings { return (Get-Content -LiteralPath (Get-ODSWindowsSettingsPath) -Raw | ConvertFrom-Json) }
        function Invoke-Offer($Layout, [bool]$NonInteractive = $false, [bool]$RestartPending = $false, [string[]]$Ladder = $pin.ModelLadder) {
            $arguments = @{ Catalog = $catalog; Ladder = $Ladder; Fallback = $pin.FallbackModels; Layout = $Layout; ModelsDir = $modelsDir
                PresentFiles = @(); SetupArtifacts = $setup }
            return (Invoke-ODSSparkMemoryOffers -PlanArguments $arguments -NonInteractive $NonInteractive -RestartPending $RestartPending)
        }

        Reset-Offer
        $result = Invoke-Offer (Read-Layout 'memory-pending.json')
        Check ($result.RestartReason -match 'Use Restart, not Shut down' -and $script:prompts.Count -eq 0 -and $script:pageFileCalls.Count -eq 0 -and
            $result.Plan.Model -ceq $top) 'a page file waiting for its restart: no offer and no second UAC prompt, the restart notice (M24), and the plan at the configured size'
        Reset-Offer
        $result = Invoke-Offer $factory -NonInteractive $true
        Check ($result.Plan.Model -ceq $nine -and $script:prompts.Count -eq 0 -and -not (Test-Path -LiteralPath $wslConfig) -and
            (Get-Output) -match 'WSL memory limit not changed\. ODS uses Qwen 3\.5 9B \(64K context\)' -and (Get-Output) -match 'Qwen 3\.6 35B-A3B later' -and
            (Get-Output).Contains($helpLine)) 'non-interactive setup changes nothing, keeps the model that fits and says how to get the larger one (M3)'
        Reset-Offer @($true, $true)
        $result = Invoke-Offer $factory
        $text = [IO.File]::ReadAllText($wslConfig)
        Check ($script:prompts[0] -match 'Limit WSL to 16 GB\?' -and $script:prompts[0].Contains($wslConfig) -and $script:prompts[0].Contains((Get-ODSWindowsRestoreScriptPath)) -and
            $script:prompts[1] -match 'Restart WSL now' -and ($script:wslCalls -join ';') -ceq '--shutdown' -and $script:dockerStarts -eq 1) 'accepting the WSL limit writes it, then with consent restarts WSL and starts Docker Desktop again (M8, M9)'
        Check ($text -ceq "[wsl2]`r`n# Set by ODS setup: memory limit for WSL. Rerun ODS setup to change it.`r`nmemory=16GB`r`n" -and
            $result.Plan.Model -ceq $top -and $result.Plan.WslGiB -eq 16 -and $result.Plan.WslSource -ceq 'ods' -and
            @($result.Done).Count -eq 1 -and -not $result.RestartReason) 'the accepted offer plans 35B-A3B with the 16 GB WSL limit ODS marked as its own'
        Check ($null -eq (Read-Settings).WslConfig.Pending) 'after ODS restarted WSL nothing waits: the record says the 16 GB limit is in effect'
        Reset-Offer @($true, $false)
        $result = Invoke-Offer $factory
        Check ($result.Plan.Model -ceq $nine -and (Near $result.Plan.WslGiB 31.25) -and (Test-Path -LiteralPath $wslConfig) -and $script:wslCalls.Count -eq 0 -and
            (Get-Output) -match 'WSL keeps its current limit until it restarts, so ODS plans with that limit for now\. To use the new one, restart Windows, or rerun setup and let it restart WSL\.' -and
            (Get-Output) -match 'WSL gets the new memory limit only after it restarts\.') 'without a WSL restart the limit waits for one, and ODS plans with the current limit'
        $pending = (Read-Settings).WslConfig.Pending
        Check ($null -ne $pending -and $pending.SinceUnix -gt 0 -and $null -eq $pending.InEffectGiB) 'the record keeps the limit as waiting, with WSL''s default as the one in effect, for stage 3 and later runs'
        # A later run before WSL restarted: the limit is already written.
        $waiting = Read-Layout 'memory-factory-64.json' @{ WslConfig = [pscustomobject]@{ GiB = $null; Owner = $null; PendingGiB = 16.0 } }
        Reset-Offer @($true)
        $null = Write-ODSWslConfig 16
        $result = Invoke-Offer $waiting
        Check ($script:prompts.Count -eq 1 -and $script:prompts[0] -match '^Restart WSL now' -and ($script:wslCalls -join ';') -ceq '--shutdown' -and
            (Get-Output).Contains("ODS already limited WSL to 16 GB in $wslConfig; WSL applies it when it restarts.") -and $result.Plan.Model -ceq $top -and
            $result.Plan.WslGiB -eq 16 -and $null -eq (Read-Settings).WslConfig.Pending) 'a rerun while the limit waits never asks for it again; a yes to the WSL restart (M9) applies it and plans 35B-A3B'
        Reset-Offer @($false)
        $null = Write-ODSWslConfig 16
        $result = Invoke-Offer $waiting
        Check ($script:prompts.Count -eq 1 -and $script:prompts[0] -match '^Restart WSL now' -and $script:wslCalls.Count -eq 0 -and $result.Plan.Model -ceq $nine -and
            (Near $result.Plan.WslGiB 31.25) -and (Get-Output) -match 'WSL gets the new memory limit only after it restarts\. ODS uses Qwen 3\.5 9B' -and
            $null -ne (Read-Settings).WslConfig.Pending) 'a rerun that declines the WSL restart again keeps planning with the limit in effect: the 9B'
        Reset-Offer
        $result = Invoke-Offer $waiting -NonInteractive $true
        Check ($script:prompts.Count -eq 0 -and $result.Plan.Model -ceq $nine -and (Get-Output) -match 'WSL gets the new memory limit only after it restarts\. ODS uses Qwen 3\.5 9B' -and
            -not ((Get-Output) -match 'WSL memory limit not changed')) 'non-interactive, a waiting limit is reported as waiting, and the 9B that fits now is planned'
        Reset-Offer @($true, $true)
        $script:shutdownCode = 1
        $result = Invoke-Offer $factory
        Check ($result.Plan.Model -ceq $nine -and ($script:wslCalls -join ';') -ceq '--shutdown' -and $script:dockerStarts -eq 0 -and $null -ne (Read-Settings).WslConfig.Pending -and
            (Get-Output) -match 'wsl --shutdown did not finish \(exit 1\); WSL keeps its current limit, so ODS plans with that limit for now\.') 'a WSL restart that fails keeps the limit waiting, and the plan uses the limit WSL still runs with'
        Reset-Offer @($false)
        $result = Invoke-Offer $factory
        Check ($result.Plan.Model -ceq $nine -and -not (Test-Path -LiteralPath $wslConfig) -and (Get-Output) -match 'WSL memory limit not changed\.') 'a declined WSL limit changes nothing and keeps the model that fits'
        Reset-Offer @($true)
        $result = Invoke-Offer $factory -RestartPending $true
        Check ($result.Plan.Model -ceq $top -and $script:prompts.Count -eq 1 -and $script:wslCalls.Count -eq 0 -and $null -ne (Read-Settings).WslConfig.Pending) 'with a Windows restart coming anyway, WSL is not restarted, and the limit waits for that restart'
        Reset-Offer @($true)
        $script:wslPresent = $false
        $result = Invoke-Offer $factory
        Check ($result.Plan.Model -ceq $top -and $script:prompts.Count -eq 1 -and $null -eq (Read-Settings).WslConfig.Pending) 'before WSL is installed the limit applies at its first start: nothing waits'
        Reset-Offer @($true)
        $result = Invoke-Offer $noPageFile
        Check ($script:prompts[0] -match 'fixed 32 GB page file on C:' -and $script:prompts[0] -match 'commit limit' -and
            $script:prompts[0] -match 'the model runs on the CPU inside Ubuntu' -and ($script:pageFileCalls -join ',') -ceq '32' -and
            $result.RestartReason -match '32 GB page file' -and $result.Plan.Model -ceq $top -and $result.Plan.PageFileGiB -eq 32) 'accepting the page-file offer (M2) sets it once and asks for the shared restart'
        Reset-Offer @($true)
        $script:pageFileResult = [pscustomobject]@{ Result = 'canceled'; ExitCode = $null }
        $result = Invoke-Offer $noPageFile
        Check ($result.Plan.Source -ceq 'none' -and -not $result.RestartReason -and (Get-Output) -match 'did not get administrator permission' -and
            (Get-Output).Contains($helpLine)) 'a cancelled UAC prompt keeps the current plan with the explanation (M4)'
        Reset-Offer @($true)
        $script:pageFileResult = [pscustomobject]@{ Result = 'failed'; ExitCode = 5 }
        $result = Invoke-Offer $noPageFile
        Check ($result.Plan.Source -ceq 'none' -and (Get-Output) -match 'failed \(exit 5\)' -and (Get-Output) -match 'Custom size 32768 MB') 'a failed setter gives the manual steps with the size (M5)'
        Reset-Offer @($false)
        $result = Invoke-Offer $noPageFile
        Check ($result.Plan.Source -ceq 'none' -and $script:pageFileCalls.Count -eq 0 -and
            (Get-Output) -match 'Page file not changed\. No GPU model fits the memory Windows has now, so the model runs on the CPU inside Ubuntu') 'a declined page file with nothing fitting gives the CPU route (M3)'
        Reset-Offer @($false)
        $owned = Read-Layout 'memory-factory-64.json' @{ WslConfig = [pscustomobject]@{ GiB = 31.25; Owner = 'owner' } }
        $result = Invoke-Offer $owned
        Check ($script:prompts.Count -eq 1 -and $script:prompts[0] -match 'page file' -and $result.Plan.Model -ceq $nine) 'an owner''s own WSL memory= is never offered a change; the page file is'
        # Both changes: room on C: for a 32 GB page file but not 48, so the
        # WSL limit plus 32 GB is the offer for Coder Next.
        $both = Read-Layout 'memory-factory-64.json' @{ FreeGiB = [pscustomobject]@{ 'C:' = 135 } }
        $ladder = @('qwen3-coder-next-q4', $top)
        $plan = Get-Plan $both -Ladder $ladder
        Check ($plan.Offer.Model -ceq 'qwen3-coder-next-q4' -and $plan.Offer.Reason -ceq 'wsl-bound+page-file' -and $plan.Offer.PageFileGiB -eq 32 -and
            $plan.Offer.WslBoundGiB -eq 16) 'changes are tried WSL alone, page file alone, then both: here the 48 GB page file does not fit the disk, so both'
        Reset-Offer @($true, $true)
        $result = Invoke-Offer $both -Ladder $ladder
        Check ($result.Plan.Model -ceq 'qwen3-coder-next-q4' -and $script:prompts.Count -eq 2 -and $script:wslCalls.Count -eq 0 -and $result.RestartReason -and $null -ne (Read-Settings).WslConfig.Pending) 'accepting both needs no WSL restart: the page file''s Windows restart applies the WSL limit too, so the limit waits for it'
        Reset-Offer @($true, $false, $true)
        $result = Invoke-Offer $both -Ladder $ladder
        Check ($result.Plan.Model -ceq $top -and $script:prompts.Count -eq 3 -and ($script:wslCalls -join ';') -ceq '--shutdown' -and
            (Get-Output) -match 'Page file not changed\. ODS uses Qwen 3\.6 35B-A3B') 'with only the WSL limit applied, ODS plans again and gets the next rung (35B-A3B)'
    }
} finally {
    foreach ($name in $previous.Keys) { Set-Item -Path "Env:\$name" -Value $previous[$name] -ErrorAction SilentlyContinue }
    if (-not $previous.HOST_ARCH) { Remove-Item Env:\HOST_ARCH -ErrorAction SilentlyContinue }
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to clean a fixture outside the temporary directory.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks Windows RTX Spark memory contracts."
