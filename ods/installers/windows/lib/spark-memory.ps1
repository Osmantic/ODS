# Memory for an NVIDIA RTX Spark (Windows on Arm, PR B design section 4).
#
# The N1X has no memory of its own: firmware splits the RAM between the GPU
# (the carve-out nvidia-smi reports) and Windows, and Windows charges every
# GPU allocation against its commit limit (visible RAM plus the page file).
# So the model is chosen from what the memory can hold:
#   - a ladder of models proven on the pinned llama.cpp build, best first,
#     then small fallback models (config/backends/nvidia.json);
#   - the first that fits the carve-out, the commit limit and the free disk
#     as things are now is chosen;
#   - the best ladder model that fits only with a change becomes an offer: a
#     bounded WSL memory limit (no administrator rights), a larger fixed page
#     file (one UAC prompt and a restart), or both. A declined offer keeps
#     the chosen model; nothing here ever stops setup.
# The commit rule itself (Get-ODSNativeCommitFit) lives in
# native-llama-runtime.ps1, which the launcher and the model controller use too.
#
# Requires wsl-portal-amd.ps1 (tier-map.ps1, native-llama-runtime.ps1) and,
# for the offers, wsl-portal-setup.ps1 (Confirm-ODSPortalPreparation,
# Invoke-ODSPortalWsl, the Docker Desktop helpers) in scope. Setup reaches it
# through the native RTX Spark route (wsl-portal-spark-route.ps1).

. (Join-Path $PSScriptRoot 'pagefile-entries.ps1')
. (Join-Path $PSScriptRoot 'wsl-memory-config.ps1')

# Constants with their evidence (PR B design 4.1). The Windows reserve and the
# commit margin are the launcher's ($script:ODSNativeWindowsReserveGiB, 16, and
# $script:ODSNativeCommitMarginGiB, 2).
$script:ODSSparkPageFileStepGiB = 16       # finer than 32: less disk
$script:ODSSparkPageFileMaxGiB = 256       # set-pagefile.ps1 ValidateRange(16,256)
$script:ODSSparkWslBoundGiB = 16           # D5; re-measured on hardware before the route ships
$script:ODSSparkWslFloorGiB = $script:ODSWslMemoryFloorGiB  # 12: ODS never writes less (wsl-memory-config.ps1)
$script:ODSSparkStackReserveFreshGiB = 40  # Ubuntu, Docker Desktop and images on a fresh PC
$script:ODSSparkStackReserveGiB = 10       # when Docker Desktop's data disk already exists
$script:ODSSparkDiskMarginGiB = 10
$script:ODSSparkMinContext = 65536
$script:ODSSparkMemoryKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management'

# --- Layout (I/O) -----------------------------------------------------------------

function Get-ODSPagingFilesValue {
    # The PagingFiles registry value, readable without administrator rights.
    $value = Get-ItemProperty -Path $script:ODSSparkMemoryKey -Name PagingFiles -ErrorAction SilentlyContinue
    if ($null -eq $value) { return @() }
    return @($value.PagingFiles | Where-Object { $_ })
}

function Get-ODSSparkWslNowGiB {
    # The commit charge of WSL's virtual machine, or $null when Windows does
    # not show it to this unelevated account.
    $process = @(Get-Process -Name vmmemWSL, vmmem -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $process.Count) { return $null }
    $bytes = [double]$process[0].PagedMemorySize64
    if ($bytes -le 0) { return $null }
    return [math]::Round($bytes / 1GB, 2)
}

function Get-ODSSparkRunningModelGiB([string]$RuntimeDir) {
    # The ODS model's planned commit while its readiness record is valid.
    $readyPath = Join-Path $RuntimeDir 'ready.json'
    if (-not (Test-Path -LiteralPath $readyPath -PathType Leaf)) { return 0.0 }
    $ready = Read-ODSNativeLlamaJsonOrNull $readyPath
    if ($null -eq $ready -or $ready.Error -or -not $ready.ModelId) { return 0.0 }
    $memory = Read-ODSNativeMemoryPlan (Join-Path $RuntimeDir 'memory-plan.json') ([pscustomobject]@{ GgufFile = $ready.ModelId; ContextSize = $ready.ContextSize })
    if (-not $memory.Plan) { return 0.0 }
    return [double]$memory.Plan.CommitNeedGiB
}

function Get-ODSDriveFreeGiB([string]$Drive) {
    return [math]::Round(([IO.DriveInfo]::new("$Drive\")).AvailableFreeSpace / 1GB, 2)
}

function Get-ODSSparkMemoryLayout {
    <#
    .SYNOPSIS
        What the memory planner works from, read without administrator
        rights. Its JSON shape is the shape of the test fixtures
        (tests/fixtures/rtx-spark/memory-*.json).
    #>
    param([Parameter(Mandatory = $true)][long]$CarveOutMiB, [string]$ModelsDir = (Get-ODSPortalModelsDir))
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    $drive = [string]$env:SystemDrive
    $usage = @(Get-CimInstance -ClassName Win32_PageFileUsage -ErrorAction Stop)
    $system = @($usage | Where-Object { ([string]$_.Name).StartsWith("$drive\", [StringComparison]::OrdinalIgnoreCase) })
    $activeGiB = [double](@($system | ForEach-Object { [double]$_.AllocatedBaseSize }) | Measure-Object -Sum).Sum / 1024
    $otherGiB = [double](@($usage | Where-Object { -not ([string]$_.Name).StartsWith("$drive\", [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { [double]$_.AllocatedBaseSize }) | Measure-Object -Sum).Sum / 1024
    $automatic = [bool](Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).AutomaticManagedPagefile
    $paging = @(Get-ODSPagingFilesValue)
    $entry = Get-ODSSystemPagingFile $paging $drive
    # What applies after the next restart. A system-managed or automatic page
    # file has no fixed size: what Windows allocated now stands for it.
    $configuredGiB = switch ($entry.Kind) {
        'fixed' { [double][math]::Max($entry.InitialMB, $entry.MaximumMB) / 1024 }
        'none' { 0.0 }
        default { $activeGiB }
    }
    if ($automatic) { $configuredGiB = $activeGiB }
    # The WSL limit in effect: one ODS wrote that waits for WSL to restart is
    # PendingGiB, never planned with (PR B design 4.3).
    $wsl = Get-ODSWslConfigState
    $modelsDrive = if ($ModelsDir -match '^([A-Za-z]:)') { $Matches[1].ToUpperInvariant() } else { $drive }
    $free = [ordered]@{}
    foreach ($name in @($drive.ToUpperInvariant(), $modelsDrive) | Select-Object -Unique) { $free[$name] = Get-ODSDriveFreeGiB $name }
    $limit = [double]$os.TotalVirtualMemorySize / 1MB
    return [pscustomobject]@{
        VisibleGiB = [math]::Round([double]$os.TotalVisibleMemorySize / 1MB, 2)
        CommitLimitGiB = [math]::Round($limit, 2)
        CommittedGiB = [math]::Round($limit - [double]$os.FreeVirtualMemory / 1MB, 2)
        CarveOutMiB = $CarveOutMiB
        SystemDrive = $drive.ToUpperInvariant()
        PageFileActiveGiB = [math]::Round($activeGiB, 2)
        PageFileConfiguredGiB = [math]::Round($configuredGiB, 2)
        OtherPageFileGiB = [math]::Round($otherGiB, 2)
        PageFileAutomatic = $automatic
        PageFilePending = (-not $automatic -and $entry.Kind -in @('fixed', 'none') -and [math]::Abs($configuredGiB - $activeGiB) -ge 0.5)
        PagingFiles = $paging
        WslConfig = $wsl
        WslNowGiB = (Get-ODSSparkWslNowGiB)
        RunningModelGiB = (Get-ODSSparkRunningModelGiB (Get-ODSPortalRuntimeDir))
        ModelsDrive = $modelsDrive
        FreeGiB = [pscustomobject]$free
        StackPresent = (Test-Path -LiteralPath ([IO.Path]::Combine($env:LOCALAPPDATA, 'Docker', 'wsl', 'disk', 'docker_data.vhdx')) -PathType Leaf)
    }
}

# --- Pure planning ----------------------------------------------------------------

function Get-ODSSparkWslMemory($Layout) {
    # The WSL memory limit in effect: Source 'wslconfig' (the owner's
    # memory=), 'ods' (the value ODS wrote, once WSL runs with it) or
    # 'default' (half of what Windows sees).
    if ($Layout.WslConfig -and $null -ne $Layout.WslConfig.GiB) {
        $source = if ([string]$Layout.WslConfig.Owner -ceq 'ods') { 'ods' } else { 'wslconfig' }
        return [pscustomobject]@{ GiB = [double]$Layout.WslConfig.GiB; Source = $source }
    }
    return [pscustomobject]@{ GiB = (Get-ODSWslDefaultMemoryGiB ([double]$Layout.VisibleGiB)); Source = 'default' }
}

function Get-ODSSparkOtherCommitGiB($Layout) {
    return (Get-ODSNativeOtherCommitGiB ([double]$Layout.CommittedGiB) ([double]$Layout.RunningModelGiB) $Layout.WslNowGiB)
}

function Get-ODSSparkCommitCapacityGiB($Layout, [double]$PageFileGiB, [double]$WslGiB) {
    # What Windows could lend the model with this system-drive page file and
    # this WSL limit (the shared commit rule).
    $limit = [double]$Layout.VisibleGiB + $PageFileGiB + [double]$Layout.OtherPageFileGiB
    return (Get-ODSNativeCommitFit -CommitNeedGiB 0 -CommitLimitGiB $limit -OtherCommitGiB (Get-ODSSparkOtherCommitGiB $Layout) -WslGiB $WslGiB).CapacityGiB
}

function Get-ODSSparkPageFileTargetGiB($Layout, [double]$CommitNeedGiB, [double]$WslGiB) {
    <#
    .SYNOPSIS
        $null when the configured page file is enough; else the smallest
        multiple of 16 GiB, never below the configured system page file, that
        fits; -1 when even 256 GiB is not enough.
    #>
    $configured = [double]$Layout.PageFileConfiguredGiB
    if ($CommitNeedGiB -le (Get-ODSSparkCommitCapacityGiB $Layout $configured $WslGiB) + 1e-9) { return $null }
    $size = [math]::Ceiling($configured / $script:ODSSparkPageFileStepGiB) * $script:ODSSparkPageFileStepGiB
    if ($size -lt $script:ODSSparkPageFileStepGiB) { $size = $script:ODSSparkPageFileStepGiB }
    while ($size -le $script:ODSSparkPageFileMaxGiB) {
        if ($size -ge $configured -and $CommitNeedGiB -le (Get-ODSSparkCommitCapacityGiB $Layout $size $WslGiB) + 1e-9) { return [int]$size }
        $size += $script:ODSSparkPageFileStepGiB
    }
    return -1
}

function Get-ODSSparkFreeGiB($Layout, [string]$Drive) {
    $free = $Layout.FreeGiB
    if ($null -eq $free) { return 0.0 }
    $value = if ($free -is [Collections.IDictionary]) { $free[$Drive] } else { $free.PSObject.Properties[$Drive].Value }
    if ($null -eq $value) { return 0.0 }
    return [double]$value
}

function Get-ODSSparkDiskNeedGiB {
    <#
    .SYNOPSIS
        Disk a plan needs on each drive (pure): files still to download, the
        page file's growth on the system drive, room for Ubuntu and Docker
        and a margin. Fits when every drive has that much free.
    #>
    param($Layout, [object[]]$Artifacts, [string]$ModelsDir, [double]$PageFileGrowthGiB = 0, [bool]$StackPresent = $false,
        [object[]]$PresentFiles = @())
    $present = @{}
    foreach ($name in @($PresentFiles)) { if ($name) { $present[([string]$name).ToLowerInvariant()] = $true } }
    $bytes = [double]0
    foreach ($artifact in @($Artifacts | Where-Object { $_ })) {
        # A file is downloaded once, even when the model is also setup's test model.
        $key = ([string]$artifact.File).ToLowerInvariant()
        if (-not $present.ContainsKey($key)) { $bytes += [double]$artifact.SizeBytes; $present[$key] = $true }
    }
    $download = [math]::Round($bytes / 1GB, 2)
    $reserve = if ($StackPresent) { $script:ODSSparkStackReserveGiB } else { $script:ODSSparkStackReserveFreshGiB }
    $modelsDrive = if ($ModelsDir -match '^([A-Za-z]:)') { $Matches[1].ToUpperInvariant() } else { [string]$Layout.ModelsDrive }
    $systemDrive = ([string]$Layout.SystemDrive).ToUpperInvariant()
    $needs = [ordered]@{}
    $needs[$modelsDrive] = $download + $reserve + $script:ODSSparkDiskMarginGiB
    if ($PageFileGrowthGiB -gt 0) {
        if ($needs.Contains($systemDrive)) { $needs[$systemDrive] += $PageFileGrowthGiB }
        else { $needs[$systemDrive] = $PageFileGrowthGiB + $script:ODSSparkDiskMarginGiB }
    }
    $short = ''
    foreach ($name in $needs.Keys) {
        if ($needs[$name] -gt (Get-ODSSparkFreeGiB $Layout $name) + 1e-9) { $short = $name; break }
    }
    return [pscustomobject]@{
        DownloadGiB = $download
        NeedGiB = [math]::Round($needs[$modelsDrive], 2)
        Drive = $modelsDrive
        FreeGiB = (Get-ODSSparkFreeGiB $Layout $modelsDrive)
        Fits = (-not $short)
        ShortDrive = $short
        ShortNeedGiB = $(if ($short) { [math]::Round($needs[$short], 2) } else { 0.0 })
        ShortFreeGiB = $(if ($short) { Get-ODSSparkFreeGiB $Layout $short } else { 0.0 })
    }
}

function Get-ODSSparkCandidate($Catalog, [string]$Id, $Layout, [int]$MinContext, [string]$HostArchitecture, [bool]$OwnContext) {
    # One ladder or fallback entry sized for this machine, or Reason why not:
    # missing, artifacts, context or device.
    $model = Get-CatalogModelById -Catalog $Catalog -ModelId $Id
    if (-not $model) { return [pscustomobject]@{ Id = $Id; Reason = 'missing' } }
    $artifacts = Test-ODSCatalogModelArtifacts -Model $model
    if ($null -eq $artifacts) { return [pscustomobject]@{ Id = $Id; Reason = 'artifacts' } }
    $gpuInfo = @{ Backend = 'nvidia'; MemoryType = 'discrete'; VramMB = [int]$Layout.CarveOutMiB }
    $runtimeProfile = Get-CatalogRuntimeProfile -Model $model -GpuInfo $gpuInfo -SystemRamGB ([int][math]::Floor([double]$Layout.VisibleGiB)) `
        -HostArchitecture $HostArchitecture
    $context = if ($runtimeProfile -and $runtimeProfile.context_length) { [int]$runtimeProfile.context_length } else { [int]$model.context_length }
    # A fallback model may run at its own catalog context.
    if (-not $OwnContext -and $context -lt $MinContext) { return [pscustomobject]@{ Id = $Id; Reason = 'context' } }
    $need = Get-CatalogModelMemoryNeed -Model $model -RuntimeProfile $runtimeProfile -ContextLength $context
    $carveOutGiB = [double]$Layout.CarveOutMiB / 1024
    $deviceCapacity = $carveOutGiB - (Get-CatalogFitMarginGB -CapacityGB $carveOutGiB -MemoryClass 'discrete')
    if ($need.DeviceNeedGiB -gt $deviceCapacity + 1e-9) { return [pscustomobject]@{ Id = $Id; Reason = 'device' } }
    return [pscustomobject]@{ Id = $Id; Reason = ''; Name = [string]$model.name; ContextSize = $need.ContextLength
        DeviceNeedGiB = $need.DeviceNeedGiB; CommitNeedGiB = $need.CommitNeedGiB; Artifacts = @($artifacts) }
}

function Get-ODSSparkMemoryOffer($Candidate, $Layout, [double]$PageFileGiB, $Wsl, [bool]$AllowPageFile, [bool]$AllowWslBound, $DiskArguments) {
    # The first change that fits this candidate: the WSL limit alone (no
    # administrator rights), the page file alone, then both. A change counts
    # only when allowed and when the disk has room for page-file growth.
    $bound = [double]$script:ODSSparkWslBoundGiB
    $canBound = $AllowWslBound -and $Wsl.Source -cin @('default', 'ods') -and [double]$Wsl.GiB -gt $bound + 1e-9
    $options = @()
    if ($canBound) { $options += ,@($true, $false) }
    if ($AllowPageFile) { $options += ,@($false, $true) }
    if ($canBound -and $AllowPageFile) { $options += ,@($true, $true) }
    foreach ($option in $options) {
        $wslGiB = if ($option[0]) { $bound } else { [double]$Wsl.GiB }
        $target = $null
        if ($option[1]) {
            $target = Get-ODSSparkPageFileTargetGiB $Layout $Candidate.CommitNeedGiB $wslGiB
            if ($null -eq $target -or $target -lt 0) { continue }
            $disk = Get-ODSSparkDiskNeedGiB @DiskArguments -Artifacts $Candidate.AllArtifacts -PageFileGrowthGiB ([double]$target - $PageFileGiB)
            if (-not $disk.Fits) { continue }
        } elseif ($Candidate.CommitNeedGiB -gt (Get-ODSSparkCommitCapacityGiB $Layout $PageFileGiB $wslGiB) + 1e-9) {
            continue
        } else {
            $disk = Get-ODSSparkDiskNeedGiB @DiskArguments -Artifacts $Candidate.AllArtifacts
        }
        $reason = @($(if ($option[0]) { 'wsl-bound' }), $(if ($option[1]) { 'page-file' }) | Where-Object { $_ }) -join '+'
        $pageFile = if ($null -ne $target) { [double]$target } else { $PageFileGiB }
        return [pscustomobject]@{ Model = $Candidate.Id; Name = $Candidate.Name; ContextSize = $Candidate.ContextSize
            DeviceNeedGiB = $Candidate.DeviceNeedGiB; CommitNeedGiB = $Candidate.CommitNeedGiB; Artifacts = $Candidate.Artifacts
            PageFileGiB = $target; WslBoundGiB = $(if ($option[0]) { [int]$bound } else { $null }); DiskNeedGiB = $disk.NeedGiB
            WslGiB = $wslGiB; CommitCapacityGiB = (Get-ODSSparkCommitCapacityGiB $Layout $pageFile $wslGiB); Reason = $reason }
    }
    return $null
}

function Select-ODSSparkModelPlan {
    <#
    .SYNOPSIS
        Choose the RTX Spark model from the ladder and the fallback list
        (pure; PR B design 4.2).
    .DESCRIPTION
        Only ladder and fallback ids are candidates, so every candidate is
        proven on the pinned build. A candidate is skipped when it is not in
        the catalog, cannot be installed and verified, would run below
        MinContext (fallbacks may use their own catalog context), does not fit
        the carve-out, or does not fit the free disk with its own downloads.
        The first one that fits the commit limit as things are now is chosen.
        The best ladder entry above it that fits only with a change (the WSL
        limit, the page file, or both, when allowed) is the Offer.
        Source is 'none' when nothing fits now: the caller offers the CPU route.
    #>
    param([object]$Catalog, [string[]]$Ladder, [string[]]$Fallback, $Layout, [string]$ModelsDir, [object[]]$PresentFiles = @(),
        [bool]$AllowPageFile, [bool]$AllowWslBound, [int]$MinContext = $script:ODSSparkMinContext, [string]$HostArchitecture = 'arm64',
        # Files setup downloads whatever the model (the llama.cpp archives, the
        # test model), each { File; SizeBytes }; counted unless present.
        [object[]]$SetupArtifacts = @())
    $wsl = Get-ODSSparkWslMemory $Layout
    $pageFile = [double]$Layout.PageFileConfiguredGiB
    $diskArguments = @{ Layout = $Layout; ModelsDir = $ModelsDir; StackPresent = [bool]$Layout.StackPresent; PresentFiles = @($PresentFiles) }
    $capacity = Get-ODSSparkCommitCapacityGiB $Layout $pageFile ([double]$wsl.GiB)
    $skipped = [Collections.Generic.List[object]]::new()
    $candidates = @()
    foreach ($entry in @(@($Ladder | ForEach-Object { @{ Id = $_; Rung = 'ladder' } }) + @($Fallback | ForEach-Object { @{ Id = $_; Rung = 'fallback' } }))) {
        if (-not $entry.Id) { continue }
        $candidate = Get-ODSSparkCandidate $Catalog ([string]$entry.Id) $Layout $MinContext $HostArchitecture ($entry.Rung -ceq 'fallback')
        if ($candidate.Reason) { $skipped.Add([pscustomobject]@{ Id = $entry.Id; Reason = $candidate.Reason }); continue }
        $candidate | Add-Member -NotePropertyName Rung -NotePropertyValue $entry.Rung
        $candidate | Add-Member -NotePropertyName AllArtifacts -NotePropertyValue (@($candidate.Artifacts) + @($SetupArtifacts))
        $disk = Get-ODSSparkDiskNeedGiB @diskArguments -Artifacts $candidate.AllArtifacts
        $candidate | Add-Member -NotePropertyName Disk -NotePropertyValue $disk
        if (-not $disk.Fits) { $skipped.Add([pscustomobject]@{ Id = $entry.Id; Reason = 'disk' }); continue }
        $candidates += $candidate
    }
    $chosen = $null
    foreach ($candidate in $candidates) {
        if ($candidate.CommitNeedGiB -le $capacity + 1e-9) { $chosen = $candidate; break }
        $skipped.Add([pscustomobject]@{ Id = $candidate.Id; Reason = 'commit' })
    }
    $offer = $null
    foreach ($candidate in @($candidates | Where-Object { $_.Rung -ceq 'ladder' })) {
        # Only rungs above the chosen one (ids are unique within the ladder).
        if ($chosen -and $chosen.Rung -ceq 'ladder' -and $candidate.Id -ceq $chosen.Id) { break }
        $offer = Get-ODSSparkMemoryOffer $candidate $Layout $pageFile $wsl $AllowPageFile $AllowWslBound $diskArguments
        if ($offer) { break }
    }
    return [pscustomobject]@{
        Model = $(if ($chosen) { $chosen.Id } else { $null })
        Name = $(if ($chosen) { $chosen.Name } else { $null })
        ContextSize = $(if ($chosen) { $chosen.ContextSize } else { $null })
        DeviceNeedGiB = $(if ($chosen) { $chosen.DeviceNeedGiB } else { $null })
        CommitNeedGiB = $(if ($chosen) { $chosen.CommitNeedGiB } else { $null })
        Artifacts = $(if ($chosen) { $chosen.Artifacts } else { @() })
        PageFileGiB = $null
        WslBoundGiB = $null
        WslGiB = [double]$wsl.GiB
        WslSource = $wsl.Source
        CommitCapacityGiB = $capacity
        DiskNeedGiB = $(if ($chosen) { $chosen.Disk.NeedGiB } else { $null })
        Source = $(if ($chosen) { $chosen.Rung } else { 'none' })
        Offer = $offer
        Skipped = @($skipped)
    }
}

function ConvertTo-ODSSparkAcceptedPlan($Plan) {
    # The plan once the owner accepted its offer: the offered model, sized
    # with the changed page file and WSL limit.
    $offer = $Plan.Offer
    return [pscustomobject]@{ Model = $offer.Model; Name = $offer.Name; ContextSize = $offer.ContextSize
        DeviceNeedGiB = $offer.DeviceNeedGiB; CommitNeedGiB = $offer.CommitNeedGiB; Artifacts = $offer.Artifacts
        PageFileGiB = $offer.PageFileGiB; WslBoundGiB = $offer.WslBoundGiB; WslGiB = $offer.WslGiB
        WslSource = $(if ($offer.WslBoundGiB) { 'ods' } else { $Plan.WslSource })
        CommitCapacityGiB = $offer.CommitCapacityGiB; DiskNeedGiB = $offer.DiskNeedGiB; Source = 'ladder'; Offer = $null
        Skipped = $Plan.Skipped }
}

function Get-ODSSparkPlanMemoryRecord($Plan) {
    # memory-plan.json for the chosen model (New-ODSPortalMemoryPlanRecord).
    $catalogPlan = [pscustomobject]@{ GgufFile = [string]@($Plan.Artifacts)[0].File; ContextSize = [long]$Plan.ContextSize }
    $need = [pscustomobject]@{ DeviceNeedGiB = $Plan.DeviceNeedGiB; CommitNeedGiB = $Plan.CommitNeedGiB; Source = 'catalog' }
    return (New-ODSPortalMemoryPlanRecord $catalogPlan $need ([double]$Plan.WslGiB))
}

# --- Page file (one UAC prompt) -----------------------------------------------------

function Start-ODSElevatedProcess([string]$FilePath, [string]$Arguments) {
    # Windows shows its UAC prompt; a cancelled prompt throws Win32Exception
    # 1223 here.
    $start = [Diagnostics.ProcessStartInfo]::new($FilePath, $Arguments)
    $start.UseShellExecute = $true
    $start.Verb = 'runas'
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    return [Diagnostics.Process]::Start($start)
}

function Set-ODSSparkPageFile([int]$SizeGiB) {
    <#
    .SYNOPSIS
        Set a fixed SizeGiB page file on the system drive through
        set-pagefile.ps1, elevated with one UAC prompt. Windows applies it at
        the next restart.
    .OUTPUTS
        Result 'set' (confirmed in the registry), 'canceled' (the owner said
        no to UAC) or 'failed', and ExitCode. Never throws for a cancel.
    #>
    $drive = [string]$env:SystemDrive
    $original = @(Get-ODSPagingFilesValue)
    $automatic = [bool](Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).AutomaticManagedPagefile
    Save-ODSWindowsPageFileOriginal -PagingFiles $original -Automatic $automatic
    $helper = Join-Path (Split-Path -Parent $PSScriptRoot) 'set-pagefile.ps1'
    $shell = [IO.Path]::Combine([Environment]::SystemDirectory, 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $helper + '" -SizeGB ' + $SizeGiB
    try {
        $process = Start-ODSElevatedProcess $shell $arguments
    } catch {
        $cause = $_.Exception
        while ($cause -and $cause -isnot [ComponentModel.Win32Exception]) { $cause = $cause.InnerException }
        if (-not $cause) { throw }
        if ($cause.NativeErrorCode -eq 1223) { return [pscustomobject]@{ Result = 'canceled'; ExitCode = $null } }
        return [pscustomobject]@{ Result = 'failed'; ExitCode = $cause.NativeErrorCode }
    }
    try {
        $process.WaitForExit()
        $code = $process.ExitCode
    } finally { $process.Dispose() }
    if ($code -ne 0) { return [pscustomobject]@{ Result = 'failed'; ExitCode = $code } }
    # Confirm what Windows will apply, unelevated.
    $now = Get-ODSSystemPagingFile @(Get-ODSPagingFilesValue) $drive
    if ($now.Kind -ne 'fixed' -or $now.InitialMB -ne $SizeGiB * 1024 -or $now.MaximumMB -ne $SizeGiB * 1024) {
        return [pscustomobject]@{ Result = 'failed'; ExitCode = 0 }
    }
    Complete-ODSWindowsPageFileChange -SizeGB $SizeGiB
    return [pscustomobject]@{ Result = 'set'; ExitCode = 0 }
}

# --- Offers (consent) ----------------------------------------------------------------

function Format-ODSSparkModelName($Plan) {
    if ($Plan -and $Plan.Model) { return "$($Plan.Name) ($([math]::Round([double]$Plan.ContextSize / 1024))K context)" }
    return 'the model on the CPU inside Ubuntu (slower)'
}

function Get-ODSSparkKeptPlanNotice($Plan, $Offer, [string]$Lead) {
    # M3: what ODS uses after a declined or impossible change.
    if ($Plan.Model) {
        return ("$Lead ODS uses $(Format-ODSSparkModelName $Plan) (about $(Format-ODSNativeGiB $Plan.DeviceNeedGiB) GiB), the largest model proven on " +
            "this PC that fits the memory Windows has now. To use $($Offer.Name) later, rerun the same install.ps1 command and answer yes. $script:ODSNativeHelpLine")
    }
    return ("$Lead No GPU model fits the memory Windows has now, so the model runs on the CPU inside Ubuntu (slower). To use $($Offer.Name) " +
        "later, rerun the same install.ps1 command and answer yes. $script:ODSNativeHelpLine")
}

function Get-ODSSparkPageFileOfferText($Plan, $Offer, $Layout, [string]$Fallback) {
    # M2.
    $other = Get-ODSSparkOtherCommitGiB $Layout
    $required = [double]$Offer.CommitNeedGiB + $other + [double]$Offer.WslGiB + $script:ODSNativeCommitMarginGiB
    $drive = [string]$Layout.SystemDrive
    $free = [math]::Round((Get-ODSSparkFreeGiB $Layout $drive) * 1.073741824, 1)
    $instead = if ($Plan.Model) { "ODS installs $Fallback now" } else { 'the model runs on the CPU inside Ubuntu (slower)' }
    return ("To run $($Offer.Name) (about $(Format-ODSNativeGiB $Offer.DeviceNeedGiB) GiB on the GPU), Windows needs a memory limit (commit " +
        "limit) of about $(Format-ODSNativeGiB $required) GiB; it has $(Format-ODSNativeGiB $Layout.CommitLimitGiB) GiB. Windows counts GPU memory " +
        "against that limit, so a fixed $($Offer.PageFileGiB) GB page file on $drive raises it. It uses $($Offer.PageFileGiB) GB of disk ($free GB free). " +
        'Set it now? Windows asks for administrator permission once, then a restart is needed; setup continues by itself after you sign in. ' +
        "If you say no, $instead and you can rerun setup later.")
}

function Get-ODSSparkWslOfferText($Plan, [int]$Bound) {
    # M8.
    return ("Ubuntu and Docker (WSL) may use up to $(Format-ODSNativeGiB $Plan.WslGiB) GB of memory here, and Windows counts that against the " +
        "same limit the model needs. Limit WSL to $Bound GB? ODS adds memory=${Bound}GB under [wsl2] in $(Get-ODSWslConfigPath), marked as set " +
        "by ODS. It applies to all your WSL distributions; the old file is kept in $(Get-ODSWindowsSettingsDir), and " +
        "$(Get-ODSWindowsRestoreScriptPath) puts it back. It takes effect when WSL restarts.")
}

function Test-ODSSparkWslPresent {
    # Without wsl.exe nothing runs in WSL yet: a new limit applies at its first start.
    return [bool](Get-Command wsl.exe -CommandType Application -ErrorAction SilentlyContinue)
}

function Restart-ODSSparkWsl([bool]$NonInteractive) {
    # M9: only with consent, and Docker Desktop is started again afterwards.
    $consent = 'Restart WSL now so the new limit applies? This stops every WSL distribution: Ubuntu, ODS''s services and any Portal agent ' +
        'task that is running, and Docker Desktop''s containers. Save any work open in Ubuntu first. Setup starts Docker Desktop again afterwards.'
    if (-not (Confirm-ODSPortalPreparation $consent $NonInteractive)) {
        Write-Host ("         WSL keeps its current limit until it restarts, so ODS plans with that limit for now. To use the new one, restart " +
            "Windows, or rerun setup and let it restart WSL. $script:ODSNativeHelpLine")
        return $false
    }
    $result = Invoke-ODSPortalWsl -Arguments @('--shutdown')
    if ($result.Code -ne 0) {
        Write-Host ("         wsl --shutdown did not finish (exit $($result.Code)); WSL keeps its current limit, so ODS plans with that limit for now. " +
            "Restart Windows, or rerun setup to try again. $script:ODSNativeHelpLine")
        return $false
    }
    $desktop = Get-ODSPortalDockerDesktop
    if ($desktop.Installed -and -not (Test-ODSPortalDockerEngine $desktop)) { Start-ODSPortalDockerDesktop $desktop }
    return $true
}

function Get-ODSSparkOfferLead($Offer, [bool]$WslWritten, [bool]$WslEffective, [bool]$PageFileSet) {
    if ($Offer.PageFileGiB -and -not $PageFileSet) { return 'Page file not changed.' }
    if ($Offer.WslBoundGiB -and -not $WslWritten) { return 'WSL memory limit not changed.' }
    if ($Offer.WslBoundGiB -and -not $WslEffective) { return 'WSL gets the new memory limit only after it restarts.' }
    return ''
}

function Invoke-ODSSparkMemoryOffers {
    <#
    .SYNOPSIS
        Steps 6 and 7 of the RTX Spark foundation (PR B design 4.2): a
        page-file change waiting for a restart, then the WSL limit (M8) and
        the page-file offer (M2) for the plan's Offer, then a WSL restart (M9)
        when no Windows restart will apply the new limit.
    .DESCRIPTION
        PlanArguments are Select-ODSSparkModelPlan's (without the Allow
        switches). A declined, cancelled or failed change keeps the plan that
        fits now (M3, M4, M5); nothing here stops setup. RestartPending says
        Windows restarts anyway (WSL's own features).
    .OUTPUTS
        Plan (Select-ODSSparkModelPlan's shape, Offer resolved), RestartReason
        ('' or the text the restart notice shows) and Done (what changed).
    #>
    param([hashtable]$PlanArguments, [bool]$NonInteractive, [bool]$RestartPending = $false)
    $layout = $PlanArguments.Layout
    if ($layout.PageFilePending) {
        # M24: plan with the size Windows applies at the restart; no offer,
        # no second UAC prompt.
        $plan = Select-ODSSparkModelPlan @PlanArguments -AllowPageFile $false -AllowWslBound $false
        return [pscustomobject]@{ Plan = $plan; Done = @()
            RestartReason = ('The page file change waits for a restart. Use Restart, not Shut down (with Fast Startup, Shut down may ' +
                'not apply it). Setup continues by itself after you sign in.') }
    }
    $plan = Select-ODSSparkModelPlan @PlanArguments -AllowPageFile $true -AllowWslBound $true
    $offer = $plan.Offer
    if (-not $offer) { return [pscustomobject]@{ Plan = $plan; RestartReason = ''; Done = @() } }
    $fallback = Format-ODSSparkModelName $plan
    # An earlier run wrote this limit and WSL has not restarted since: only
    # the restart (M9) is left.
    $pending = $layout.WslConfig.PendingGiB
    $alreadyWritten = [bool]$offer.WslBoundGiB -and $null -ne $pending -and [math]::Abs([double]$pending - [double]$offer.WslBoundGiB) -lt 0.01
    if ($NonInteractive) {
        Write-Host "         $(Get-ODSSparkKeptPlanNotice $plan $offer (Get-ODSSparkOfferLead $offer $alreadyWritten $false $false))"
        return [pscustomobject]@{ Plan = $plan; RestartReason = ''; Done = @() }
    }
    $done = @()
    $restartReason = ''
    $wslWritten = $alreadyWritten
    if ($alreadyWritten) {
        Write-Host "         ODS already limited WSL to $($offer.WslBoundGiB) GB in $(Get-ODSWslConfigPath); WSL applies it when it restarts."
    } elseif ($offer.WslBoundGiB -and (Confirm-ODSPortalPreparation (Get-ODSSparkWslOfferText $plan $offer.WslBoundGiB) $false)) {
        $written = Write-ODSWslConfig $offer.WslBoundGiB
        if ($written.Result -ceq 'written') {
            $wslWritten = $true
            $done += "WSL memory limited to $($offer.WslBoundGiB) GB in $($written.Path)"
        } else {
            Write-Host "         ODS did not change $($written.Path) (it is not a plain text file ODS can edit safely). $script:ODSNativeHelpLine"
        }
    }
    $pageFileSet = $false
    if ($offer.PageFileGiB -and (Confirm-ODSPortalPreparation (Get-ODSSparkPageFileOfferText $plan $offer $layout $fallback) $false)) {
        $result = Set-ODSSparkPageFile $offer.PageFileGiB
        if ($result.Result -ceq 'set') {
            $pageFileSet = $true
            $done += "page file on $($layout.SystemDrive) set to $($offer.PageFileGiB) GB"
            $restartReason = "A $($offer.PageFileGiB) GB page file was set for the GPU model; Windows applies it after a restart."
        } elseif ($result.Result -ceq 'canceled') {
            Write-Host ("         Windows did not get administrator permission (the prompt was cancelled), so the page file was not changed. " +
                "ODS uses $fallback now; rerun setup to try again. On a standard Windows account, an administrator must approve that " +
                "prompt, or set the page file by hand with the steps setup prints when it fails. $script:ODSNativeHelpLine")
        } else {
            Write-Host ("         Setting the page file failed (exit $($result.ExitCode)), so it was not changed and ODS uses $fallback. To set it " +
                'yourself (needs an administrator): System Properties > Advanced > Performance > Settings > Advanced > Virtual memory > ' +
                "Change; clear `"Automatically manage`", select $($layout.SystemDrive), Custom size $($offer.PageFileGiB * 1024) MB for both " +
                "sizes, Set, OK, then restart and rerun setup. $script:ODSNativeHelpLine")
        }
    }
    # A Windows restart (the page file, or WSL's own features) applies the
    # new WSL limit; setup stops for it before stage 3, and the record keeps
    # the limit pending until Windows has started again. Otherwise WSL
    # itself must restart, with consent; on a no, the limit in effect stays
    # the one planned with, in stage 3 and on reruns, until WSL restarts.
    $wslEffective = $false
    if ($wslWritten) {
        if ($RestartPending -or $pageFileSet) {
            $wslEffective = $true
        } elseif (-not (Test-ODSSparkWslPresent) -or (Restart-ODSSparkWsl $false)) {
            Complete-ODSWindowsWslConfigApplied
            $wslEffective = $true
        }
    }
    $lead = Get-ODSSparkOfferLead $offer $wslWritten $wslEffective $pageFileSet
    if (-not $lead) {
        return [pscustomobject]@{ Plan = (ConvertTo-ODSSparkAcceptedPlan $plan); RestartReason = $restartReason; Done = $done }
    }
    # Plan again with the WSL limit that applies; a page file waiting for
    # its restart is planned after that restart.
    $arguments = @{} + $PlanArguments
    if ($wslEffective) {
        $changed = $layout.PSObject.Copy()
        $changed.WslConfig = [pscustomobject]@{ GiB = [double]$offer.WslBoundGiB; Owner = 'ods'; PendingGiB = $null }
        $arguments.Layout = $changed
    }
    $kept = Select-ODSSparkModelPlan @arguments -AllowPageFile $false -AllowWslBound $false
    Write-Host "         $(Get-ODSSparkKeptPlanNotice $kept $offer $lead)"
    return [pscustomobject]@{ Plan = $kept; RestartReason = $restartReason; Done = $done }
}
