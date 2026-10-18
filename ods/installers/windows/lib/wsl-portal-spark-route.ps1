# NVIDIA RTX Spark (Windows on Arm with an NVIDIA CUDA GPU): the native
# llama.cpp model route of the Windows -> WSL Portal setup.
#
# Stage 1, Initialize-ODSPortalSparkFoundation, runs before Windows' own
# prerequisites, in this order; nothing is downloaded or changed before d:
#   a. what ODS already runs: a model server ODS does not manage that is live
#      stops setup with nothing changed; a disabled one is replaced in stage 3,
#      after its task and files are saved;
#   b. the generic disk and virtualization checks;
#   c. the memory layout and the disk plan for the ladder and fallback models
#      (config/backends/nvidia.json), with no prompts;
#   d. the pinned llama.cpp CUDA build: consent, install, then a check on the
#      GPU with the small test model, cached per build, driver and GPU. On a
#      fresh install a build that cannot run here gives the CPU route; a
#      missing Visual C++ runtime for Arm64 is offered once;
#   e. the memory offers (WSL limit, page file); a page-file change shares
#      Windows' one restart.
# Stage 3, Add-ODSPortalSparkArguments, runs instead of the NVIDIA container
# gate: it stops ODS's own GPU container an older install left in Ubuntu,
# downloads every file of the model, cuts over to the Windows task and
# returns the --native-llm-* arguments (with --native-llm-runtime, the
# Models page's build policy). The Linux side then keeps WSL containers off
# the GPU (ods_native_llm_owns_host_gpu).
# Requires wsl-portal-setup.ps1, wsl-portal-amd.ps1, wsl-portal-spark.ps1 and
# spark-memory.ps1 in scope.

# The catalog model that checks a CUDA build on the GPU; it stays in the
# model store as the smallest fallback model.
$script:ODSSparkTestModelId = 'qwen3.5-2b-q4'
# A rerun whose check cache misses runs the check after the cutover stopped
# the current model when less than this much GPU memory or commit is free.
$script:ODSSparkCheckMemoryGiB = 3
$script:ODSSparkQualificationFile = 'qualification.json'
# An imported model's memory need is its files plus this much, as the model
# controller counts it.
$script:ODSSparkImportedModelExtraGiB = 2
$script:ODSSparkVcRedistWingetId = 'Microsoft.VCRedist.2015+.arm64'
$script:ODSSparkVcRedistUrl = 'https://aka.ms/vs/17/release/vc_redist.arm64.exe'
$script:ODSSparkCpuRoute = 'Setup continues with the model on the CPU inside Ubuntu (slower).'

# --- Catalog and plan inputs -----------------------------------------------------

function Get-ODSSparkCatalog([string]$SourceRoot) {
    $path = Join-Path (Join-Path $SourceRoot 'config') 'model-library.json'
    return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop)
}

function Get-ODSSparkCatalogModelByFile($Catalog, [string]$GgufFile) {
    foreach ($model in @($Catalog.models)) {
        if ((Test-CatalogModelSourceAllowed -Model $model) -and [string]$model.gguf_file -ceq $GgufFile) { return $model }
    }
    return $null
}

function Get-ODSSparkTestArtifact($Catalog) {
    $model = Get-CatalogModelById -Catalog $Catalog -ModelId $script:ODSSparkTestModelId
    $artifacts = if ($model) { Test-ODSCatalogModelArtifacts -Model $model } else { $null }
    if ($null -eq $artifacts -or @($artifacts).Count -ne 1) {
        throw ("The ODS model catalog has no single-file $($script:ODSSparkTestModelId) entry to check llama.cpp on the GPU, so this copy " +
            'of ODS is incomplete. Download ODS again, then rerun setup.')
    }
    return @($artifacts)[0]
}

function Get-ODSSparkPresentFiles {
    $modelsDir = Get-ODSPortalModelsDir
    if (-not (Test-Path -LiteralPath $modelsDir -PathType Container)) { return @() }
    return @(Get-ChildItem -LiteralPath $modelsDir -File -Filter '*.gguf' | ForEach-Object { $_.Name })
}

function Get-ODSSparkInstalledRuntime($Pin) {
    # The verified installed CUDA runtime, or $null when it is missing or
    # fails verification (Install-ODSNativeLlamaRuntime then moves the damaged
    # copy aside and downloads it again, as the AMD route does).
    $directory = Get-ODSNativeLlamaInstallDirectory $Pin
    if (-not (Test-Path -LiteralPath $directory)) { return $null }
    try {
        return (Test-ODSNativeLlamaInstall -Directory $directory -ExpectedZipSha256 $Pin.Sha256 -ExpectedReleaseTag $Pin.ReleaseTag `
            -ExpectedArchives @($Pin.Archives))
    } catch { return $null }
}

function New-ODSSparkPlanArguments($Catalog, $Pin, $Layout, $TestArtifact, $Runtime) {
    # Select-ODSSparkModelPlan's inputs: the pinned ladder and fallback list,
    # and the files setup downloads whatever the model (the llama.cpp archives
    # until they are installed, the test model until it is in the store).
    $setup = @()
    if (-not $Runtime) { $setup += @($Pin.Archives | ForEach-Object { [pscustomobject]@{ File = [string]$_.Asset; SizeBytes = [long]$_.Size } }) }
    $setup += [pscustomobject]@{ File = [string]$TestArtifact.File; SizeBytes = [long]$TestArtifact.SizeBytes }
    return @{ Catalog = $Catalog; Ladder = @($Pin.ModelLadder); Fallback = @($Pin.FallbackModels); Layout = $Layout
        ModelsDir = (Get-ODSPortalModelsDir); PresentFiles = @(Get-ODSSparkPresentFiles); SetupArtifacts = @($setup) }
}

# --- Owner-facing text (PR B design section 9) -------------------------------------

function ConvertTo-ODSSparkGB([double]$GiB) {
    # Disk sizes in GB, like the catalog's download sizes.
    return ([math]::Round([math]::Max($GiB, 0) * 1.073741824, 1)).ToString('0.0', [Globalization.CultureInfo]::InvariantCulture)
}

function Format-ODSSparkLayoutLine($SparkHost, $Layout, $Plan) {
    # M1: the firmware split, Windows' memory and the model plan.
    $pageFile = "page file $(Format-ODSNativeGiB $Layout.PageFileActiveGiB) GiB"
    if ($Layout.PageFilePending) { $pageFile += " ($(Format-ODSNativeGiB $Layout.PageFileConfiguredGiB) GiB after the next restart)" }
    $model = if ($Plan.Model) {
        "Model plan: $($Plan.Name), $([math]::Round([double]$Plan.ContextSize / 1024))K context, about $(Format-ODSNativeGiB $Plan.DeviceNeedGiB) GiB of GPU memory."
    } else { 'Model plan: no GPU model fits as things are now.' }
    return ("$($SparkHost.GpuName): $(Format-ODSNativeGiB ([double]$Layout.CarveOutMiB / 1024)) GiB reserved for the GPU, " +
        "$(Format-ODSNativeGiB $Layout.VisibleGiB) GiB for Windows, $pageFile, commit limit $(Format-ODSNativeGiB $Layout.CommitLimitGiB) GiB. $model")
}

function Get-ODSSparkCandidateDisk([hashtable]$PlanArguments, [string]$Id) {
    # The disk one ladder or fallback model needs here, with setup's own files.
    $candidate = Get-ODSSparkCandidate $PlanArguments.Catalog $Id $PlanArguments.Layout $script:ODSSparkMinContext 'arm64' $true
    if ($candidate.Reason) { return $null }
    $disk = Get-ODSSparkDiskNeedGiB -Layout $PlanArguments.Layout -Artifacts (@($candidate.Artifacts) + @($PlanArguments.SetupArtifacts)) `
        -ModelsDir $PlanArguments.ModelsDir -StackPresent ([bool]$PlanArguments.Layout.StackPresent) -PresentFiles @($PlanArguments.PresentFiles)
    $disk | Add-Member -NotePropertyName Name -NotePropertyValue $candidate.Name
    return $disk
}

function Get-ODSSparkSkipReason($Plan, [string]$Id) {
    return (@($Plan.Skipped | Where-Object { [string]$_.Id -ceq $Id } | ForEach-Object { [string]$_.Reason }) -join ',')
}

function Test-ODSSparkPlanShortOfDisk($Plan) {
    # Nothing fits, and disk, not memory, is why (M7).
    $reasons = @($Plan.Skipped | ForEach-Object { [string]$_.Reason })
    return ($reasons -ccontains 'disk' -and -not ($reasons -ccontains 'commit'))
}

function Get-ODSSparkTopDiskNotice([hashtable]$PlanArguments, $Plan) {
    # M6: the top rung was skipped for disk and a smaller model was planned.
    $top = @($PlanArguments.Ladder)[0]
    if (-not $top -or [string]$Plan.Model -ceq $top -or (Get-ODSSparkSkipReason $Plan $top) -cne 'disk') { return '' }
    $disk = Get-ODSSparkCandidateDisk $PlanArguments $top
    if (-not $disk -or -not $disk.ShortDrive) { return '' }
    return ("$($disk.ShortDrive) has $(ConvertTo-ODSSparkGB $disk.ShortFreeGiB) GB free. $($disk.Name) needs about " +
        "$(ConvertTo-ODSSparkGB $disk.ShortNeedGiB) GB (download $(ConvertTo-ODSSparkGB $disk.DownloadGiB) GB, and room for Ubuntu and " +
        "Docker images). ODS uses $(Format-ODSSparkModelName $Plan) now. Free space on $($disk.ShortDrive) and rerun setup to get " +
        "$($disk.Name). $script:ODSNativeHelpLine")
}

function Get-ODSSparkDiskStopText([hashtable]$PlanArguments, $Plan) {
    # M7: no GPU model fits the free disk.
    $smallest = $null
    foreach ($skip in @($Plan.Skipped | Where-Object { [string]$_.Reason -ceq 'disk' })) {
        $disk = Get-ODSSparkCandidateDisk $PlanArguments ([string]$skip.Id)
        if ($disk -and $disk.ShortDrive -and (-not $smallest -or $disk.ShortNeedGiB -lt $smallest.ShortNeedGiB)) { $smallest = $disk }
    }
    $drive = if ($smallest) { $smallest.ShortDrive } else { [string]$PlanArguments.Layout.SystemDrive }
    $free = if ($smallest) { ConvertTo-ODSSparkGB $smallest.ShortFreeGiB } else { ConvertTo-ODSSparkGB (Get-ODSSparkFreeGiB $PlanArguments.Layout $drive) }
    $need = if ($smallest) { " needs about $(ConvertTo-ODSSparkGB $smallest.ShortNeedGiB) GB" } else { ' does not fit' }
    return ("$drive has $free GB free, but the smallest GPU model plan for this PC$need (model, test model, llama.cpp and room for " +
        'Ubuntu and Docker images). Continue with the model on the CPU inside Ubuntu (slower)? If you say no, setup stops; nothing was ' +
        "downloaded or changed. Free space on $drive, for example by emptying the Recycle Bin or moving large files to another drive, then rerun.")
}

function Get-ODSSparkNoGpuModelText {
    # M3 when no change at all could make a GPU model fit.
    return ('No GPU model proven on this PC fits its memory (the GPU''s share or what Windows can lend), so the model runs on the CPU ' +
        "inside Ubuntu (slower). $script:ODSNativeHelpLine")
}

function Assert-ODSSparkForeignRuntimeReplaceable($Existing) {
    # M21 and M21b: a model task ODS does not manage stops setup while it is
    # live. A disabled, stopped one is saved and replaced in stage 3.
    if ([string]$Existing.Kind -cne 'foreign' -or $Existing.Replaceable) { return }
    $name = [string]$Existing.TaskName
    if ([string]$Existing.State -eq 'Disabled') {
        throw ("The Windows model task $name is disabled, but its server is still running (port $($Existing.Port) is used by " +
            "$($Existing.Listener)). Close it from its own app, or restart Windows (the disabled task will not start it again), then rerun " +
            'setup. Nothing was changed.')
    }
    $still = if ($Existing.Running -or $Existing.Listener) { 'that server keeps running' } else { 'that task stays as it is' }
    throw ("The Windows model task $name runs a model server this ODS release does not manage ($($Existing.Executable)). Setup stopped " +
        "before changing anything, and $still. To let ODS replace it with its llama.cpp server, run in normal PowerShell: " +
        "Stop-ScheduledTask -TaskPath '\' -TaskName '$name'; Disable-ScheduledTask -TaskPath '\' -TaskName '$name'. Then rerun setup: it " +
        "saves that task and its files under $(Get-ODSPortalReplacedTasksDir) before replacing them, so you can bring them back.")
}

function Format-ODSSparkQualificationFailure($Outcome) {
    # M11, M12, M13, M15 and M23 start from the check's own cause and step.
    if ([string]$Outcome.Class -ceq 'policy') {
        return ("Windows blocked llama.cpp (Smart App Control or an application control policy): $($Outcome.Reason) ODS does not change " +
            'that setting.')
    }
    return [string]$Outcome.Reason
}

# --- The CUDA build: install, check on the GPU, cache ---------------------------------

function Get-ODSSparkQualificationPath($Runtime) { return (Join-Path $Runtime.Directory $script:ODSSparkQualificationFile) }

function Read-ODSSparkQualificationCache($Runtime, $Pin, $SparkHost) {
    <#
    .SYNOPSIS
        The saved GPU check of this runtime, or $null. It holds only while the
        archives, the NVIDIA driver, the PCI id and the GPU name are the ones
        it was made with.
    #>
    $path = Get-ODSSparkQualificationPath $Runtime
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $record = Read-ODSNativeLlamaJsonOrNull $path
    if ($null -eq $record -or $record.schemaVersion -ne 1) { return $null }
    $archives = @($Pin.Archives | ForEach-Object { [string]$_.Sha256 }) -join ','
    if ((@($record.Archives) -join ',') -cne $archives -or [string]$record.DriverVersion -cne [string]$SparkHost.DriverVersion -or
        [string]$record.PciDeviceId -cne [string]$SparkHost.PciDeviceId -or [string]$record.GpuName -cne [string]$SparkHost.GpuName) {
        return $null
    }
    $device = $record.Device
    if (-not $device -or [string]$device.Name -cnotmatch '^CUDA[0-9]{1,2}$') { return $null }
    return [pscustomobject]@{ Build = [int]$record.Build; Commit = [string]$record.Commit; Devices = @($device); Device = $device
        PolicyHint = ''; Backend = 'cuda' }
}

function Write-ODSSparkQualificationCache($Runtime, $Pin, $SparkHost, $Outcome) {
    $device = $Outcome.Qualification.Device
    $record = [ordered]@{
        schemaVersion = 1
        Archives = @($Pin.Archives | ForEach-Object { [string]$_.Sha256 })
        DriverVersion = [string]$SparkHost.DriverVersion
        PciDeviceId = [string]$SparkHost.PciDeviceId
        GpuName = [string]$SparkHost.GpuName
        Device = [ordered]@{ Name = [string]$device.Name; Description = [string]$device.Description
            TotalMiB = [long]$device.TotalMiB; FreeMiB = [long]$device.FreeMiB }
        Build = [int]$Outcome.Qualification.Build
        Commit = [string]$Outcome.Qualification.Commit
        Archs = @($Outcome.Check.Archs)
        CheckedAt = [datetime]::UtcNow.ToString('o')
    }
    Write-ODSPrivateEnvFile -Path (Get-ODSSparkQualificationPath $Runtime) -Content ($record | ConvertTo-Json -Depth 4)
}

function Test-ODSSparkCheckMemoryShort([string]$GpuName) {
    # The running model leaves too little GPU memory or commit for the check.
    $gpu = Get-ODSNativeGpuMemoryState $GpuName
    $commit = Get-ODSNativeCommitState
    $gpuFree = if ($gpu) { ([double]$gpu.TotalMiB - [double]$gpu.UsedMiB) / 1024 } else { [double]::PositiveInfinity }
    $commitFree = if ($commit) { [double]$commit.CommitLimitGiB - [double]$commit.CommittedGiB } else { [double]::PositiveInfinity }
    return ($gpuFree -lt $script:ODSSparkCheckMemoryGiB -or $commitFree -lt $script:ODSSparkCheckMemoryGiB)
}

function Save-ODSSparkTestModel($TestArtifact, [hashtable]$State, $Done) {
    $path = Join-Path (Get-ODSPortalModelsDir) $TestArtifact.File
    $present = Test-Path -LiteralPath $path -PathType Leaf
    $null = Save-ODSPortalModelArtifacts @($TestArtifact)
    if (-not $present) {
        $State.TestModelDownloaded = $true
        $Done.Add("test model $($TestArtifact.File) downloaded")
    }
}

function Remove-ODSSparkTestModel($TestArtifact, [hashtable]$State) {
    # Off the GPU route the test model is not needed; a copy that was in the
    # store before this run stays.
    if (-not $State.TestModelDownloaded) { return }
    $path = Join-Path (Get-ODSPortalModelsDir) $TestArtifact.File
    if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
    $State.TestModelDownloaded = $false
}

function Invoke-ODSSparkQualification($Pin, $Runtime, $SparkHost, $TestArtifact, [hashtable]$State, $Done) {
    # The build and its CUDA device first; only then the test model and the
    # GPU check. A binary that cannot run comes back as its class.
    $integrity = Get-ODSNativeLlamaCodeIntegrityState
    try {
        $build = Test-ODSNativeLlamaQualification -ExecutablePath $Runtime.ExecutablePath -ExpectedBuild $Pin.Build `
            -AdapterName $SparkHost.GpuName -CodeIntegrity $integrity -Backend cuda -Architecture $Pin.Architecture
    } catch {
        $class = [string]$_.Exception.Data['class']
        if (-not $class) { throw }
        return [pscustomobject]@{ Passed = $false; Class = $class; Reason = $_.Exception.Message; Qualification = $null; Check = $null }
    }
    if ($build.PolicyHint) { Write-Host "         $($build.PolicyHint)" }
    $testPath = Join-Path (Get-ODSPortalModelsDir) $TestArtifact.File
    if ($build.Device) { Save-ODSSparkTestModel $TestArtifact $State $Done }
    return (Invoke-ODSPortalCudaQualification -Pin $Pin -Runtime $Runtime -AdapterName $SparkHost.GpuName -ComputeCap $SparkHost.ComputeCap `
        -TestModelPath $testPath -CodeIntegrity $integrity -Qualification $build)
}

function Install-ODSSparkVcRuntime {
    # winget installs Microsoft's package; the installer asks for UAC itself.
    $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $winget) {
        Write-Host ("         This Windows has no winget to install it. Install it from $script:ODSSparkVcRedistUrl, then rerun setup. " +
            $script:ODSNativeHelpLine)
        return $false
    }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $winget.Source install --id $script:ODSSparkVcRedistWingetId --exact --source winget --accept-package-agreements --accept-source-agreements | Out-Host
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    # winget codes that mean it is already there, or installed pending a restart.
    if ($code -in @(0, -1978335189, -1978335135, -1978334963, -1978334962, -1978334967)) { return $true }
    Write-Host ("         winget could not install the Visual C++ runtime for Arm64 (exit $code). Install it from " +
        "$script:ODSSparkVcRedistUrl, then rerun setup. $script:ODSNativeHelpLine")
    return $false
}

function Confirm-ODSSparkVcRuntime([bool]$NonInteractive) {
    # M15, asked only on a fresh install whose build misses the runtime.
    $text = ('llama.cpp could not start because a Windows component is missing (0xC0000135), most likely the Microsoft Visual C++ ' +
        "2015-2022 Redistributable for Arm64. Install it now? winget installs $script:ODSSparkVcRedistWingetId from Microsoft and accepts " +
        'its license terms; Windows asks for administrator permission once. Or install it yourself from ' +
        "$script:ODSSparkVcRedistUrl and rerun setup. $script:ODSNativeHelpLine")
    if (-not (Confirm-ODSPortalPreparation $text $NonInteractive)) { return $false }
    return (Install-ODSSparkVcRuntime)
}

function Get-ODSSparkRuntimeConsentText($Pin, $TestArtifact) {
    # M10.
    $mib = [math]::Ceiling([double](@($Pin.Archives) | Measure-Object -Property Size -Sum).Sum / 1MB)
    $present = Test-Path -LiteralPath (Join-Path (Get-ODSPortalModelsDir) $TestArtifact.File) -PathType Leaf
    $test = if ($present) { 'and check it on your GPU with the test model already downloaded' } else {
        "and a $([math]::Round([double]$TestArtifact.SizeBytes / 1e9, 1)) GB test model to check it on your GPU before the large model download" }
    return ("Download llama.cpp $($Pin.ReleaseTag) for NVIDIA GPUs on Windows on Arm (CUDA $($Pin.CudaVersion)) from github.com/ggml-org " +
        "(about $mib MiB, SHA-256 pinned), $($test)? It runs only on this computer (127.0.0.1).")
}

function New-ODSSparkBinary([string]$Route, $Pin, $TestArtifact, $Runtime, $Qualification, $DeferredProbe, [hashtable]$State, $Done) {
    return [pscustomobject]@{ Route = $Route; Pin = $Pin; Test = $TestArtifact; Runtime = $Runtime; Qualification = $Qualification
        DeferredProbe = $DeferredProbe; TestModelDownloaded = [bool]$State.TestModelDownloaded; Done = @($Done) }
}

function Initialize-ODSPortalSparkRuntimeBinary($SparkHost, $Pin, $TestArtifact, $Existing, [bool]$NonInteractive) {
    <#
    .SYNOPSIS
        Stage-1 step d: the pinned llama.cpp CUDA build, installed and
        checked on this GPU before any large download.
    .DESCRIPTION
        A driver older than the build needs stops setup with nothing changed.
        A fresh install asks first; a declined download, or a build that
        cannot run here, gives Route 'cpu' (a missing Arm64 Visual C++
        runtime is offered once, then the check runs again). With ODS's
        runtime already installed (a rerun) a failure stops setup and the
        current model is not changed. A rerun with too little free memory for
        the check while the current model runs defers it to the cutover.
    .OUTPUTS
        Route ('native' or 'cpu'), Pin, Test, Runtime, Qualification,
        DeferredProbe, TestModelDownloaded and Done.
    #>
    Assert-ODSNativeLlamaDriver $Pin $SparkHost.DriverVersion
    $rerun = [string]$Existing.Kind -ceq 'llama-server'
    $done = [Collections.Generic.List[string]]::new()
    $state = @{ TestModelDownloaded = $false }
    $runtime = Get-ODSSparkInstalledRuntime $Pin
    if (-not $runtime) {
        if (-not $rerun) {
            if (-not (Confirm-ODSPortalPreparation (Get-ODSSparkRuntimeConsentText $Pin $TestArtifact) $NonInteractive)) {
                Write-Host "         Continuing without the GPU: the model runs on the CPU inside Ubuntu (slower). Rerun setup to use the GPU. $script:ODSNativeHelpLine"
                return (New-ODSSparkBinary 'cpu' $Pin $TestArtifact $null $null $null $state $done)
            }
        } else {
            $mib = [math]::Ceiling([double](@($Pin.Archives) | Measure-Object -Property Size -Sum).Sum / 1MB)
            Write-Host "         ODS now runs the model with llama.cpp $($Pin.ReleaseTag) (CUDA $($Pin.CudaVersion)) from github.com/ggml-org. Downloading it (about $mib MiB, SHA-256 pinned)."
        }
        # Staging happens while a previous runtime keeps serving.
        $runtime = Install-ODSNativeLlamaRuntime -Pin $Pin
        $done.Add("llama.cpp $($Pin.ReleaseTag) for NVIDIA GPUs downloaded to $($runtime.Directory)")
    }
    $cached = Read-ODSSparkQualificationCache $runtime $Pin $SparkHost
    if ($cached) {
        Write-Host "         llama.cpp $($Pin.ReleaseTag) already passed its check on $($cached.Device.Name) with driver $($SparkHost.DriverVersion)."
        return (New-ODSSparkBinary 'native' $Pin $TestArtifact $runtime $cached $null $state $done)
    }
    $testPath = Join-Path (Get-ODSPortalModelsDir) $TestArtifact.File
    if ($rerun -and (Test-ODSSparkCheckMemoryShort $SparkHost.GpuName)) {
        # The current model holds the GPU: check the build and device now and
        # the answer after the cutover stops it (a failure restarts it).
        try {
            $build = Test-ODSNativeLlamaQualification -ExecutablePath $runtime.ExecutablePath -ExpectedBuild $Pin.Build `
                -AdapterName $SparkHost.GpuName -Backend cuda -Architecture $Pin.Architecture
        } catch {
            if (-not $_.Exception.Data['class']) { throw }
            throw "$(Format-ODSSparkQualificationFailure ([pscustomobject]@{ Class = [string]$_.Exception.Data['class']; Reason = $_.Exception.Message })) Your current model was not changed."
        }
        if (-not $build.Device) {
            $none = Invoke-ODSPortalCudaQualification -Pin $Pin -Runtime $runtime -AdapterName $SparkHost.GpuName -ComputeCap $SparkHost.ComputeCap `
                -TestModelPath $testPath -Qualification $build
            throw "$($none.Reason) Your current model was not changed."
        }
        Save-ODSSparkTestModel $TestArtifact $state $done
        Write-Host '         llama.cpp is checked on the GPU once your current model stops for the switch; if the check fails, that model starts again.'
        return (New-ODSSparkBinary 'native' $Pin $TestArtifact $runtime $build ([pscustomobject]@{ TestModelPath = $testPath }) $state $done)
    }
    $outcome = Invoke-ODSSparkQualification $Pin $runtime $SparkHost $TestArtifact $state $done
    if (-not $outcome.Passed -and [string]$outcome.Class -ceq 'dll' -and -not $rerun -and (Confirm-ODSSparkVcRuntime $NonInteractive)) {
        $done.Add('the Microsoft Visual C++ runtime for Arm64 installed')
        # One retry; its own result decides.
        $outcome = Invoke-ODSSparkQualification $Pin $runtime $SparkHost $TestArtifact $state $done
    }
    if ($outcome.Passed) {
        Write-ODSSparkQualificationCache $runtime $Pin $SparkHost $outcome
        $device = $outcome.Qualification.Device
        Write-Host "         llama.cpp $($Pin.ReleaseTag) passed its check on $($device.Name): $($device.Description) answered the test question correctly."
        return (New-ODSSparkBinary 'native' $Pin $TestArtifact $runtime $outcome.Qualification $null $state $done)
    }
    $text = Format-ODSSparkQualificationFailure $outcome
    if ($rerun) { throw "$text Your current model was not changed." }
    Write-Host "         $text $script:ODSSparkCpuRoute $script:ODSNativeHelpLine"
    Remove-ODSSparkTestModel $TestArtifact $state
    return (New-ODSSparkBinary 'cpu' $Pin $TestArtifact $runtime $null $null $state $done)
}

function Invoke-ODSSparkDeferredCheck($Check) {
    # The deferred GPU check, run by the cutover once the current model is
    # stopped. A failure carries Data['gpuCheck'], so the cutover restores
    # that model and keeps this text.
    $binary = $Check.Binary
    $outcome = Invoke-ODSPortalCudaQualification -Pin $binary.Pin -Runtime $binary.Runtime -AdapterName $Check.SparkHost.GpuName `
        -ComputeCap $Check.SparkHost.ComputeCap -TestModelPath $binary.DeferredProbe.TestModelPath -Qualification $binary.Qualification
    if (-not $outcome.Passed) {
        $failure = [Management.Automation.RuntimeException]::new((Format-ODSSparkQualificationFailure $outcome))
        $failure.Data['gpuCheck'] = $true
        throw $failure
    }
    Write-ODSSparkQualificationCache $binary.Runtime $binary.Pin $Check.SparkHost $outcome
    Write-Host "         llama.cpp $($binary.Pin.ReleaseTag) passed its check on $($outcome.Qualification.Device.Name)."
}

# --- Stage 1 ---------------------------------------------------------------------

function New-ODSSparkFoundation([string]$Route, $Plan, $Binary, $Existing, [string]$RestartReason, [string[]]$Done) {
    return [pscustomobject]@{ Stop = $null; Route = $Route; Plan = $Plan; Binary = $Binary; Existing = $Existing
        RestartReason = $RestartReason; Done = @($Done | Where-Object { $_ })
        DeferredProbe = $(if ($Binary) { $Binary.DeferredProbe } else { $null }) }
}

function Initialize-ODSPortalSparkFoundation($SparkHost, [System.Collections.IDictionary]$Options, [string]$InstallerRoot, [bool]$NonInteractive, [string]$SourceRoot) {
    <#
    .SYNOPSIS
        Stage 1 on an RTX Spark, before Windows' own prerequisites (see the
        file header for the order).
    .OUTPUTS
        Route ('native' or 'cpu'), Plan, Binary, Existing, RestartReason ('' or
        the text the shared restart shows) and Done (what was already
        downloaded or changed). Stops only by a throw: a live model server ODS
        does not manage, a driver older than the build needs, a declined CPU
        route when the disk is too small, or a failed check on a rerun.
    #>
    # a. What ODS already runs, before anything else.
    $existing = Get-ODSPortalExistingRuntime -AllowForeign
    Assert-ODSSparkForeignRuntimeReplaceable $existing
    $rerun = [string]$existing.Kind -ceq 'llama-server'
    # b. The generic checks; Windows' own foundation runs them again (harmless).
    $present = [bool](Get-Command wsl.exe -ErrorAction SilentlyContinue)
    $wslReady = $present -and ((Invoke-ODSPortalWsl -Arguments @('--status')).Code -eq 0)
    Assert-ODSPortalHostCapacity $wslReady
    # c. Memory and disk: no prompts and no downloads yet.
    $catalog = Get-ODSSparkCatalog $SourceRoot
    $pin = Get-ODSNativeLlamaPin -SourceRoot $SourceRoot -Backend nvidia -Architecture arm64
    $test = Get-ODSSparkTestArtifact $catalog
    $layout = Get-ODSSparkMemoryLayout -CarveOutMiB $SparkHost.CarveOutMiB
    $planArguments = New-ODSSparkPlanArguments $catalog $pin $layout $test (Get-ODSSparkInstalledRuntime $pin)
    $now = Select-ODSSparkModelPlan @planArguments -AllowPageFile $false -AllowWslBound $false
    Write-Host "         $(Format-ODSSparkLayoutLine $SparkHost $layout $now)"
    if ($now.Model) {
        $notice = Get-ODSSparkTopDiskNotice $planArguments $now
        if ($notice) { Write-Host "         $notice" }
    } elseif (-not $rerun) {
        if (Test-ODSSparkPlanShortOfDisk $now) {
            if (-not (Confirm-ODSPortalPreparation (Get-ODSSparkDiskStopText $planArguments $now) $NonInteractive)) {
                throw ('There is not enough free disk space for a GPU model, so setup stopped; nothing was downloaded or changed. Free space ' +
                    'on the drive named above, then rerun the same install.ps1 command.')
            }
            return (New-ODSSparkFoundation 'cpu' $null $null $existing '' @())
        }
        $possible = Select-ODSSparkModelPlan @planArguments -AllowPageFile $true -AllowWslBound $true
        if (-not $possible.Offer) {
            Write-Host "         $(Get-ODSSparkNoGpuModelText)"
            return (New-ODSSparkFoundation 'cpu' $null $null $existing '' @())
        }
    }
    # d. The CUDA build, checked on the GPU.
    $binary = Initialize-ODSPortalSparkRuntimeBinary $SparkHost $pin $test $existing $NonInteractive
    $done = @($binary.Done)
    if ($binary.Route -cne 'native') { return (New-ODSSparkFoundation 'cpu' $null $binary $existing '' $done) }
    # e. The memory offers, with the files setup now has.
    $planArguments = New-ODSSparkPlanArguments $catalog $pin $layout $test $binary.Runtime
    $offers = Invoke-ODSSparkMemoryOffers -PlanArguments $planArguments -NonInteractive $NonInteractive -RestartPending (-not $wslReady)
    $done += @($offers.Done)
    $reason = [string]$offers.RestartReason
    if ($reason -and -not $reason.Contains('Use Restart, not Shut down')) {
        $reason += ' Use Restart, not Shut down (with Fast Startup, Shut down may not apply it).'
    }
    if (-not $reason -and -not $offers.Plan.Model -and -not $rerun) {
        # Nothing fits and no change waits for a restart: the CPU route. The
        # offers already said why (M3).
        $state = @{ TestModelDownloaded = $binary.TestModelDownloaded }
        Remove-ODSSparkTestModel $binary.Test $state
        return (New-ODSSparkFoundation 'cpu' $null $binary $existing '' $done)
    }
    return (New-ODSSparkFoundation 'native' $offers.Plan $binary $existing $reason $done)
}

# --- Stage 3 ---------------------------------------------------------------------

function Get-ODSSparkContainerRoute([string]$WslDistro, [string]$WslInstallDir) {
    # True when the ODS install in this distro runs the model in its NVIDIA
    # container (an install from before this route). Only the two keys are read.
    $read = Invoke-ODSPortalWsl -Arguments @('--distribution', $WslDistro, '--exec', 'grep', '-E', '^(GPU_BACKEND|NATIVE_LLM_BASE_URL)=',
        "$WslInstallDir/.env")
    # grep exits 1 without such a line and 2 without a .env (a new installation).
    if ($read.Code -in @(1, 2)) { return $false }
    if ($read.Code -ne 0) {
        throw ("Setup could not read $WslInstallDir/.env in $WslDistro (exit $($read.Code)): $($read.Error) Nothing was changed. Check " +
            "that $WslDistro starts, then rerun setup.").Trim()
    }
    $values = @{}
    foreach ($line in ([string]$read.Output -split "`r?`n")) {
        if ($line -match '^(GPU_BACKEND|NATIVE_LLM_BASE_URL)=(.*)$') { $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'") }
    }
    return ([string]$values['GPU_BACKEND'] -ceq 'nvidia' -and -not [string]$values['NATIVE_LLM_BASE_URL'])
}

function Stop-ODSSparkContainerModel([string]$WslDistro, [string]$WslInstallDir) {
    <#
    .SYNOPSIS
        M22: an older install ran the model in ODS's own NVIDIA container,
        which would hold GPU memory the Windows server needs. It is stopped
        (only containers of compose project ods, service llama-server) and
        the ids are returned, so a failed cutover can start them again.
    #>
    if (-not (Get-ODSSparkContainerRoute $WslDistro $WslInstallDir)) { return @() }
    $list = Invoke-ODSPortalWsl -Arguments @('--distribution', $WslDistro, '--exec', 'docker', 'ps', '--quiet',
        '--filter', 'label=com.docker.compose.project=ods', '--filter', 'label=com.docker.compose.service=llama-server')
    if ($list.Code -ne 0) {
        throw ("Docker in $WslDistro could not list ODS's model container (exit $($list.Code)): $($list.Error) Nothing was changed. " +
            'Check that Docker Desktop is running, then rerun setup.').Trim()
    }
    $ids = @(([string]$list.Output -split "`r?`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -cmatch '^[0-9a-f]{12,64}$' })
    if (-not $ids.Count) { return @() }
    Write-Host ('         This PC ran the model in a container in Ubuntu, which cannot use this GPU correctly. ODS now stops that container ' +
        'and runs the model with llama.cpp on Windows; chat is back when setup finishes. Model files in Ubuntu stay until you delete them ' +
        'on the Models page.')
    $stop = Invoke-ODSPortalWsl -Arguments (@('--distribution', $WslDistro, '--exec', 'docker', 'stop') + $ids)
    if ($stop.Code -ne 0) {
        throw ("ODS could not stop its model container in $WslDistro (docker stop exit $($stop.Code)): $($stop.Error) Nothing else was " +
            'changed. Stop it in Ubuntu with: docker stop ods-llama-server, then rerun setup.').Trim()
    }
    return $ids
}

function Start-ODSSparkContainerModel([string]$WslDistro, [string[]]$Ids) {
    # After a failed cutover: the container the migration stopped runs again.
    $start = Invoke-ODSPortalWsl -Arguments (@('--distribution', $WslDistro, '--exec', 'docker', 'start') + $Ids)
    if ($start.Code -eq 0) { return "ODS's model container in Ubuntu was started again." }
    return "ODS's model container in Ubuntu did not start again (docker start exit $($start.Code)); start it in Ubuntu with: docker start ods-llama-server."
}

function Get-ODSSparkSelectionNeed($Catalog, $Selection, $Layout) {
    # GPU memory and commit a carried selection needs: the catalog estimate on
    # arm64 with the carve-out as GPU memory, else the files plus 2 GiB.
    $model = Get-ODSSparkCatalogModelByFile $Catalog ([string]$Selection.GgufFile)
    if ($model) {
        $gpuInfo = @{ Backend = 'nvidia'; MemoryType = 'discrete'; VramMB = [int]$Layout.CarveOutMiB }
        $runtimeProfile = Get-CatalogRuntimeProfile -Model $model -GpuInfo $gpuInfo -SystemRamGB ([int][math]::Floor([double]$Layout.VisibleGiB)) `
            -HostArchitecture 'arm64'
        $need = Get-CatalogModelMemoryNeed -Model $model -RuntimeProfile $runtimeProfile -ContextLength ([int]$Selection.ContextSize)
        return [pscustomobject]@{ DeviceNeedGiB = $need.DeviceNeedGiB; CommitNeedGiB = $need.CommitNeedGiB; Source = 'catalog' }
    }
    $files = @(Get-ODSGgufSplitSiblings ([string]$Selection.GgufFile))
    if ($Selection.MmprojFile) { $files += [string]$Selection.MmprojFile }
    $bytes = [long]0
    foreach ($name in $files) {
        $path = Join-Path (Get-ODSPortalModelsDir) $name
        if (Test-Path -LiteralPath $path -PathType Leaf) { $bytes += (Get-Item -LiteralPath $path).Length }
    }
    $gib = [math]::Round($bytes / 1GB + $script:ODSSparkImportedModelExtraGiB, 2)
    return [pscustomobject]@{ DeviceNeedGiB = $gib; CommitNeedGiB = $gib; Source = 'files' }
}

function Resolve-ODSSparkRuntimeSelection($Existing, $Plan, $Catalog, $Layout) {
    <#
    .SYNOPSIS
        The model to start: the owner's Models-page choice when every file of
        it is in the store and it still fits this PC's memory, else the plan.
    #>
    $recommended = if ($Plan.Model) {
        [pscustomobject]@{ GgufFile = [string]@($Plan.Artifacts)[0].File; ContextSize = [int]$Plan.ContextSize }
    } else { [pscustomobject]@{ GgufFile = '(none)'; ContextSize = 0 } }
    if ($Existing.Selection -and $Existing.Selection.GgufFile) {
        $selection = Resolve-ODSPortalRuntimeSelection $Existing $recommended -RequireAllParts
        if ($selection.Carried) {
            $need = Get-ODSSparkSelectionNeed $Catalog $selection $Layout
            $carveOutGiB = [double]$Layout.CarveOutMiB / 1024
            $device = $carveOutGiB - (Get-CatalogFitMarginGB -CapacityGB $carveOutGiB -MemoryClass 'discrete')
            $commit = Get-ODSSparkCommitCapacityGiB $Layout ([double]$Layout.PageFileConfiguredGiB) ([double]$Plan.WslGiB)
            if ($need.DeviceNeedGiB -le $device + 1e-9 -and $need.CommitNeedGiB -le $commit + 1e-9) {
                $selection | Add-Member -NotePropertyName Need -NotePropertyValue $need
                return $selection
            }
            $instead = if ($Plan.Model) { "using the recommended $($recommended.GgufFile)" } else { 'and no other GPU model fits now' }
            Write-Host ("         Your selected model $($selection.GgufFile) needs about $(Format-ODSNativeGiB $need.CommitNeedGiB) GiB, but Windows can " +
                "lend about $(Format-ODSNativeGiB ([math]::Min($device, $commit))) GiB now; $instead.")
        }
    }
    if (-not $Plan.Model) {
        throw ('No GPU model fits the memory Windows can lend now, so setup did not change the model runtime. Rerun setup and answer yes ' +
            'to the page-file offer, or close large programs and rerun.')
    }
    $model = Get-CatalogModelById -Catalog $Catalog -ModelId $Plan.Model
    $projector = if ($model) { [string](Get-CatalogProperty $model 'mmproj_file') } else { '' }
    return [pscustomobject]@{ GgufFile = $recommended.GgufFile; ContextSize = $recommended.ContextSize; Carried = $false
        MmprojFile = $(if ($projector) { $projector } else { $null })
        Need = [pscustomobject]@{ DeviceNeedGiB = $Plan.DeviceNeedGiB; CommitNeedGiB = $Plan.CommitNeedGiB; Source = 'catalog' } }
}

function Get-ODSSparkRuntimePort($Existing) {
    # Reruns keep the port the WSL side already uses; it is checked once freed.
    if ($env:AMD_INFERENCE_PORT -and [string]$Existing.Kind -ceq 'llama-server') {
        $port = [int]$env:AMD_INFERENCE_PORT
        if ($port -lt 1 -or $port -gt 65535) { throw 'AMD_INFERENCE_PORT must be between 1 and 65535.' }
        return [pscustomobject]@{ Port = $port; Keep = $true }
    }
    if ([string]$Existing.Kind -ceq 'llama-server' -and $Existing.Selection.Port -ge 1 -and $Existing.Selection.Port -le 65535) {
        return [pscustomobject]@{ Port = [int]$Existing.Selection.Port; Keep = $true }
    }
    return [pscustomobject]@{ Port = (Select-ODSPortalRuntimePort); Keep = $false }
}

function Initialize-ODSPortalSparkRuntime($SparkHost, $Foundation, [string]$SourceRoot, [string]$WslDistro, [string]$WslInstallDir) {
    <#
    .SYNOPSIS
        Stage 3 on the native route: migrate an older container install, plan
        again with no prompts, download every file of the model, cut over to
        the Windows task (with the deferred GPU check when stage 1 left one)
        and return the --native-llm-* arguments and the key environment.
    #>
    $existing = Get-ODSPortalExistingRuntime -AllowForeign
    Assert-ODSSparkForeignRuntimeReplaceable $existing
    $binary = $Foundation.Binary
    $stopped = @(Stop-ODSSparkContainerModel $WslDistro $WslInstallDir)
    try {
        $catalog = Get-ODSSparkCatalog $SourceRoot
        # The WSL limit in effect: one stage 1 wrote while the owner declined
        # the WSL restart (M9) is still pending and not planned with.
        $layout = Get-ODSSparkMemoryLayout -CarveOutMiB $SparkHost.CarveOutMiB
        $planArguments = New-ODSSparkPlanArguments $catalog $binary.Pin $layout $binary.Test $binary.Runtime
        $plan = Select-ODSSparkModelPlan @planArguments -AllowPageFile $false -AllowWslBound $false
        $selection = Resolve-ODSSparkRuntimeSelection $existing $plan $catalog $layout
        if (-not $selection.Carried) { $null = Save-ODSPortalModelArtifacts @($plan.Artifacts) }
        $port = Get-ODSSparkRuntimePort $existing
        $apiKey = Get-ODSPortalApiKey
        $options = New-ODSPortalRuntimeOptions $binary.Runtime $binary.Qualification 'cuda' 'arm64'
        $runtimePlan = [ordered]@{ ExecutablePath = $binary.Runtime.ExecutablePath; Port = $port.Port; ModelsDir = (Get-ODSPortalModelsDir)
            ContextSize = [int]$selection.ContextSize; GgufFile = [string]$selection.GgufFile }
        if ($selection.MmprojFile) { $runtimePlan.MmprojFile = [string]$selection.MmprojFile }
        $memoryPlan = New-ODSPortalMemoryPlanRecord $runtimePlan $selection.Need ([double]$plan.WslGiB)
        $check = $null
        if ($binary.DeferredProbe) { $check = [pscustomobject]@{ Binary = $binary; SparkHost = $SparkHost } }
        Write-Host "         Starting llama.cpp on 127.0.0.1:$($port.Port) with $($selection.GgufFile)..."
        $null = Invoke-ODSPortalRuntimeCutover -Existing $existing -Plan $runtimePlan -Options $options -ApiKey $apiKey -KeepPort $port.Keep `
            -WslDistro $WslDistro -WslInstallDir $WslInstallDir -TaskDescription $script:ODSPortalRuntimeTaskDescriptions['cuda'] `
            -MemoryPlan $memoryPlan -BeforeStart $(if ($check) { { param($Check) Invoke-ODSSparkDeferredCheck $Check } } else { $null }) `
            -BeforeStartArgument $check
    } catch {
        if (-not $stopped.Count) { throw }
        throw "$($_.Exception.Message) $(Start-ODSSparkContainerModel $WslDistro $stopped)"
    }
    $previousDirectory = if ([string]$existing.Kind -ceq 'llama-server') { Split-Path -Parent $existing.Launch.ExecutablePath } else { '' }
    Remove-ODSNativeLlamaOldRuntimes -KeepDirectory $binary.Runtime.Directory -PreviousDirectory $previousDirectory
    Write-Host "         GPU model ready: $($selection.GgufFile) ($($selection.ContextSize) tokens of context)."
    return [pscustomobject]@{
        Arguments = @(
            '--native-llm-url', "http://localhost:$($port.Port)",
            '--native-llm-host-transport', 'model-router',
            '--native-llm-model', [string]$selection.GgufFile,
            '--native-llm-context-size', [string]$selection.ContextSize,
            '--native-llm-gpu-name', [string]$SparkHost.GpuName,
            '--native-llm-gpu-vram-mb', [string]$SparkHost.CarveOutMiB,
            '--native-llm-api-key-env', $script:ODSPortalApiKeyVariable,
            # The Models page judges imports against the build that runs.
            '--native-llm-runtime', [string]$binary.Pin.PolicyKey
        )
        Environment = @{ $script:ODSPortalApiKeyVariable = $apiKey }
    }
}

function Add-ODSPortalSparkArguments([string[]]$LinuxArgs, [System.Collections.IDictionary]$Options, $SparkHost, $Foundation, [string]$WslDistro, [string]$SourceRoot) {
    <#
    .SYNOPSIS
        Stage 3 on an RTX Spark, instead of the NVIDIA container gate:
        the Linux installer arguments, the environment forwarded to it and
        whether the model runs on Windows (WindowsModelRoute).
    #>
    if ([string]$Foundation.Route -cne 'native') {
        Write-ODSPortalStage 3 'NVIDIA GPU' "$($SparkHost.GpuName): the model runs on the CPU inside Ubuntu (slower) this time, for the reason shown above."
        # Ubuntu still sees the GPU through WSL and would pick a CUDA image
        # that cannot run on it, so the CPU route is forced.
        return [pscustomobject]@{ Arguments = @($LinuxArgs); Environment = @{ GPU_BACKEND = 'cpu' }; WindowsModelRoute = $false }
    }
    $wslInstallDir = Resolve-ODSPortalWslBinding $Options $WslDistro
    Write-ODSPortalStage 3 'NVIDIA GPU' ("$($SparkHost.GpuName) ($(Format-ODSNativeGiB ([double]$SparkHost.CarveOutMiB / 1024)) GiB for the GPU): " +
        'the model runs through llama.cpp (CUDA) on Windows.')
    $route = Initialize-ODSPortalSparkRuntime $SparkHost $Foundation $SourceRoot $WslDistro $wslInstallDir
    $sparkArgs = @($route.Arguments)
    # The Linux installer's top tier; the model itself came from the ladder.
    if (-not $Options['Tier']) { $sparkArgs += @('--tier', '4') }
    return [pscustomobject]@{ Arguments = @($LinuxArgs + $sparkArgs); Environment = $route.Environment; WindowsModelRoute = $true }
}
