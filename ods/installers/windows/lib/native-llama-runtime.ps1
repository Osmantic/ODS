# ============================================================================
# ODS Windows Installer -- native llama.cpp runtime (AMD GPUs on Windows)
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Acquire, qualify, launch and prove ggml-org's llama-server.exe
#          (Vulkan build) for the Windows Portal task and the legacy native
#          installer. WSL2 cannot drive an AMD GPU, so the model runs here.
#
# Flavors: a runtime is one llama.cpp release build, chosen by GPU backend
# and Windows architecture. win-vulkan-x64 (AMD) is the only routed flavor and
# keeps every behaviour below. win-cuda-<version>-arm64 (NVIDIA GPUs on
# Windows on Arm, config/backends/nvidia.json) ships as two archives, writes
# pin.json schemaVersion 2 with its own required-file list, launches with
# --fit off and is qualified by a short generation check. Nothing routes to it
# yet.
#
# Contract (Round F, section 5):
#   - Binary: %LOCALAPPDATA%\ODS\llama.cpp\<tag>-win-vulkan-x64\ with pin.json
#     (tag, asset, zip SHA-256 and a SHA-256 for every file). Size and hash
#     are checked before extraction; pin.json is re-verified at every launch.
#   - Qualification: --version (build number) and --list-devices. No Vulkan
#     device means the CPU route with a driver message; never a silent CPU
#     fallback, because the launch always names --device (VulkanN, or none
#     with zero GPU layers for the legacy installer's explicit CPU route).
#   - Launch: loopback only, --alias <GGUF>, --parallel 1, --metrics,
#     --no-webui and --api-key-file <owner-only file>. The key is never on
#     the command line.
#   - Proof: GET /health, then /v1/models (id equals the GGUF), then /props
#     (model_path and n_ctx, allowing llama.cpp's 256-cell alignment).
#   - Memory (CUDA only): Windows charges every GPU allocation against its
#     commit limit, so before a CUDA start the launcher checks the commit and
#     GPU memory the plan's memory-plan.json names. It waits for sign-in to
#     settle, then refuses with the cause in ready.json; a start-override.json
#     for the same plan (the Models page's Start anyway) starts it anyway.
#     Vulkan (AMD) never runs this check.
#
# The Portal's durable launcher copies this file and private-file.ps1 beside
# its private plan. Keep both ASCII-only and free of other ODS dependencies.
# ============================================================================

. (Join-Path $PSScriptRoot 'private-file.ps1')

# Required files of a pin.json v1 runtime: the win-vulkan-x64 flavor. A v2
# runtime lists its own in pin.json, so the copied launcher needs no config.
$script:ODSNativeLlamaRequiredFiles = @('llama-server.exe', 'ggml-vulkan.dll')
$script:ODSNativeLlamaExecutableExtensions = @('.exe', '.dll', '.sys', '.com', '.bat', '.cmd', '.ps1', '.psm1', '.vbs', '.js', '.msi', '.scr', '.cpl', '.ocx')
$script:ODSNativeLlamaStartupSeconds = 900
$script:ODSNativeLlamaContextAlignment = 256
$script:ODSNativeLlamaVulkanFlavor = 'win-vulkan-x64'
# The generation check: a question whose only right answer is one token.
$script:ODSNativeLlamaProbeQuestion = 'What is 2+2? Answer with the number only.'
$script:ODSNativeLlamaProbeContext = 4096
# The commit rule the RTX Spark memory planner, this launcher and the model
# controller share (Get-ODSNativeCommitFit). Windows itself commits about 12
# GiB at idle and about 15 GiB beside the full stack on an RTX Spark, so at
# least 16 GiB is kept for it; 2 GiB is slack.
$script:ODSNativeWindowsReserveGiB = 16
$script:ODSNativeCommitMarginGiB = 2
# A refused start waits this long for sign-in to settle (other startup apps,
# the WSL startup task's 30 s delay), re-checking every 5 seconds.
$script:ODSNativeStartWaitSeconds = 120
$script:ODSNativeStartPollSeconds = 5
$script:ODSNativeStartRefusalCodes = @('memory_refused', 'gpu_busy')
$script:ODSNativeHelpLine = 'Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC'

# --- Flavor ---------------------------------------------------------------------

function ConvertFrom-ODSNativeLlamaFlavor([string]$Flavor) {
    <#
    .SYNOPSIS
        Backend and Windows architecture of a llama.cpp release flavor, or
        $null for a flavor ODS does not know.
    #>
    if ($Flavor -ceq $script:ODSNativeLlamaVulkanFlavor) {
        return [pscustomobject]@{ Flavor = $Flavor; Backend = 'vulkan'; Architecture = 'x64'; CudaVersion = '' }
    }
    $match = [regex]::Match($Flavor, '^win-cuda-(?<cuda>[0-9]{1,2}\.[0-9]{1,2})-(?<arch>arm64)$')
    if ($match.Success) {
        return [pscustomobject]@{ Flavor = $Flavor; Backend = 'cuda'; Architecture = $match.Groups['arch'].Value
            CudaVersion = $match.Groups['cuda'].Value }
    }
    return $null
}

function Get-ODSNativeLlamaPinFlavor($Pin) {
    # Pins written before flavors existed are the Vulkan build.
    $flavor = [string]$Pin.Flavor
    if (-not $flavor) { return $script:ODSNativeLlamaVulkanFlavor }
    if (-not (ConvertFrom-ODSNativeLlamaFlavor $flavor)) { throw "Unknown llama.cpp runtime flavor '$flavor'." }
    return $flavor
}

function New-ODSNativeLlamaFailure([string]$Message, [string]$Class) {
    <#
    .SYNOPSIS
        An exception whose Data['class'] says why llama.cpp cannot run here:
        start, policy, dll, timeout, exit, build or driver. The message is the
        owner-facing text; callers choose a route from the class.
    #>
    $exception = [Management.Automation.RuntimeException]::new($Message)
    $exception.Data['class'] = $Class
    return $exception
}

# --- Pin ----------------------------------------------------------------------

function Assert-ODSNativeLlamaArchivePin($Pin) {
    # A flavor shipped as release archives (config/backends/nvidia.json).
    $tag = [string]$Pin.release_tag
    $flavor = ConvertFrom-ODSNativeLlamaFlavor ([string]$Pin.flavor)
    $archives = @($Pin.archives)
    $required = @($Pin.required_files)
    $problem = ''
    if ($tag -cnotmatch '^b[0-9]{3,6}$') { $problem = 'a bNNNN release_tag' }
    elseif (-not $flavor -or $flavor.Backend -ceq 'vulkan' -or [string]$Pin.backend -cne $flavor.Backend) {
        $problem = 'a known flavor (win-cuda-<version>-arm64) and its backend'
    }
    elseif ([string]$Pin.policy_key -cnotmatch '^windows-native(-[a-z0-9]{1,16})?$') { $problem = 'a windows-native policy_key' }
    elseif ($archives.Count -lt 1 -or $archives.Count -gt 2) { $problem = 'one or two release archives' }
    elseif (-not $required.Count -or $required.Count -gt 64 -or @($required | Where-Object { [string]$_ -ceq 'llama-server.exe' }).Count -ne 1) {
        $problem = 'required_files including llama-server.exe'
    }
    elseif ([string]$Pin.min_driver_version -cnotmatch '^[0-9]{2,4}\.[0-9]{1,3}$') { $problem = 'a min_driver_version such as 616.41' }
    if (-not $problem) {
        $names = @("llama-$tag-bin-$($flavor.Flavor).zip", "cudart-llama-bin-$($flavor.Flavor).zip")
        for ($index = 0; $index -lt $archives.Count; $index++) {
            $archive = $archives[$index]
            if ([string]$archive.asset -cne $names[$index] -or [string]$archive.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
                ($archive.size -isnot [int] -and $archive.size -isnot [long]) -or
                [long]$archive.size -lt 1048576 -or [long]$archive.size -gt 536870912) {
                $problem = "archive $($index + 1) as $($names[$index]) with a lowercase SHA-256 and a size in bytes"
                break
            }
        }
    }
    if (-not $problem) {
        $seen = @{}
        foreach ($name in $required) {
            if ([string]$name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$' -or $seen.ContainsKey(([string]$name).ToLowerInvariant())) {
                $problem = 'distinct ASCII required_files'
                break
            }
            $seen[([string]$name).ToLowerInvariant()] = $true
        }
    }
    if (-not $problem) {
        $map = $Pin.cuda_archs_by_compute
        $entries = if ($map) { @($map.PSObject.Properties) } else { @() }
        foreach ($entry in $entries) {
            if ($entry.Name -cnotmatch '^[0-9]{1,2}\.[0-9]$' -or -not @($entry.Value).Count -or
                @($entry.Value | Where-Object { [string]$_ -cnotmatch '^[0-9]{2,4}[a-z]?$' }).Count) {
                $problem = 'cuda_archs_by_compute as compute capability -> CUDA arch names'
                break
            }
        }
        if (-not $entries.Count) { $problem = 'cuda_archs_by_compute as compute capability -> CUDA arch names' }
    }
    if (-not $problem) {
        foreach ($id in @($Pin.model_ladder) + @($Pin.fallback_models)) {
            if ([string]$id -cnotmatch '^[a-z0-9][a-z0-9._-]{0,127}$') { $problem = 'catalog ids in model_ladder and fallback_models'; break }
        }
    }
    if ($problem) { throw "The Windows llama.cpp pin must name $problem." }
}

function Assert-ODSNativeLlamaPin($Pin) {
    if ($Pin.PSObject.Properties['archives']) { Assert-ODSNativeLlamaArchivePin $Pin; return }
    $tag = [string]$Pin.release_tag
    if ($tag -cnotmatch '^b[0-9]{3,6}$' -or
        [string]$Pin.asset -cne "llama-$tag-bin-win-vulkan-x64.zip" -or
        [string]$Pin.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        ($Pin.size -isnot [int] -and $Pin.size -isnot [long]) -or
        [long]$Pin.size -lt 1048576 -or [long]$Pin.size -gt 536870912) {
        throw 'The Windows llama.cpp pin must name a bNNNN tag, its win-vulkan-x64 zip, a lowercase SHA-256 and a size in bytes.'
    }
}

function Read-ODSNativeLlamaConstantsPin([string]$ConstantsPath) {
    # The Portal does not load constants.ps1 (it also sets install paths for
    # the legacy installer). Read the three pin literals with the parser; the
    # file is never executed here.
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($ConstantsPath, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw "Cannot parse $ConstantsPath." }
    $values = @{}
    foreach ($assignment in $ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left -is [Management.Automation.Language.VariableExpressionAst] -and
        $node.Left.VariablePath.UserPath -in @('script:LLAMA_CPP_RELEASE_TAG', 'script:LLAMA_CPP_VULKAN_SHA256', 'script:LLAMA_CPP_VULKAN_SIZE')
    }, $false)) {
        $name = $assignment.Left.VariablePath.UserPath
        $expression = $assignment.Right.Expression
        if ($values.ContainsKey($name) -or $assignment.Right -isnot [Management.Automation.Language.CommandExpressionAst]) {
            throw "constants.ps1 assigns $name more than once or not as a literal."
        }
        if ($name -eq 'script:LLAMA_CPP_RELEASE_TAG') {
            if ($expression -isnot [Management.Automation.Language.StringConstantExpressionAst]) { throw 'constants.ps1 must assign LLAMA_CPP_RELEASE_TAG a literal.' }
            $values[$name] = $expression.Value
            continue
        }
        if ($expression -isnot [Management.Automation.Language.HashtableAst]) { throw "constants.ps1 must assign $name a literal table." }
        $table = @{}
        foreach ($pair in $expression.KeyValuePairs) {
            $value = $pair.Item2.PipelineElements[0].Expression
            if ($pair.Item1 -isnot [Management.Automation.Language.StringConstantExpressionAst] -or
                $value -isnot [Management.Automation.Language.ConstantExpressionAst]) {
                throw "constants.ps1 must list $name entries as literals."
            }
            $table[$pair.Item1.Value] = $value.Value
        }
        $values[$name] = $table
    }
    if ($values.Count -ne 3) { throw 'constants.ps1 does not define the llama.cpp Windows pin.' }
    return [pscustomobject]@{
        ReleaseTag = $values['script:LLAMA_CPP_RELEASE_TAG']
        Sha256 = $values['script:LLAMA_CPP_VULKAN_SHA256']
        Size = $values['script:LLAMA_CPP_VULKAN_SIZE']
    }
}

function Get-ODSNativeLlamaArchivePin([string]$SourceRoot, [string]$ContractName, [string]$Key, [string]$Platform) {
    # Flavors shipped as release archives have no constants.ps1 fallback.
    $relative = "config/backends/$ContractName"
    $contractPath = if ($SourceRoot) { Join-Path (Join-Path (Join-Path $SourceRoot 'config') 'backends') $ContractName } else { '' }
    if (-not $contractPath -or -not (Test-Path -LiteralPath $contractPath -PathType Leaf)) {
        throw "ODS has no pinned llama.cpp build for $Platform ($relative is missing)."
    }
    $contract = Get-Content -LiteralPath $contractPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    $pins = $contract.runtime.llama_server
    if (-not $pins -or -not $pins.PSObject.Properties[$Key]) {
        throw "ODS has no pinned llama.cpp build for $Platform ($relative has no runtime.llama_server.$Key)."
    }
    $pin = $pins.$Key
    Assert-ODSNativeLlamaPin $pin
    $tag = [string]$pin.release_tag
    $flavor = ConvertFrom-ODSNativeLlamaFlavor ([string]$pin.flavor)
    $archives = @($pin.archives | ForEach-Object {
        [pscustomobject]@{ Asset = [string]$_.asset; Sha256 = [string]$_.sha256; Size = [long]$_.size
            Url = "https://github.com/ggml-org/llama.cpp/releases/download/$tag/$([string]$_.asset)" }
    })
    $archs = @{}
    foreach ($entry in @($pin.cuda_archs_by_compute.PSObject.Properties)) { $archs[$entry.Name] = @($entry.Value | ForEach-Object { [string]$_ }) }
    return [pscustomobject]@{
        ReleaseTag = $tag
        Build = [int]$tag.Substring(1)
        # The primary (llama.cpp) archive, under the names the v1 pin uses.
        Asset = $archives[0].Asset
        Sha256 = $archives[0].Sha256
        Size = $archives[0].Size
        Url = $archives[0].Url
        Source = "$relative runtime.llama_server.$Key"
        Flavor = $flavor.Flavor
        Backend = $flavor.Backend
        Architecture = $flavor.Architecture
        CudaVersion = $flavor.CudaVersion
        PolicyKey = [string]$pin.policy_key
        Archives = $archives
        RequiredFiles = @($pin.required_files | ForEach-Object { [string]$_ })
        RequiredCudaArchs = $archs
        MinDriverVersion = [string]$pin.min_driver_version
        ModelLadder = @($pin.model_ladder | ForEach-Object { [string]$_ })
        FallbackModels = @($pin.fallback_models | ForEach-Object { [string]$_ })
    }
}

function Get-ODSNativeLlamaPin {
    <#
    .SYNOPSIS
        The pinned llama.cpp Windows release for a GPU vendor and Windows
        architecture.
    .DESCRIPTION
        AMD (x64): the Vulkan build from config/backends/amd.json
        runtime.llama_server.windows, or constants.ps1 until amd.json has it.
        NVIDIA (arm64): the CUDA build from config/backends/nvidia.json
        runtime.llama_server.windows_arm64, never a fallback.
        Asset, Sha256, Size and Url always describe the primary archive;
        Archives lists every archive of the flavor.
    #>
    param(
        [string]$SourceRoot = '',
        [ValidateSet('amd', 'nvidia', IgnoreCase = $false)][string]$Backend = 'amd',
        [ValidateSet('x64', 'arm64', IgnoreCase = $false)][string]$Architecture = 'x64'
    )

    if ($Backend -ceq 'nvidia') {
        if ($Architecture -cne 'arm64') { throw 'ODS has no Windows llama.cpp build for NVIDIA GPUs on x64 Windows; they run the model in a container in Ubuntu.' }
        return (Get-ODSNativeLlamaArchivePin -SourceRoot $SourceRoot -ContractName 'nvidia.json' -Key 'windows_arm64' `
            -Platform 'NVIDIA GPUs on Windows on Arm')
    }
    if ($Architecture -cne 'x64') { throw 'ODS has no pinned llama.cpp build for AMD GPUs on Windows on Arm.' }
    $pin = $null
    $source = ''
    if ($SourceRoot) {
        $contractPath = Join-Path (Join-Path (Join-Path $SourceRoot 'config') 'backends') 'amd.json'
        if (Test-Path -LiteralPath $contractPath -PathType Leaf) {
            $contract = Get-Content -LiteralPath $contractPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
            $runtime = $contract.runtime
            if ($runtime -and $runtime.PSObject.Properties['llama_server'] -and
                $runtime.llama_server.PSObject.Properties['windows']) {
                # A present but malformed pin is an error, never a fallback.
                $pin = $runtime.llama_server.windows
                $source = 'config/backends/amd.json'
            }
        }
    }
    if ($null -eq $pin) {
        # The legacy installer loads constants.ps1, whose tag a tier may
        # override; the Portal reads the literals without running that file.
        $tag = [string]$script:LLAMA_CPP_RELEASE_TAG
        $hashes = $script:LLAMA_CPP_VULKAN_SHA256
        $sizes = $script:LLAMA_CPP_VULKAN_SIZE
        if (-not $tag -and $null -eq $hashes -and $null -eq $sizes) {
            $constants = Read-ODSNativeLlamaConstantsPin (Join-Path $PSScriptRoot 'constants.ps1')
            $tag = [string]$constants.ReleaseTag
            $hashes = $constants.Sha256
            $sizes = $constants.Size
        }
        if (-not $tag -or $null -eq $hashes -or $null -eq $sizes -or -not $hashes.ContainsKey($tag) -or -not $sizes.ContainsKey($tag)) {
            throw "No pinned SHA-256 and size for llama.cpp '$tag'; refusing an unverified llama-server."
        }
        $pin = [pscustomobject]@{
            release_tag = $tag
            asset = "llama-$tag-bin-win-vulkan-x64.zip"
            sha256 = [string]$hashes[$tag]
            size = [long]$sizes[$tag]
        }
        $source = 'installers/windows/lib/constants.ps1'
    }
    Assert-ODSNativeLlamaPin $pin
    $tag = [string]$pin.release_tag
    $url = "https://github.com/ggml-org/llama.cpp/releases/download/$tag/$([string]$pin.asset)"
    return [pscustomobject]@{
        ReleaseTag = $tag
        Build = [int]$tag.Substring(1)
        Asset = [string]$pin.asset
        Sha256 = [string]$pin.sha256
        Size = [long]$pin.size
        Url = $url
        Source = $source
        Flavor = $script:ODSNativeLlamaVulkanFlavor
        Backend = 'vulkan'
        Architecture = 'x64'
        CudaVersion = ''
        PolicyKey = 'windows-native'
        Archives = @([pscustomobject]@{ Asset = [string]$pin.asset; Sha256 = [string]$pin.sha256; Size = [long]$pin.size; Url = $url })
        RequiredFiles = $script:ODSNativeLlamaRequiredFiles
        RequiredCudaArchs = @{}
        MinDriverVersion = ''
        ModelLadder = @()
        FallbackModels = @()
    }
}

function Get-ODSNativeLlamaRoot {
    $localAppData = $env:LOCALAPPDATA
    if ([string]::IsNullOrWhiteSpace($localAppData)) {
        $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    }
    if ([string]::IsNullOrWhiteSpace($localAppData)) { throw 'Cannot locate %LOCALAPPDATA% for the llama.cpp runtime.' }
    return (Join-Path (Join-Path $localAppData 'ODS') 'llama.cpp')
}

function Get-ODSNativeLlamaInstallDirectory($Pin, [string]$Root = (Get-ODSNativeLlamaRoot)) {
    # <tag>-<flavor>: b9014-win-vulkan-x64 as before, b11429-win-cuda-13.4-arm64.
    return (Join-Path $Root ("{0}-{1}" -f $Pin.ReleaseTag, (Get-ODSNativeLlamaPinFlavor $Pin)))
}

# --- Acquire --------------------------------------------------------------------

function Invoke-ODSNativeLlamaDownload([string]$Url, [string]$Destination) {
    # curl.exe ships with Windows 10 1803+. HTTPS only, including redirects.
    if ($Url -notmatch '^https://github\.com/ggml-org/llama\.cpp/releases/download/') {
        throw "Refusing to download llama.cpp from an unexpected location: $Url"
    }
    & curl.exe --fail --location --proto '=https' --proto-redir '=https' --silent --show-error --output $Destination $Url
    if ($LASTEXITCODE -ne 0) { throw "Could not download llama.cpp (curl exit $LASTEXITCODE): $Url" }
}

function Get-ODSNativeLlamaFileSha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Expand-ODSNativeLlamaArchive([string]$ZipPath, [string]$Destination) {
    # Every entry must stay inside Destination; absolute, drive, stream and
    # parent-directory names are refused before any byte is written.
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $root = [IO.Path]::GetFullPath($Destination).TrimEnd([char[]]@('\', '/')) + [IO.Path]::DirectorySeparatorChar
    $archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $entries = @($archive.Entries)
        if ($entries.Count -eq 0 -or $entries.Count -gt 4096) { throw 'The llama.cpp archive has an unexpected number of entries.' }
        $total = [long]0
        foreach ($entry in $entries) {
            $name = [string]$entry.FullName
            if ([string]::IsNullOrWhiteSpace($name) -or $name -match '[\x00-\x1f:*?"<>|]' -or
                $name -match '^[\\/]' -or $name -match '(^|[\\/])\.{1,2}([\\/]|$)') {
                throw "The llama.cpp archive contains an unsafe entry name: $name"
            }
            $total += [long]$entry.Length
            if ($total -gt 2147483648) { throw 'The llama.cpp archive expands beyond its size limit.' }
            $relative = $name.Replace('\', '/').TrimEnd('/')
            $full = [IO.Path]::GetFullPath((Join-Path $Destination ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))))
            if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
                throw "The llama.cpp archive entry escapes its destination: $name"
            }
            if ($name.EndsWith('/') -or $name.EndsWith('\')) {
                New-Item -ItemType Directory -Path $full -Force | Out-Null
                continue
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $full) -Force | Out-Null
            if (Test-Path -LiteralPath $full) { throw "The llama.cpp archive repeats an entry: $name" }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $full, $false)
        }
    } finally {
        $archive.Dispose()
    }
}

function Get-ODSNativeLlamaPayloadRoot([string]$ExtractDirectory) {
    # Official zips are flat; an archive wrapped in one folder is flattened.
    $children = @(Get-ChildItem -LiteralPath $ExtractDirectory -Force)
    if ($children.Count -eq 1 -and $children[0].PSIsContainer) { return $children[0].FullName }
    return $ExtractDirectory
}

function Get-ODSNativeLlamaFileManifest([string]$Directory) {
    $root = [IO.Path]::GetFullPath($Directory).TrimEnd([char[]]@('\', '/')) + [IO.Path]::DirectorySeparatorChar
    $files = [Collections.Generic.List[object]]::new()
    foreach ($item in @(Get-ChildItem -LiteralPath $Directory -Recurse -Force)) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "The llama.cpp runtime contains a link, which is not allowed: $($item.FullName)"
        }
        if ($item.PSIsContainer) { continue }
        $relative = $item.FullName.Substring($root.Length).Replace('\', '/')
        if ($relative -ceq 'pin.json') { continue }
        $files.Add([pscustomobject]@{ path = $relative; sha256 = (Get-ODSNativeLlamaFileSha256 $item.FullName); size = [long]$item.Length })
    }
    return @($files | Sort-Object -Property path)
}

function Test-ODSNativeLlamaInstall {
    <#
    .SYNOPSIS
        Verify a versioned runtime directory against its pin.json. Every listed
        file must match its SHA-256 and no unlisted executable code may appear.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [string]$ExpectedZipSha256 = '',
        [string]$ExpectedReleaseTag = '',
        # A multi-archive pin's Archives: the runtime must come from exactly these.
        [object[]]$ExpectedArchives = @()
    )
    $pinPath = Join-Path $Directory 'pin.json'
    if (-not (Test-Path -LiteralPath $pinPath -PathType Leaf)) { throw "The llama.cpp runtime has no pin.json: $Directory" }
    $record = [IO.File]::ReadAllText($pinPath, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json -ErrorAction Stop
    if (($record.schemaVersion -ne 1 -and $record.schemaVersion -ne 2) -or [string]$record.releaseTag -cnotmatch '^b[0-9]{3,6}$' -or
        [string]$record.zipSha256 -cnotmatch '^[0-9a-f]{64}$' -or @($record.files).Count -eq 0) {
        throw "The llama.cpp pin.json is invalid: $pinPath"
    }
    $requiredFiles = $script:ODSNativeLlamaRequiredFiles
    $flavor = $script:ODSNativeLlamaVulkanFlavor
    if ($record.schemaVersion -eq 2) {
        $requiredFiles = Get-ODSNativeLlamaRecordRequiredFiles $record $pinPath
        $flavor = [string]$record.flavor
    }
    if ($ExpectedReleaseTag -and [string]$record.releaseTag -cne $ExpectedReleaseTag) {
        throw "The llama.cpp runtime is $($record.releaseTag), not the pinned $ExpectedReleaseTag."
    }
    if ($ExpectedZipSha256 -and [string]$record.zipSha256 -cne $ExpectedZipSha256) {
        throw 'The llama.cpp runtime was installed from a different archive than the pinned one.'
    }
    if ($ExpectedArchives.Count) {
        $installed = if ($record.schemaVersion -eq 2) { @($record.archives | ForEach-Object { "$($_.asset)|$($_.sha256)" }) } else { @() }
        $pinned = @($ExpectedArchives | ForEach-Object { "$($_.Asset)|$($_.Sha256)" })
        if (($installed -join ',') -cne ($pinned -join ',')) {
            throw 'The llama.cpp runtime was installed from different archives than the pinned ones.'
        }
    }
    $listed = @{}
    foreach ($file in @($record.files)) {
        $path = [string]$file.path
        if ($path -notmatch '^[^\x00-\x1f\\:*?"<>|/]+(/[^\x00-\x1f\\:*?"<>|/]+)*$' -or $path -match '(^|/)\.{1,2}(/|$)' -or
            [string]$file.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $listed.ContainsKey($path.ToLowerInvariant())) {
            throw "The llama.cpp pin.json lists an invalid file: $path"
        }
        $listed[$path.ToLowerInvariant()] = $file
    }
    foreach ($required in $requiredFiles) {
        if (-not $listed.ContainsKey(([string]$required).ToLowerInvariant())) { throw "The llama.cpp runtime is missing $required." }
    }
    $actual = @{}
    foreach ($file in (Get-ODSNativeLlamaFileManifest $Directory)) { $actual[$file.path.ToLowerInvariant()] = $file }
    foreach ($key in $listed.Keys) {
        if (-not $actual.ContainsKey($key)) {
            throw "llama.cpp file $($listed[$key].path) is missing (antivirus quarantine or a partial copy). Rerun setup to restore it."
        }
        if ($actual[$key].sha256 -cne [string]$listed[$key].sha256) {
            throw "llama.cpp file $($listed[$key].path) no longer matches its pinned SHA-256. Rerun setup to restore it."
        }
    }
    foreach ($key in $actual.Keys) {
        if (-not $listed.ContainsKey($key) -and
            [IO.Path]::GetExtension($key) -in $script:ODSNativeLlamaExecutableExtensions) {
            throw "An unpinned executable file appeared in the llama.cpp runtime: $($actual[$key].path)"
        }
    }
    return [pscustomobject]@{
        Directory = $Directory
        ExecutablePath = (Join-Path $Directory 'llama-server.exe')
        ReleaseTag = [string]$record.releaseTag
        ZipSha256 = [string]$record.zipSha256
        FileCount = $listed.Count
        Flavor = $flavor
    }
}

function Get-ODSNativeLlamaRecordRequiredFiles($Record, [string]$PinPath) {
    # pin.json v2: a known non-Vulkan flavor, its archives (primary first) and
    # the required files the flavor's pin listed, llama-server.exe included.
    $flavor = ConvertFrom-ODSNativeLlamaFlavor ([string]$Record.flavor)
    $archives = @($Record.archives)
    $required = @($Record.requiredFiles | ForEach-Object { [string]$_ })
    $valid = $flavor -and $flavor.Backend -cne 'vulkan' -and [string]$Record.backend -ceq $flavor.Backend -and
        $archives.Count -ge 1 -and $archives.Count -le 2 -and [string]$archives[0].sha256 -ceq [string]$Record.zipSha256 -and
        [string]$archives[0].asset -ceq "llama-$($Record.releaseTag)-bin-$($flavor.Flavor).zip" -and
        @($archives | Where-Object { [string]$_.sha256 -cnotmatch '^[0-9a-f]{64}$' }).Count -eq 0 -and
        $required -ccontains 'llama-server.exe' -and
        @($required | Where-Object { $_ -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$' }).Count -eq 0
    if ($archives.Count -eq 2 -and [string]$archives[1].asset -cne "cudart-llama-bin-$($flavor.Flavor).zip") { $valid = $false }
    if (-not $valid) { throw "The llama.cpp pin.json is invalid: $PinPath" }
    return $required
}

function Install-ODSNativeLlamaRuntime {
    <#
    .SYNOPSIS
        Download, verify and publish the pinned llama.cpp Vulkan runtime.
    .DESCRIPTION
        Idempotent: a verified versioned directory is reused. Otherwise the zip
        lands in a private staging directory, its size and SHA-256 are checked
        before extraction, entries are extracted traversal-safely, pin.json
        records every file hash, and one rename publishes the directory.
        A flavor other than Vulkan (several archives) goes through
        Install-ODSNativeLlamaArchiveSet; the Vulkan path below is unchanged.
    #>
    param(
        [Parameter(Mandatory = $true)]$Pin,
        [string]$Root = (Get-ODSNativeLlamaRoot)
    )
    if ((Get-ODSNativeLlamaPinFlavor $Pin) -cne $script:ODSNativeLlamaVulkanFlavor) {
        return (Install-ODSNativeLlamaArchiveSet -Pin $Pin -Root $Root)
    }
    $target = Get-ODSNativeLlamaInstallDirectory $Pin $Root
    if (Test-Path -LiteralPath $target) {
        try {
            return (Test-ODSNativeLlamaInstall -Directory $target -ExpectedZipSha256 $Pin.Sha256 -ExpectedReleaseTag $Pin.ReleaseTag)
        } catch {
            # Keep the damaged copy for diagnosis; a new one is published below.
            $damaged = Join-Path $Root ('.damaged-{0}-{1}' -f $Pin.ReleaseTag, [guid]::NewGuid().ToString('N'))
            [IO.Directory]::Move($target, $damaged)
            Write-Host "         The existing llama.cpp $($Pin.ReleaseTag) failed verification ($($_.Exception.Message)); downloading it again."
        }
    }
    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    $staging = Join-Path $Root ('.staging-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging | Out-Null
    try {
        $zip = Join-Path $staging $Pin.Asset
        Invoke-ODSNativeLlamaDownload $Pin.Url $zip
        $length = (Get-Item -LiteralPath $zip).Length
        if ($length -ne $Pin.Size) {
            throw "The llama.cpp download is $length bytes, not the pinned $($Pin.Size); it was discarded."
        }
        $actualSha = Get-ODSNativeLlamaFileSha256 $zip
        if ($actualSha -cne $Pin.Sha256) {
            throw "The llama.cpp download does not match its pinned SHA-256 (got $actualSha, expected $($Pin.Sha256)); it was discarded."
        }
        $extract = Join-Path $staging 'extract'
        Expand-ODSNativeLlamaArchive $zip $extract
        $payload = Get-ODSNativeLlamaPayloadRoot $extract
        foreach ($required in $script:ODSNativeLlamaRequiredFiles) {
            if (-not (Test-Path -LiteralPath (Join-Path $payload $required) -PathType Leaf)) {
                throw "The llama.cpp archive has no $required; it is not the Windows Vulkan build."
            }
        }
        $record = [ordered]@{
            schemaVersion = 1
            releaseTag = $Pin.ReleaseTag
            asset = $Pin.Asset
            zipSha256 = $Pin.Sha256
            zipSize = $Pin.Size
            source = "https://github.com/ggml-org/llama.cpp/releases/tag/$($Pin.ReleaseTag)"
            files = @(Get-ODSNativeLlamaFileManifest $payload)
        }
        Write-ODSPrivateEnvFile -Path (Join-Path $payload 'pin.json') -Content ($record | ConvertTo-Json -Depth 4)
        [IO.Directory]::Move($payload, $target)
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
    return (Test-ODSNativeLlamaInstall -Directory $target -ExpectedZipSha256 $Pin.Sha256 -ExpectedReleaseTag $Pin.ReleaseTag)
}

function Merge-ODSNativeLlamaPayload([string]$Source, [string]$Destination, [hashtable]$Origins, [string]$Asset) {
    # One archive's files join the runtime. A path another archive already
    # supplied is kept once when the bytes agree and refused when they differ.
    $root = [IO.Path]::GetFullPath($Source).TrimEnd([char[]]@('\', '/')) + [IO.Path]::DirectorySeparatorChar
    foreach ($item in @(Get-ChildItem -LiteralPath $Source -Recurse -Force -File)) {
        $relative = $item.FullName.Substring($root.Length)
        $key = $relative.Replace('\', '/').ToLowerInvariant()
        $sha = Get-ODSNativeLlamaFileSha256 $item.FullName
        if ($Origins.ContainsKey($key)) {
            if ($Origins[$key].Sha256 -cne $sha) {
                throw "The llama.cpp archives disagree on $($relative.Replace('\', '/')) ($($Origins[$key].Asset) and $Asset ship different files); nothing was installed."
            }
            continue
        }
        $target = Join-Path $Destination $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        [IO.File]::Move($item.FullName, $target)
        $Origins[$key] = @{ Sha256 = $sha; Asset = $Asset }
    }
}

function Install-ODSNativeLlamaArchiveSet {
    <#
    .SYNOPSIS
        Download, verify and publish a llama.cpp flavor shipped as several
        archives (the CUDA build and its CUDA runtime).
    .DESCRIPTION
        Idempotent like the Vulkan path. Each archive's size and SHA-256 are
        checked before it is extracted into its own folder; the folders are
        merged, the flavor's required files must all be present, and pin.json
        (schemaVersion 2) records the archives, the required files and a
        SHA-256 for every file before one rename publishes the directory.
    #>
    param(
        [Parameter(Mandatory = $true)]$Pin,
        [string]$Root = (Get-ODSNativeLlamaRoot)
    )
    $flavor = ConvertFrom-ODSNativeLlamaFlavor (Get-ODSNativeLlamaPinFlavor $Pin)
    $archives = @($Pin.Archives)
    if ($archives.Count -lt 1 -or $archives.Count -gt 2 -or [string]$Pin.Sha256 -cne [string]$archives[0].Sha256 -or -not @($Pin.RequiredFiles).Count) {
        throw "The llama.cpp $($flavor.Flavor) pin must list one or two archives, the llama.cpp archive first, and its required files."
    }
    $target = Get-ODSNativeLlamaInstallDirectory $Pin $Root
    if (Test-Path -LiteralPath $target) {
        try {
            return (Test-ODSNativeLlamaInstall -Directory $target -ExpectedZipSha256 $Pin.Sha256 -ExpectedReleaseTag $Pin.ReleaseTag `
                -ExpectedArchives $archives)
        } catch {
            # Keep the damaged copy for diagnosis; a new one is published below.
            $damaged = Join-Path $Root ('.damaged-{0}-{1}' -f $Pin.ReleaseTag, [guid]::NewGuid().ToString('N'))
            [IO.Directory]::Move($target, $damaged)
            Write-Host "         The existing llama.cpp $($Pin.ReleaseTag) ($($flavor.Flavor)) failed verification ($($_.Exception.Message)); downloading it again."
        }
    }
    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    $staging = Join-Path $Root ('.staging-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging | Out-Null
    try {
        $merged = Join-Path $staging 'runtime'
        New-Item -ItemType Directory -Path $merged | Out-Null
        $origins = @{}
        for ($index = 0; $index -lt $archives.Count; $index++) {
            $archive = $archives[$index]
            $download = Join-Path $staging $archive.Asset
            Invoke-ODSNativeLlamaDownload $archive.Url $download
            $length = (Get-Item -LiteralPath $download).Length
            if ($length -ne $archive.Size) {
                throw "The llama.cpp download $($archive.Asset) is $length bytes, not the pinned $($archive.Size); it was discarded."
            }
            $actualSha = Get-ODSNativeLlamaFileSha256 $download
            if ($actualSha -cne $archive.Sha256) {
                throw "The llama.cpp download $($archive.Asset) does not match its pinned SHA-256 (got $actualSha, expected $($archive.Sha256)); it was discarded."
            }
            $extract = Join-Path $staging "extract-$index"
            Expand-ODSNativeLlamaArchive $download $extract
            Merge-ODSNativeLlamaPayload -Source (Get-ODSNativeLlamaPayloadRoot $extract) -Destination $merged -Origins $origins -Asset $archive.Asset
            Remove-Item -LiteralPath $download -Force
        }
        foreach ($required in @($Pin.RequiredFiles)) {
            if (-not (Test-Path -LiteralPath (Join-Path $merged $required) -PathType Leaf)) {
                throw "The llama.cpp archives have no $required; they are not the Windows $($flavor.Flavor) build."
            }
        }
        $record = [ordered]@{
            schemaVersion = 2
            releaseTag = $Pin.ReleaseTag
            flavor = $flavor.Flavor
            backend = $flavor.Backend
            archives = @($archives | ForEach-Object { [ordered]@{ asset = $_.Asset; sha256 = $_.Sha256; size = $_.Size } })
            zipSha256 = $Pin.Sha256
            requiredFiles = @($Pin.RequiredFiles)
            source = "https://github.com/ggml-org/llama.cpp/releases/tag/$($Pin.ReleaseTag)"
            files = @(Get-ODSNativeLlamaFileManifest $merged)
        }
        Write-ODSPrivateEnvFile -Path (Join-Path $merged 'pin.json') -Content ($record | ConvertTo-Json -Depth 4)
        [IO.Directory]::Move($merged, $target)
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
    return (Test-ODSNativeLlamaInstall -Directory $target -ExpectedZipSha256 $Pin.Sha256 -ExpectedReleaseTag $Pin.ReleaseTag `
        -ExpectedArchives $archives)
}

function Publish-ODSNativeLlamaRuntimeCopy {
    <#
    .SYNOPSIS
        Publish a verified runtime at a fixed path. The legacy native installer
        keeps <InstallDir>\llama-server, which ods.ps1, bootstrap-upgrade.sh and
        the host agent's restart path launch. Only a copy whose pin.json no
        longer verifies (an older tag, a damaged or unpinned copy) is replaced,
        with one directory swap; the caller stops that copy's process first.
    #>
    param([Parameter(Mandatory = $true)]$Runtime, [Parameter(Mandatory = $true)][string]$Destination)
    if (Test-Path -LiteralPath $Destination) {
        try {
            $current = Test-ODSNativeLlamaInstall -Directory $Destination -ExpectedZipSha256 $Runtime.ZipSha256 -ExpectedReleaseTag $Runtime.ReleaseTag
            return [pscustomobject]@{ Directory = $current.Directory; ExecutablePath = $current.ExecutablePath; Changed = $false }
        } catch { }
    }
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $leaf = Split-Path -Leaf $Destination
    $staging = Join-Path $parent (".$leaf.staging-" + [guid]::NewGuid().ToString('N'))
    try {
        Copy-Item -LiteralPath $Runtime.Directory -Destination $staging -Recurse
        $null = Test-ODSNativeLlamaInstall -Directory $staging -ExpectedZipSha256 $Runtime.ZipSha256 -ExpectedReleaseTag $Runtime.ReleaseTag
        if (Test-Path -LiteralPath $Destination) {
            $previous = Join-Path $parent (".$leaf.previous-" + [guid]::NewGuid().ToString('N'))
            try {
                [IO.Directory]::Move($Destination, $previous)
            } catch {
                throw "Cannot replace $Destination while a program still uses it; stop llama-server and rerun. ($($_.Exception.Message))"
            }
            try {
                [IO.Directory]::Move($staging, $Destination)
            } catch {
                [IO.Directory]::Move($previous, $Destination)
                throw
            }
            Remove-Item -LiteralPath $previous -Recurse -Force
        } else {
            [IO.Directory]::Move($staging, $Destination)
        }
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
    return [pscustomobject]@{ Directory = $Destination; ExecutablePath = (Join-Path $Destination 'llama-server.exe'); Changed = $true }
}

function Get-ODSNativeRuntimeDir {
    # Private files of the legacy native runtime (API key file, launch options,
    # log). Outside the install folder, which containers mount.
    return (Join-Path (Split-Path -Parent (Get-ODSNativeLlamaRoot)) 'native-runtime')
}

function Remove-ODSNativeLlamaOldRuntimes {
    # Keep the active runtime and one previous one (rollback); remove older
    # ODS-created versioned, staging and damaged copies only.
    param([string]$KeepDirectory, [string]$PreviousDirectory = '', [string]$Root = (Get-ODSNativeLlamaRoot))
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return }
    $keep = @($KeepDirectory, $PreviousDirectory) | Where-Object { $_ } | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') }
    foreach ($child in @(Get-ChildItem -LiteralPath $Root -Directory -Force)) {
        if ($child.Name -notmatch '^(b[0-9]{3,6}-win-vulkan-x64|b[0-9]{3,6}-win-cuda-[0-9.]+-arm64|\.staging-[0-9a-f]{32}|\.damaged-b[0-9]{3,6}-[0-9a-f]{32})$') { continue }
        if ([IO.Path]::GetFullPath($child.FullName).TrimEnd('\') -in $keep) { continue }
        Remove-Item -LiteralPath $child.FullName -Recurse -Force
    }
}

# --- Qualify --------------------------------------------------------------------

function Invoke-ODSNativeLlamaProbe {
    <#
    .SYNOPSIS
        Run llama-server.exe with read-only arguments (--version,
        --list-devices) and capture its output, bounded in time.
    #>
    param([string]$ExecutablePath, [string[]]$Arguments, [int]$TimeoutSeconds = 60)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $ExecutablePath
    $psi.Arguments = ($Arguments -join ' ')
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.WorkingDirectory = Split-Path -Parent $ExecutablePath
    try {
        $process = [System.Diagnostics.Process]::Start($psi)
    } catch {
        $cause = $_.Exception
        while ($cause.InnerException) { $cause = $cause.InnerException }
        $native = if ($cause -is [System.ComponentModel.Win32Exception]) { $cause.NativeErrorCode } else { 0 }
        return [pscustomobject]@{ Started = $false; ExitCode = $null; Output = ''; TimedOut = $false
            StartError = $cause.Message; NativeError = $native }
    }
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill() } catch { }
            return [pscustomobject]@{ Started = $true; ExitCode = $null; Output = ''; TimedOut = $true; StartError = ''; NativeError = 0 }
        }
        $process.WaitForExit()
        return [pscustomobject]@{ Started = $true; ExitCode = $process.ExitCode
            Output = ([string]$stdout.Result + [string]$stderr.Result); TimedOut = $false; StartError = ''; NativeError = 0 }
    } finally {
        $process.Dispose()
    }
}

function Get-ODSNativeLlamaExitHint([int]$ExitCode, [string]$Backend = 'vulkan', [string]$Architecture = 'x64') {
    # NTSTATUS values arrive as negative 32-bit exit codes. The Visual C++
    # runtime and image wording follow the Windows architecture, the driver
    # wording the GPU backend; Vulkan on x64 keeps its original text.
    $arm64 = $Architecture -ceq 'arm64'
    $nvidia = $Backend -ceq 'cuda'
    if ($ExitCode -eq -1073741515) {
        if ($arm64) { return 'A DLL it needs is missing (0xC0000135), most likely the Microsoft Visual C++ 2015-2022 Redistributable for Arm64. Install it from https://aka.ms/vs/17/release/vc_redist.arm64.exe or with: winget install Microsoft.VCRedist.2015+.arm64' }
        return 'A DLL it needs is missing (0xC0000135), most likely the Microsoft Visual C++ 2015-2022 Redistributable (x64). Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe or with: winget install Microsoft.VCRedist.2015+.x64'
    }
    if ($ExitCode -eq -1073740760) { return 'Windows code integrity refused one of its files (0xC0000428). Smart App Control, Windows Defender Application Control or another allow-list policy blocks the unsigned llama.cpp binaries.' }
    if ($ExitCode -eq -1073741701) {
        if ($arm64) { return 'Windows reports a bad image (0xC000007B): the download is damaged or not built for this processor (Arm64).' }
        return 'Windows reports a bad image (0xC000007B): the download is damaged or not built for x64.'
    }
    if ($ExitCode -eq -1073741502) {
        if ($nvidia) { return 'A DLL failed to initialize (0xC0000142). Restart Windows, update the NVIDIA graphics driver with the NVIDIA App or from nvidia.com, then rerun setup.' }
        return 'A DLL failed to initialize (0xC0000142). Restart Windows, update the AMD graphics driver, then rerun setup.'
    }
    if ($ExitCode -eq -1073741819) {
        if ($nvidia) { return 'It crashed (0xC0000005), usually inside the graphics driver. Update the NVIDIA graphics driver with the NVIDIA App or from nvidia.com, then rerun setup.' }
        return 'It crashed (0xC0000005), usually inside the graphics driver. Update the AMD Adrenalin driver from amd.com/support, then rerun setup.'
    }
    return ''
}

function Get-ODSNativeLlamaFailureClass($Probe, $CodeIntegrity = $null) {
    <#
    .SYNOPSIS
        Why a llama-server.exe run failed: policy (code integrity), start, dll
        (0xC0000135, a missing runtime DLL), timeout or exit.
    #>
    $integrityOn = $CodeIntegrity -and ($CodeIntegrity.SmartAppControl -eq 'on' -or $CodeIntegrity.UserModeCodeIntegrity -eq 'enforced')
    if (-not $Probe.Started) {
        # 1260 ERROR_ACCESS_DISABLED_BY_POLICY, 4551 an application control
        # policy; with Smart App Control or WDAC enforced, any blocked start
        # except a missing file is most likely that policy.
        if ($Probe.NativeError -in @(1260, 4551) -or ($integrityOn -and $Probe.NativeError -ne 2)) { return 'policy' }
        return 'start'
    }
    if ($Probe.TimedOut) { return 'timeout' }
    if ($Probe.ExitCode -eq -1073741515) { return 'dll' }
    if ($Probe.ExitCode -eq -1073740760) { return 'policy' }
    return 'exit'
}

function Get-ODSNativeLlamaStartHint([int]$NativeError) {
    if ($NativeError -eq 1260) { return 'A Windows policy blocks it (ERROR_ACCESS_DISABLED_BY_POLICY).' }
    if ($NativeError -eq 4551) { return 'Smart App Control or Windows Defender Application Control blocks it.' }
    if ($NativeError -eq 225) { return 'Antivirus software flagged it.' }
    if ($NativeError -eq 2) { return 'The file is gone, often removed by antivirus software after download.' }
    if ($NativeError -eq 5) { return 'Windows denied access to it.' }
    return ''
}

function Get-ODSNativeLlamaCodeIntegrityState {
    <#
    .SYNOPSIS
        Read-only Smart App Control and WDAC state, used only for guidance.
        ODS never changes either setting.
    #>
    $smartAppControl = 'unknown'
    try {
        $value = Get-ItemPropertyValue -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -Name 'VerifiedAndReputablePolicyState' -ErrorAction Stop
        $smartAppControl = switch ([int]$value) { 0 { 'off' } 1 { 'on' } 2 { 'evaluation' } default { 'unknown' } }
    } catch { }
    $userModeIntegrity = 'unknown'
    try {
        $guard = Get-CimInstance -Namespace 'root\Microsoft\Windows\DeviceGuard' -ClassName Win32_DeviceGuard -ErrorAction Stop
        $userModeIntegrity = switch ([int]$guard.UsermodeCodeIntegrityPolicyEnforcementStatus) { 0 { 'off' } 1 { 'audit' } 2 { 'enforced' } default { 'unknown' } }
    } catch { }
    return [pscustomobject]@{ SmartAppControl = $smartAppControl; UserModeCodeIntegrity = $userModeIntegrity }
}

function Format-ODSNativeLlamaPolicyHint($State) {
    $hints = @()
    if ($State.SmartAppControl -eq 'on') {
        $hints += 'Smart App Control is on and can block the unsigned llama.cpp binaries (they are SHA-256 pinned, not signed).'
    } elseif ($State.SmartAppControl -eq 'evaluation') {
        $hints += 'Smart App Control is in evaluation mode; if Windows turns it on later it can block the unsigned llama.cpp binaries.'
    }
    if ($State.UserModeCodeIntegrity -eq 'enforced') {
        $hints += 'A Windows Defender Application Control policy is enforced and can block the unsigned llama.cpp binaries.'
    }
    if ($hints.Count) { $hints += 'ODS does not change these settings. See Windows Security > App & browser control.' }
    return ($hints -join ' ')
}

function ConvertFrom-ODSNativeLlamaDeviceList([string]$Output) {
    # llama.cpp b9014 prints "  Vulkan0: <name> (<total> MiB, <free> MiB free)";
    # the b11429 CUDA build "  CUDA0: <name> (...)" the same way. Under WDDM
    # the CUDA sizes are this process's budget, not the GPU's memory.
    $devices = [Collections.Generic.List[object]]::new()
    foreach ($line in ($Output -split "`r?`n")) {
        $match = [regex]::Match($line, '^\s+(?<name>[A-Za-z][A-Za-z0-9_-]*[0-9]+):\s+(?<description>.+?)\s+\((?<total>[0-9]+) MiB, (?<free>[0-9]+) MiB free\)\s*$')
        if (-not $match.Success) { continue }
        $devices.Add([pscustomobject]@{
            Name = $match.Groups['name'].Value
            Description = $match.Groups['description'].Value
            TotalMiB = [long]$match.Groups['total'].Value
            FreeMiB = [long]$match.Groups['free'].Value
        })
    }
    return @($devices)
}

function ConvertTo-ODSNativeLlamaAdapterKey([string]$Name) {
    $text = ([string]$Name).ToLowerInvariant().Replace([string][char]0x00AE, ' ').Replace([string][char]0x2122, ' ')
    $text = $text -replace '\((tm|r)\)', ' '
    return (($text -replace '[^a-z0-9]+', ' ').Trim())
}

function Select-ODSNativeLlamaDevice {
    <#
    .SYNOPSIS
        Choose the Vulkan device for the detected AMD adapter, or $null.
    .DESCRIPTION
        Software rasterizers and other vendors' GPUs are never chosen. With no
        name match, a single AMD-looking Vulkan device is accepted. -Backend
        cuda picks among CUDA devices instead (Select-ODSNativeLlamaCudaDevice).
    #>
    param([object[]]$Devices, [string]$AdapterName, [ValidateSet('vulkan', 'cuda', IgnoreCase = $false)][string]$Backend = 'vulkan')
    if ($Backend -ceq 'cuda') { return (Select-ODSNativeLlamaCudaDevice -Devices $Devices -AdapterName $AdapterName) }
    $vulkan = @($Devices | Where-Object {
        $_.Name -cmatch '^Vulkan[0-9]{1,2}$' -and
        $_.Description -notmatch '(?i)llvmpipe|swiftshader|lavapipe|software|basic render|microsoft' -and
        $_.Description -notmatch '(?i)nvidia|geforce|quadro|intel|arc\b|iris|uhd graphics'
    })
    if ($vulkan.Count -eq 0) { return $null }
    $target = ConvertTo-ODSNativeLlamaAdapterKey $AdapterName
    if ($target) {
        $exact = @($vulkan | Where-Object { (ConvertTo-ODSNativeLlamaAdapterKey $_.Description) -eq $target })
        if ($exact.Count -ge 1) { return $exact[0] }
        $partial = @($vulkan | Where-Object {
            $key = ConvertTo-ODSNativeLlamaAdapterKey $_.Description
            $key -and ($key.Contains($target) -or $target.Contains($key))
        })
        if ($partial.Count -ge 1) { return $partial[0] }
    }
    $amd = @($vulkan | Where-Object { $_.Description -match '(?i)\bamd\b|radeon' })
    if ($amd.Count -eq 1) { return $amd[0] }
    return $null
}

function Select-ODSNativeLlamaCudaDevice {
    <#
    .SYNOPSIS
        Choose the CUDA device for the detected NVIDIA adapter, or $null.
    .DESCRIPTION
        Only CUDAn devices count; llama.cpp lists no NPU among them. A name
        match wins; with no match a single CUDA device is accepted, never one
        of several.
    #>
    param([object[]]$Devices, [string]$AdapterName)
    $cuda = @($Devices | Where-Object { $_.Name -cmatch '^CUDA[0-9]{1,2}$' })
    if ($cuda.Count -eq 0) { return $null }
    $target = ConvertTo-ODSNativeLlamaAdapterKey $AdapterName
    if ($target) {
        $exact = @($cuda | Where-Object { (ConvertTo-ODSNativeLlamaAdapterKey $_.Description) -eq $target })
        if ($exact.Count -ge 1) { return $exact[0] }
        $partial = @($cuda | Where-Object {
            $key = ConvertTo-ODSNativeLlamaAdapterKey $_.Description
            $key -and ($key.Contains($target) -or $target.Contains($key))
        })
        if ($partial.Count -ge 1) { return $partial[0] }
    }
    if ($cuda.Count -eq 1) { return $cuda[0] }
    return $null
}

function ConvertFrom-ODSNativeLlamaVersion([string]$Output) {
    # b9014: "version: 9014 (d4b0c22f)". b11429: "version: 0.6.0-dev (build
    # 11429, commit d81235049)". $null when neither form is present.
    $match = [regex]::Match($Output, '(?m)^version:\s*([0-9]+)\s*\(([0-9a-f]+)\)')
    if ($match.Success) { return [pscustomobject]@{ Build = [int]$match.Groups[1].Value; Commit = $match.Groups[2].Value } }
    $match = [regex]::Match($Output, '(?m)^version:\s*[0-9A-Za-z.+-]{1,32}\s*\(build\s+([0-9]{1,7}),\s*commit\s+([0-9a-f]{6,40})\)')
    if ($match.Success) { return [pscustomobject]@{ Build = [int]$match.Groups[1].Value; Commit = $match.Groups[2].Value } }
    return $null
}

function Test-ODSNativeLlamaQualification {
    <#
    .SYNOPSIS
        Qualify an installed llama-server.exe before any cutover.
    .OUTPUTS
        Build, Devices and Device ($null when no usable device of -Backend
        exists). Throws with guidance when the binary cannot run at all; the
        exception's Data['class'] is start, policy, dll, timeout, exit or build.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ExecutablePath,
        [Parameter(Mandatory = $true)][int]$ExpectedBuild,
        [string]$AdapterName = '',
        $CodeIntegrity = $null,
        [ValidateSet('vulkan', 'cuda', IgnoreCase = $false)][string]$Backend = 'vulkan',
        [ValidateSet('x64', 'arm64', IgnoreCase = $false)][string]$Architecture = 'x64'
    )
    if ($null -eq $CodeIntegrity) { $CodeIntegrity = Get-ODSNativeLlamaCodeIntegrityState }
    $policyHint = Format-ODSNativeLlamaPolicyHint $CodeIntegrity
    $version = Invoke-ODSNativeLlamaProbe -ExecutablePath $ExecutablePath -Arguments @('--version')
    $class = Get-ODSNativeLlamaFailureClass $version $CodeIntegrity
    if (-not $version.Started) {
        throw (New-ODSNativeLlamaFailure ("llama-server.exe could not start: $($version.StartError) " + (Get-ODSNativeLlamaStartHint $version.NativeError) + " $policyHint").Trim() $class)
    }
    if ($version.TimedOut) { throw (New-ODSNativeLlamaFailure 'llama-server.exe --version did not finish within 60 seconds.' $class) }
    if ($version.ExitCode -ne 0) {
        $hint = Get-ODSNativeLlamaExitHint $version.ExitCode $Backend $Architecture
        throw (New-ODSNativeLlamaFailure ("llama-server.exe --version failed with exit code $($version.ExitCode). $hint $policyHint").Trim() $class)
    }
    $parsed = ConvertFrom-ODSNativeLlamaVersion ([string]$version.Output)
    if (-not $parsed) { throw (New-ODSNativeLlamaFailure 'llama-server.exe --version did not report a build number.' 'build') }
    $build = $parsed.Build
    if ($build -ne $ExpectedBuild) { throw (New-ODSNativeLlamaFailure "llama-server.exe reports build $build, not the pinned b$ExpectedBuild." 'build') }
    $list = Invoke-ODSNativeLlamaProbe -ExecutablePath $ExecutablePath -Arguments @('--list-devices')
    if (-not $list.Started -or $list.TimedOut -or $list.ExitCode -ne 0) {
        $code = if ($list.Started -and -not $list.TimedOut) { $list.ExitCode } else { 0 }
        throw (New-ODSNativeLlamaFailure ("llama-server.exe --list-devices failed. " + (Get-ODSNativeLlamaExitHint $code $Backend $Architecture) + " $policyHint").Trim() `
            (Get-ODSNativeLlamaFailureClass $list $CodeIntegrity))
    }
    $devices = @(ConvertFrom-ODSNativeLlamaDeviceList ([string]$list.Output))
    $device = Select-ODSNativeLlamaDevice -Devices $devices -AdapterName $AdapterName -Backend $Backend
    return [pscustomobject]@{
        Build = $build
        Commit = $parsed.Commit
        Devices = $devices
        Device = $device
        PolicyHint = $policyHint
        Backend = $Backend
    }
}

# --- CUDA qualification ---------------------------------------------------------
# A CUDA build can start, list the GPU and still answer "????" when it has no
# kernels for the GPU's compute capability (the DGX Spark GB10 with the CUDA
# 12.8 image, docs/DGX-SPARK-GB10-LLAMA-CPP-NOTES.md). So a CUDA runtime is
# qualified on the GPU itself with a small model before any large download:
# the CUDA targets llama-server reports, and a "2+2" answer.

function Test-ODSNativeLlamaDriverVersion([string]$Installed, [string]$Minimum) {
    # NVIDIA Windows driver versions (616.62) compare part by part as numbers.
    $have = [regex]::Match(([string]$Installed).Trim(), '^([0-9]{2,4})\.([0-9]{1,3})$')
    $need = [regex]::Match([string]$Minimum, '^([0-9]{2,4})\.([0-9]{1,3})$')
    if (-not $have.Success -or -not $need.Success) { return $false }
    $haveMajor = [int]$have.Groups[1].Value
    $needMajor = [int]$need.Groups[1].Value
    if ($haveMajor -ne $needMajor) { return $haveMajor -gt $needMajor }
    return ([int]$have.Groups[2].Value -ge [int]$need.Groups[2].Value)
}

function Assert-ODSNativeLlamaDriver($Pin, [string]$DriverVersion) {
    <#
    .SYNOPSIS
        Before any download: refuse an NVIDIA driver older than the CUDA
        flavor's minimum (CUDA 13.4 release notes: 616.41 for the RTX Spark).
        The exception's Data['class'] is driver.
    #>
    if (-not $Pin.MinDriverVersion) { return }
    $build = if ($Pin.CudaVersion) { "llama.cpp CUDA $($Pin.CudaVersion) build" } else { 'llama.cpp build' }
    if ([string]$DriverVersion -notmatch '^\s*[0-9]{2,4}\.[0-9]{1,3}\s*$') {
        throw (New-ODSNativeLlamaFailure ("ODS could not read the NVIDIA driver version, which the $build needs to be $($Pin.MinDriverVersion) or newer. " +
            'Update or reinstall the NVIDIA driver with the NVIDIA App or from nvidia.com, restart Windows and rerun setup. Nothing was downloaded or changed.') 'driver')
    }
    if (-not (Test-ODSNativeLlamaDriverVersion $DriverVersion $Pin.MinDriverVersion)) {
        throw (New-ODSNativeLlamaFailure ("The NVIDIA driver ($($DriverVersion.Trim())) is older than $($Pin.MinDriverVersion), which the $build needs. " +
            'Update it with the NVIDIA App or from nvidia.com, restart Windows and rerun setup. Do not install NVIDIA drivers inside Ubuntu. ' +
            'Nothing was downloaded or changed.') 'driver')
    }
}

function Get-ODSNativeLlamaCudaArchs([string]$Log) {
    # llama-server logs "system_info: ... | CUDA : ARCHS = 860,890,1200,1210 | ...".
    # As in ods-doctor.sh, the last such line counts; its comma-separated
    # names are read up to the first other text. An empty list means the
    # line is missing.
    $line = @(([string]$Log -split "`r?`n") | Where-Object { $_.Contains('CUDA : ARCHS =') } | Select-Object -Last 1)
    if (-not $line.Count) { return @() }
    $value = [regex]::Match($line[0], 'CUDA : ARCHS = ([0-9A-Za-z]+(?:\s*,\s*[0-9A-Za-z]+)*)').Groups[1].Value
    return @($value.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Get-ODSNativeLlamaRequiredCudaArchs([string]$ComputeCap, $Map) {
    # The pin's names for a compute capability (12.1: 1210, 121, 121a, the
    # values ods-doctor.sh accepts), else the exact names derived from it
    # (8.6: 860, 86, 86a). A device the pin does not list is checked, not
    # refused by a list.
    $names = $null
    if ($Map -is [Collections.IDictionary]) { if ($Map.Contains($ComputeCap)) { $names = $Map[$ComputeCap] } }
    elseif ($Map -and $Map.PSObject.Properties[$ComputeCap]) { $names = $Map.PSObject.Properties[$ComputeCap].Value }
    if ($null -ne $names -and @($names).Count) { return @($names | ForEach-Object { [string]$_ }) }
    $match = [regex]::Match([string]$ComputeCap, '^([0-9]{1,2})\.([0-9])$')
    if (-not $match.Success) { return @() }
    $major = [int]$match.Groups[1].Value
    $minor = [int]$match.Groups[2].Value
    return @([string]($major * 100 + $minor * 10), "$major$minor", "$major$($minor)a")
}

function Test-ODSNativeLlamaCudaArchsCover([string[]]$Archs, [string]$ComputeCap, $Map) {
    $accepted = @(Get-ODSNativeLlamaRequiredCudaArchs $ComputeCap $Map)
    return [bool](@($Archs | Where-Object { $_ -and $_ -cin $accepted }).Count)
}

function Get-ODSNativeLlamaCheckVerdict([string]$Content, [string[]]$Archs, [string]$ComputeCap, $Map) {
    # Pure: the CUDA targets first (a build without them is never used),
    # then the answer. Class '' means the check passed.
    $Archs = @($Archs | Where-Object { $_ })
    if ($ComputeCap -and -not $Archs.Count) {
        return [pscustomobject]@{ Class = 'build'; Reason = ("llama.cpp did not report which CUDA targets it was built for (no 'CUDA : ARCHS =' line in its log), " +
            "so ODS cannot confirm it supports this GPU (compute capability $ComputeCap). Update ODS and rerun setup.") }
    }
    if ($ComputeCap -and -not (Test-ODSNativeLlamaCudaArchsCover $Archs $ComputeCap $Map)) {
        return [pscustomobject]@{ Class = 'build'; Reason = ("llama.cpp has CUDA kernels for $(@($Archs) -join ','), not for this GPU (compute capability $ComputeCap), " +
            'so its answers would be unusable. ODS did not run a model with it. Update ODS and rerun setup to use the GPU on Windows.') }
    }
    if ([string]$Content -notmatch '^\s*4\b') {
        $shown = ([string]$Content -replace '[^\x20-\x7e]', '?')
        if ($shown.Length -gt 40) { $shown = $shown.Substring(0, 40) + '...' }
        return [pscustomobject]@{ Class = 'generation'; Reason = ("llama.cpp started on the GPU but answered `"$shown`" to a test question (expected 4). " +
            'That points to a build without kernels for this GPU or a driver problem. Update the NVIDIA driver with the NVIDIA App or from nvidia.com, restart Windows and rerun setup.') }
    }
    return [pscustomobject]@{ Class = ''; Reason = '' }
}

function Get-ODSNativeLlamaCheckExitFailure([int]$ExitCode, [string]$Architecture, $CodeIntegrity) {
    $class = Get-ODSNativeLlamaFailureClass ([pscustomobject]@{ Started = $true; TimedOut = $false; ExitCode = $ExitCode }) $CodeIntegrity
    $hint = Get-ODSNativeLlamaExitHint $ExitCode 'cuda' $Architecture
    return [pscustomobject]@{ Class = $class
        Reason = ("llama-server exited during the GPU check with code $ExitCode. $hint " + (Format-ODSNativeLlamaPolicyHint $CodeIntegrity)).Trim() }
}

function Start-ODSNativeLlamaCheckServer([string]$ExecutablePath, [string[]]$Arguments, $CodeIntegrity) {
    # Process, or Failure when Windows refused to start it.
    try {
        $child = Start-Process -FilePath $ExecutablePath -ArgumentList (ConvertTo-ODSNativeLlamaArgumentString $Arguments) `
            -WorkingDirectory (Split-Path -Parent $ExecutablePath) -WindowStyle Hidden -PassThru -ErrorAction Stop
    } catch {
        $cause = $_.Exception
        while ($cause.InnerException) { $cause = $cause.InnerException }
        $native = if ($cause -is [System.ComponentModel.Win32Exception]) { $cause.NativeErrorCode } else { 0 }
        $class = Get-ODSNativeLlamaFailureClass ([pscustomobject]@{ Started = $false; NativeError = $native }) $CodeIntegrity
        $reason = ("llama-server.exe could not start: $($cause.Message) " + (Get-ODSNativeLlamaStartHint $native) + ' ' +
            (Format-ODSNativeLlamaPolicyHint $CodeIntegrity)).Trim()
        return [pscustomobject]@{ Process = $null; Failure = [pscustomobject]@{ Class = $class; Reason = $reason } }
    }
    # Holding the handle keeps its exit code and stops a reused PID from
    # ever being the one this check stops.
    $null = $child.Handle
    return [pscustomobject]@{ Process = $child; Failure = $null }
}

function Wait-ODSNativeLlamaCheckReady([int]$Port, $Process, [int]$TimeoutSeconds, [string]$Architecture, $CodeIntegrity) {
    # $null once /health is 200, else the failure.
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) { return (Get-ODSNativeLlamaCheckExitFailure ([int]$Process.ExitCode) $Architecture $CodeIntegrity) }
        if ((Get-ODSNativeLlamaHealthState $Port) -eq 'ready') { return $null }
        Start-Sleep -Seconds 1
    }
    return [pscustomobject]@{ Class = 'timeout'
        Reason = "llama.cpp did not load its small test model on the GPU within $TimeoutSeconds seconds. Restart Windows and rerun setup." }
}

function Get-ODSNativeLlamaCheckAnswer([int]$Port, [string]$ApiKey, $Process, [string]$Architecture, $CodeIntegrity) {
    # The model's answer to the 2+2 question, or the failure.
    $request = @{ messages = @(@{ role = 'user'; content = $script:ODSNativeLlamaProbeQuestion }); temperature = 0; max_tokens = 8; stream = $false }
    $response = Invoke-ODSNativeLlamaHttp -Port $Port -Path '/v1/chat/completions' -ApiKey $ApiKey -Method POST `
        -JsonBody ($request | ConvertTo-Json -Depth 4 -Compress) -TimeoutMilliseconds 120000
    if ($response.StatusCode -ne 200) {
        if ($Process.HasExited) {
            return [pscustomobject]@{ Content = ''; Failure = (Get-ODSNativeLlamaCheckExitFailure ([int]$Process.ExitCode) $Architecture $CodeIntegrity) }
        }
        # llama.cpp errors are {"error":{"code":400,"message":"..."}}; any
        # other body is left out of the message.
        $detail = [regex]::Match([string]$response.Body, '"message"\s*:\s*"([^"\\]{1,200})"').Groups[1].Value
        $detail = ($detail -replace '[^\x20-\x7e]', '?')
        $suffix = if ($detail) { ": $detail" } else { '' }
        return [pscustomobject]@{ Content = ''; Failure = [pscustomobject]@{ Class = 'generation'
            Reason = "llama.cpp answered HTTP $($response.StatusCode) to a test question on the GPU$suffix. Restart Windows and rerun setup." } }
    }
    $answer = $response.Body | ConvertFrom-Json -ErrorAction Stop
    return [pscustomobject]@{ Content = [string]@($answer.choices)[0].message.content; Failure = $null }
}

function Stop-ODSNativeLlamaCheckServer($Process, [int]$Port) {
    # Only the process this check started, through its retained handle.
    if (-not $Process.HasExited) {
        try { $Process.Kill() } catch { if (-not $Process.HasExited) { throw } }
    }
    if (-not $Process.WaitForExit(60000)) { throw 'The llama.cpp GPU check did not exit within 60 seconds of being stopped.' }
    # Windows can take a moment to close an exited process's socket: 10 s.
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if (-not @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).Count) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "Port $Port is still in use after the llama.cpp GPU check stopped; close the program using it, then rerun setup."
}

function Invoke-ODSNativeLlamaGenerationProbe {
    <#
    .SYNOPSIS
        Qualify a CUDA llama-server.exe on this GPU: load a small model, ask
        what 2+2 is, and read the CUDA targets it reports.
    .DESCRIPTION
        A hidden server on 127.0.0.1 with a temporary key file, 4096 tokens of
        context, one slot, every layer on -Device and --fit off. It is stopped
        through its retained process handle, and its port must be free again.
        Passed needs the CUDA targets for -ComputeCap (when given) and an
        answer that starts with 4.
    .OUTPUTS
        Passed, Content, Archs, Class ('' when passed; start, policy, dll,
        exit, timeout, build or generation) and Reason (the cause and what to
        do, ASCII; the caller adds the route it takes instead).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ExecutablePath,
        [Parameter(Mandatory = $true)][string]$ModelPath,
        [Parameter(Mandatory = $true)][string]$Device,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [string]$ComputeCap = '',
        $ArchsByCompute = $null,
        [string[]]$ReasoningArguments = @('--reasoning', 'off'),
        [ValidateSet('x64', 'arm64', IgnoreCase = $false)][string]$Architecture = 'arm64',
        $CodeIntegrity = $null,
        [int]$TimeoutSeconds = 180
    )
    if ($Device -cnotmatch '^CUDA[0-9]{1,2}$') { throw "The llama.cpp GPU check needs a CUDA device, not '$Device'." }
    if ($Port -lt 1 -or $Port -gt 65535) { throw 'The llama.cpp GPU check needs a port from 1 to 65535.' }
    if (-not (Test-Path -LiteralPath $ModelPath -PathType Leaf)) { throw "The llama.cpp GPU check model is missing: $ModelPath" }
    if (@(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).Count) {
        throw "Port $Port is already in use, so the llama.cpp GPU check did not start."
    }
    if ($null -eq $CodeIntegrity) { $CodeIntegrity = Get-ODSNativeLlamaCodeIntegrityState }
    $logDirectory = Split-Path -Parent $LogPath
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    if (Test-Path -LiteralPath $LogPath) { Remove-Item -LiteralPath $LogPath -Force }
    $key = New-ODSNativeLlamaApiKey
    $keyPath = Join-Path $logDirectory ('check-api-key-' + [guid]::NewGuid().ToString('N'))
    Write-ODSNativeLlamaApiKeyFile $keyPath $key
    $arguments = @('--model', (ConvertTo-ODSNativeLlamaArgumentPath $ModelPath), '--host', '127.0.0.1', '--port', [string]$Port,
        '--ctx-size', [string]$script:ODSNativeLlamaProbeContext, '--parallel', '1', '--n-gpu-layers', 'all', '--device', $Device,
        '--fit', 'off', '--no-webui', '--api-key-file', (ConvertTo-ODSNativeLlamaArgumentPath $keyPath),
        '--log-file', (ConvertTo-ODSNativeLlamaArgumentPath $LogPath)) + @($ReasoningArguments | ForEach-Object { [string]$_ })
    $failure = $null
    $content = ''
    $child = $null
    try {
        $start = Start-ODSNativeLlamaCheckServer $ExecutablePath $arguments $CodeIntegrity
        $child = $start.Process
        $failure = $start.Failure
        if (-not $failure) { $failure = Wait-ODSNativeLlamaCheckReady $Port $child $TimeoutSeconds $Architecture $CodeIntegrity }
        if (-not $failure) {
            $answer = Get-ODSNativeLlamaCheckAnswer $Port $key $child $Architecture $CodeIntegrity
            $content = $answer.Content
            $failure = $answer.Failure
        }
    } finally {
        try {
            if ($child) { Stop-ODSNativeLlamaCheckServer $child $Port }
        } finally {
            if ($child) { $child.Dispose() }
            if (Test-Path -LiteralPath $keyPath) { Remove-Item -LiteralPath $keyPath -Force }
        }
    }
    $log = if (Test-Path -LiteralPath $LogPath -PathType Leaf) { [IO.File]::ReadAllText($LogPath) } else { '' }
    $archs = @(Get-ODSNativeLlamaCudaArchs $log)
    if (-not $failure) { $failure = Get-ODSNativeLlamaCheckVerdict $content $archs $ComputeCap $ArchsByCompute }
    return [pscustomobject]@{ Passed = (-not $failure.Class); Content = $content; Archs = $archs; Class = $failure.Class; Reason = $failure.Reason }
}

# --- API key --------------------------------------------------------------------

function New-ODSNativeLlamaApiKey {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return (-join ($bytes | ForEach-Object { $_.ToString('x2') }))
}

function Write-ODSNativeLlamaApiKeyFile([string]$Path, [string]$Key) {
    # llama.cpp reads --api-key-file with std::getline: a BOM or CR would become
    # part of the key, so the file holds exactly 64 hex characters.
    if ($Key -cnotmatch '^[0-9a-f]{64}$') { throw 'The llama-server API key must be 64 lowercase hex characters.' }
    Write-ODSPrivateFileBytes -Path $Path -Bytes ([Text.Encoding]::ASCII.GetBytes($Key))
}

function Read-ODSNativeLlamaApiKey([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'The llama-server API key file is missing; rerun setup.' }
    $bytes = [IO.File]::ReadAllBytes($Path)
    $text = [Text.Encoding]::ASCII.GetString($bytes)
    if ($bytes.Length -ne 64 -or $text -cnotmatch '^[0-9a-f]{64}$') {
        throw 'The llama-server API key file is malformed (it must hold 64 hex characters, no BOM or newline); rerun setup.'
    }
    return $text
}

# --- Launch arguments -----------------------------------------------------------

function Get-ODSNativeLlamaShortPath([string]$Path) {
    # 8.3 name through the Windows Script Host COM object; $null when 8.3 names
    # are disabled or the path does not exist.
    try {
        $fso = New-Object -ComObject Scripting.FileSystemObject
        if (Test-Path -LiteralPath $Path -PathType Leaf) { return [string]$fso.GetFile($Path).ShortPath }
        if (Test-Path -LiteralPath $Path -PathType Container) { return [string]$fso.GetFolder($Path).ShortPath }
    } catch { }
    return $null
}

function ConvertTo-ODSNativeLlamaArgumentPath([string]$Path) {
    <#
    .SYNOPSIS
        A path llama-server.exe can open. It reads argv in the ANSI code page
        but opens files as UTF-8 (ggml_fopen), so non-ASCII paths fail; their
        8.3 short form is used instead, or setup stops with an explanation.
    #>
    if ($Path -cmatch '^[\x20-\x7e]+$') { return $Path }
    $short = Get-ODSNativeLlamaShortPath $Path
    if (-not $short) {
        $parent = Split-Path -Parent $Path
        $leaf = Split-Path -Leaf $Path
        $shortParent = Get-ODSNativeLlamaShortPath $parent
        if ($shortParent -and $leaf -cmatch '^[\x20-\x7e]+$') { $short = Join-Path $shortParent $leaf }
    }
    if ($short -and $short -cmatch '^[\x20-\x7e]+$') { return $short }
    throw "llama.cpp on Windows cannot open '$Path': the path has non-ASCII characters and no 8.3 short name. Use an ASCII Windows user or folder name, or enable 8.3 names on that drive."
}

function ConvertTo-ODSNativeLlamaArgumentString([string[]]$Values) {
    # Start-Process joins ArgumentList without quoting. Quote every value with
    # the Windows CRT rules (backslashes before quotes and at the end doubled).
    return (@($Values | ForEach-Object {
        '"' + [regex]::Replace([regex]::Replace([string]$_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
    }) -join ' ')
}

function Assert-ODSNativeLlamaPlan($Plan) {
    if (-not [IO.Path]::IsPathRooted([string]$Plan.ExecutablePath) -or
        [IO.Path]::GetFileName([string]$Plan.ExecutablePath) -ine 'llama-server.exe' -or
        -not [IO.Path]::IsPathRooted([string]$Plan.ModelsDir) -or
        [string]$Plan.GgufFile -cnotmatch '^[\x20-\x7e]{1,240}$' -or [string]$Plan.GgufFile -notmatch '\.gguf$' -or
        [string]$Plan.GgufFile -match '[\\/:*?"<>|]' -or ([string]$Plan.GgufFile).StartsWith('.') -or
        [string]$Plan.GgufFile -ne ([string]$Plan.GgufFile).Trim() -or
        ($Plan.ContextSize -isnot [int] -and $Plan.ContextSize -isnot [long]) -or
        $Plan.ContextSize -lt 1 -or $Plan.ContextSize -gt 10000000 -or
        ($Plan.Port -isnot [int] -and $Plan.Port -isnot [long]) -or $Plan.Port -lt 1 -or $Plan.Port -gt 65535) {
        throw 'The saved llama-server plan is invalid (llama.cpp on Windows needs an ASCII .gguf file name).'
    }
    # A vision model's projector (mmproj) is optional and sits beside its
    # weights in the same store, under the same ASCII basename rule.
    if ($null -ne $Plan.MmprojFile) {
        $projector = [string]$Plan.MmprojFile
        if ($projector -cnotmatch '^[\x20-\x7e]{1,240}$' -or $projector -notmatch '\.gguf$' -or
            $projector -match '[\\/:*?"<>|]' -or $projector.StartsWith('.') -or $projector -ne $projector.Trim() -or
            $projector -ieq [string]$Plan.GgufFile) {
            throw 'The saved llama-server plan names an invalid vision projector (an ASCII .gguf file beside the model).'
        }
    }
}

function Get-ODSNativeLlamaOptionsBackend($Options) {
    # Options written before CUDA existed name no backend: Vulkan.
    if ($null -eq $Options.Backend) { return 'vulkan' }
    return [string]$Options.Backend
}

function Get-ODSNativeLlamaOptionsArchitecture($Options) {
    if ($null -eq $Options.Architecture) { return 'x64' }
    return [string]$Options.Architecture
}

function Assert-ODSNativeLlamaOptions($Options) {
    # Device 'none' is the explicit CPU route (no usable Vulkan device), and
    # only with zero GPU layers; never a silent fallback. NGpuLayers takes
    # every N_GPU_LAYERS value .env.schema.json allows: auto, all or a layer
    # count, which llama.cpp range-checks itself. Backend (vulkan or cuda)
    # and Architecture (x64 or arm64) are optional; the device name follows
    # the backend, and only Vulkan has the CPU route.
    $backend = Get-ODSNativeLlamaOptionsBackend $Options
    $architecture = Get-ODSNativeLlamaOptionsArchitecture $Options
    $devicePattern = if ($backend -ceq 'cuda') { '^CUDA[0-9]{1,2}$' } else { '^Vulkan[0-9]{1,2}$' }
    $cpuRoute = $backend -ceq 'vulkan' -and [string]$Options.Device -ceq 'none'
    if ($Options.schemaVersion -ne 1 -or $backend -cnotin @('vulkan', 'cuda') -or $architecture -cnotin @('x64', 'arm64') -or
        ([string]$Options.Device -cnotmatch $devicePattern -and -not $cpuRoute) -or
        ($cpuRoute -and [string]$Options.NGpuLayers -cne '0') -or
        [string]$Options.NGpuLayers -cnotmatch '^(auto|all|[0-9]{1,10})$' -or
        -not [IO.Path]::IsPathRooted([string]$Options.ApiKeyPath) -or
        -not [IO.Path]::IsPathRooted([string]$Options.LogPath) -or
        [string]$Options.ReleaseTag -cnotmatch '^b[0-9]{3,6}$' -or
        [string]$Options.ZipSha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'The saved llama-server launch options are invalid; rerun setup.'
    }
    $reasoning = @($Options.ReasoningArguments)
    if ($reasoning.Count -and -not (
        ($reasoning.Count -eq 2 -and $reasoning[0] -ceq '--reasoning' -and $reasoning[1] -cin @('off', 'on', 'auto')) -or
        ($reasoning.Count -eq 2 -and $reasoning[0] -ceq '--reasoning-format' -and $reasoning[1] -cmatch '^[a-z-]{1,24}$') -or
        ($reasoning.Count -eq 4 -and $reasoning[0] -ceq '--reasoning-format' -and $reasoning[1] -cmatch '^[a-z-]{1,24}$' -and
            $reasoning[2] -ceq '--reasoning-budget' -and $reasoning[3] -ceq '0'))) {
        throw 'The saved llama-server reasoning options are invalid; rerun setup.'
    }
    $extra = @($Options.ExtraArguments)
    $allowed = @('--flash-attn', '--cache-type-k', '--cache-type-v', '--n-cpu-moe', '--ctx-checkpoints',
        '--cache-ram', '--checkpoint-every-n-tokens', '--spec-type', '--spec-draft-n-max',
        '--spec-draft-type-k', '--spec-draft-type-v')
    for ($index = 0; $index -lt $extra.Count; $index++) {
        $flag = [string]$extra[$index]
        if ($flag -ceq '--no-cache-prompt') { continue }
        if ($flag -cnotin $allowed -or $index + 1 -ge $extra.Count -or [string]$extra[$index + 1] -cnotmatch '^-?[A-Za-z0-9._][A-Za-z0-9._-]{0,31}$') {
            throw "The saved llama-server tuning option '$flag' is not allowed; rerun setup."
        }
        $index++
    }
}

function New-ODSNativeLlamaLaunchArguments($Plan, $Options) {
    Assert-ODSNativeLlamaPlan $Plan
    Assert-ODSNativeLlamaOptions $Options
    $model = ConvertTo-ODSNativeLlamaArgumentPath (Join-Path $Plan.ModelsDir $Plan.GgufFile)
    $arguments = @(
        '--model', $model,
        '--alias', [string]$Plan.GgufFile,
        # Loopback only: an authenticated model API still never leaves this PC.
        '--host', '127.0.0.1',
        '--port', [string]$Plan.Port,
        '--ctx-size', [string]$Plan.ContextSize,
        # One slot makes /props n_ctx the whole planned context.
        '--parallel', '1',
        '--n-gpu-layers', [string]$Options.NGpuLayers,
        # Naming the device fails closed when Vulkan is unusable, instead of
        # llama.cpp's silent CPU fallback.
        '--device', [string]$Options.Device,
        '--metrics',
        '--no-webui',
        '--api-key-file', (ConvertTo-ODSNativeLlamaArgumentPath $Options.ApiKeyPath),
        '--log-file', (ConvertTo-ODSNativeLlamaArgumentPath $Options.LogPath)
    )
    if ((Get-ODSNativeLlamaOptionsBackend $Options) -ceq 'cuda') {
        # llama.cpp defaults to --fit on, which shrinks unset settings to fit
        # device memory. Under WDDM that memory is this process's budget, so
        # fit could move layers or experts to the CPU; the plan sized them.
        $arguments += @('--fit', 'off')
    }
    if ($null -ne $Plan.MmprojFile) {
        $arguments += @('--mmproj', (ConvertTo-ODSNativeLlamaArgumentPath (Join-Path $Plan.ModelsDir $Plan.MmprojFile)))
    }
    $arguments += @($Options.ReasoningArguments | ForEach-Object { [string]$_ })
    $arguments += @($Options.ExtraArguments | ForEach-Object { [string]$_ })
    return $arguments
}

# --- HTTP proof -----------------------------------------------------------------

function Invoke-ODSNativeLlamaHttp {
    <#
    .SYNOPSIS
        One bounded GET (or POST of a small JSON body) against the loopback
        llama-server. Returns StatusCode (0 when nothing answered) and Body.
        The key is sent only as a header.
    #>
    param([int]$Port, [string]$Path, [string]$ApiKey = '', [int]$TimeoutMilliseconds = 5000,
        [ValidateSet('GET', 'POST', IgnoreCase = $false)][string]$Method = 'GET', [string]$JsonBody = '')
    $payload = $null
    if ($Method -ceq 'POST') {
        $payload = [Text.UTF8Encoding]::new($false).GetBytes($JsonBody)
        if (-not $payload.Length -or $payload.Length -gt 65536) { throw 'A llama-server POST needs a JSON body of at most 64 KiB.' }
    } elseif ($JsonBody) { throw 'Only a llama-server POST carries a body.' }
    $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$Port$Path")
    $request.Method = $Method
    $request.Timeout = $TimeoutMilliseconds
    $request.ReadWriteTimeout = $TimeoutMilliseconds
    $request.Proxy = $null
    if ($ApiKey) { $request.Headers['Authorization'] = 'Bearer ' + $ApiKey }
    $response = $null
    try {
        if ($payload) {
            $request.ContentType = 'application/json'
            $request.ContentLength = $payload.Length
            $requestStream = $request.GetRequestStream()
            try { $requestStream.Write($payload, 0, $payload.Length) } finally { $requestStream.Dispose() }
        }
        $response = $request.GetResponse()
    } catch {
        $cause = $_.Exception
        while ($cause -and $cause -isnot [Net.WebException]) { $cause = $cause.InnerException }
        if (-not $cause) { throw }
        if (-not $cause.Response) { return [pscustomobject]@{ StatusCode = 0; Body = ''; Error = [string]$cause.Status } }
        $response = $cause.Response
    }
    try {
        $stream = $response.GetResponseStream()
        $buffer = New-Object byte[] 65536
        $content = [IO.MemoryStream]::new()
        try {
            while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $content.Write($buffer, 0, $read)
                if ($content.Length -gt 4194304) { throw 'llama-server returned an oversized response.' }
            }
            $body = [Text.UTF8Encoding]::new($false).GetString($content.ToArray())
        } finally { $content.Dispose(); $stream.Dispose() }
        return [pscustomobject]@{ StatusCode = [int]$response.StatusCode; Body = $body; Error = '' }
    } finally {
        $response.Close()
    }
}

function Get-ODSNativeLlamaHealthState([int]$Port) {
    # /health is public: 200 when the model is loaded, 503 while loading.
    $health = Invoke-ODSNativeLlamaHttp -Port $Port -Path '/health' -TimeoutMilliseconds 3000
    if ($health.StatusCode -eq 200) { return 'ready' }
    if ($health.StatusCode -eq 503) { return 'loading' }
    if ($health.StatusCode -eq 0) { return 'unreachable' }
    return "http-$($health.StatusCode)"
}

function Get-ODSNativeLlamaModelProof {
    <#
    .SYNOPSIS
        Prove the served model and context: /v1/models id equals the GGUF and
        /props reports its path and one slot of the planned context.
    .OUTPUTS
        ModelId, RuntimeContext, ContextVerified and ContextLength. Per the
        controller contract ContextLength is the requested size when
        0 <= n_ctx - requested < 256 (llama.cpp alignment), otherwise n_ctx.
    #>
    param([int]$Port, [string]$GgufFile, [string[]]$ModelPaths, [long]$ContextSize, [string]$ApiKey, [switch]$Vision)
    $models = Invoke-ODSNativeLlamaHttp -Port $Port -Path '/v1/models'
    if ($models.StatusCode -ne 200) { throw "llama-server /v1/models answered HTTP $($models.StatusCode)." }
    $catalog = $models.Body | ConvertFrom-Json -ErrorAction Stop
    $entries = @($catalog.data)
    if ($entries.Count -ne 1 -or [string]$entries[0].id -cne $GgufFile) {
        $served = (@($entries | ForEach-Object { [string]$_.id }) -join ', ')
        throw "llama-server serves '$served' instead of the planned model '$GgufFile'."
    }
    $trainingContext = $null
    if ($entries[0].meta -and $entries[0].meta.PSObject.Properties['n_ctx_train']) { $trainingContext = $entries[0].meta.n_ctx_train }
    $props = Invoke-ODSNativeLlamaHttp -Port $Port -Path '/props' -ApiKey $ApiKey
    if ($props.StatusCode -eq 401) { throw 'llama-server rejected the ODS API key on /props.' }
    if ($props.StatusCode -ne 200) { throw "llama-server /props answered HTTP $($props.StatusCode)." }
    $settings = $props.Body | ConvertFrom-Json -ErrorAction Stop
    $servedPath = [string]$settings.model_path
    $pathMatches = @($ModelPaths | Where-Object { $_ -and $servedPath.Equals([string]$_, [StringComparison]::OrdinalIgnoreCase) })
    if (-not $pathMatches.Count) { throw "llama-server loaded '$servedPath', not the planned model file." }
    if ($settings.total_slots -ne 1) { throw 'llama-server must run exactly one slot for the context proof.' }
    if ($Vision -and -not ($settings.modalities -and $settings.modalities.vision -eq $true)) {
        throw 'llama-server did not load the planned vision projector.'
    }
    $runtimeContext = $settings.default_generation_settings.n_ctx
    if ($runtimeContext -isnot [int] -and $runtimeContext -isnot [long]) { throw 'llama-server /props reported no integer n_ctx.' }
    $difference = [long]$runtimeContext - $ContextSize
    $verified = $difference -ge 0 -and $difference -lt $script:ODSNativeLlamaContextAlignment
    $message = ''
    if ($difference -lt 0) {
        $message = "llama.cpp loaded $GgufFile with $runtimeContext tokens of context, less than the planned $ContextSize."
        if ($trainingContext) { $message += " The model's training context is $trainingContext; choose a context up to that size." }
    } elseif (-not $verified) {
        $message = "llama.cpp loaded $GgufFile with $runtimeContext tokens of context instead of the planned $ContextSize."
    }
    return [pscustomobject]@{
        ModelId = $GgufFile
        RuntimeContext = [long]$runtimeContext
        ContextVerified = $verified
        ContextLength = $(if ($verified) { [long]$ContextSize } else { [long]$runtimeContext })
        TrainingContext = $trainingContext
        Message = $message
    }
}

# --- Process ownership ----------------------------------------------------------

function Get-ODSPortalOwnedProcessTree([object[]]$Roots, [object[]]$Nodes, [string]$ServerPath = '') {
    # With -ServerPath, a process older than its recorded parent is skipped
    # unless it runs from the server's folder: Windows reused that parent's
    # PID, so it was never our child. A browser window opened before the
    # runtime started can name the runtime's console host this way. Without
    # it (the non-Portal routes) any older child still refuses.
    $serverFolder = if ($ServerPath) { [IO.Path]::GetDirectoryName($ServerPath) } else { '' }
    $tree = [Collections.Generic.List[object]]::new()
    foreach ($root in $Roots) { $tree.Add($root) }
    for ($index = 0; $index -lt $tree.Count; $index++) {
        $parent = $tree[$index]
        if ($parent.CreationDate -isnot [datetime] -or -not $parent.ExecutablePath) {
            throw "Cannot read the owned runtime process identity (PID $($parent.ProcessId)); no process was stopped."
        }
        foreach ($node in @($Nodes | Where-Object { $_.ParentProcessId -eq $parent.ProcessId })) {
            if ($node.CreationDate -isnot [datetime]) {
                throw 'The runtime process ancestry is stale or ambiguous; no process was stopped.'
            }
            if ($node.CreationDate.ToUniversalTime() -lt $parent.CreationDate.ToUniversalTime()) {
                $nodeFolder = if ($node.ExecutablePath) { [IO.Path]::GetDirectoryName([string]$node.ExecutablePath) } else { '' }
                if (-not $serverFolder -or $nodeFolder -ieq $serverFolder) {
                    throw 'The runtime process ancestry is stale or ambiguous; no process was stopped.'
                }
                continue
            }
            # A retained process handle supplies the original root's exit time.
            # Children born after that exit belong to a reused PID, not to us.
            if ($parent.ExitedAt -and $node.CreationDate.ToUniversalTime() -gt $parent.ExitedAt.ToUniversalTime()) { continue }
            $known = @($tree | Where-Object { $_.ProcessId -eq $node.ProcessId })
            if ($known.Count) {
                if ($known.Count -ne 1 -or $known[0].CreationDate.ToUniversalTime() -ne $node.CreationDate.ToUniversalTime() -or
                    $known[0].ExecutablePath -ine $node.ExecutablePath -or
                    $node.ProcessId -in @($tree | Select-Object -First ($index + 1) | ForEach-Object { $_.ProcessId })) {
                    throw 'The runtime process ancestry is stale or ambiguous; no process was stopped.'
                }
                continue
            }
            $tree.Add($node)
        }
    }
    $handles = [Collections.Generic.List[object]]::new()
    try {
        foreach ($node in $tree) {
            if ($node.ExitedAt) { continue }
            $process = Get-Process -Id $node.ProcessId -ErrorAction Stop
            $handles.Add($process)
            $null = $process.Handle
            if ($process.Path -ine $node.ExecutablePath -or
                [math]::Abs(($process.StartTime.ToUniversalTime() - $node.CreationDate.ToUniversalTime()).TotalMilliseconds) -ge 1) {
                throw 'A runtime process changed during ownership verification; no process was stopped.'
            }
        }
        return [pscustomobject]@{ Nodes = $tree; Handles = $handles }
    } catch {
        foreach ($process in $handles) { $process.Dispose() }
        throw
    }
}

function Stop-ODSPortalOwnedProcesses($Handles) {
    # Parents first prevent a supervisor from launching replacement children.
    foreach ($process in $Handles) {
        if (-not $process.HasExited) {
            try { $process.Kill() } catch {
                $cause = $_.Exception
                while ($cause -is [System.Management.Automation.RuntimeException] -and $cause.InnerException) {
                    $cause = $cause.InnerException
                }
                if ($cause -isnot [InvalidOperationException] -and
                    $cause -isnot [System.ComponentModel.Win32Exception]) { throw }
                # Task Scheduler may have exited this held process after HasExited
                # but before Kill. Accept that race only if the same handle proves exit.
                if (-not $process.WaitForExit(1000)) {
                    throw "Could not stop owned runtime process $($process.Id): $($cause.Message)"
                }
            }
        }
    }
    # Kill() returns at once, but a llama-server holding a large model in GPU
    # or shared memory can take well over five seconds to exit while the
    # driver releases it (Strixy: the 22 GB 35B on the 8060S failed one model
    # switch in three). The process is already stopping; wait for its exit.
    foreach ($process in $Handles) {
        if (-not $process.WaitForExit(60000)) { throw 'An owned runtime process did not exit within 60 seconds of being stopped.' }
    }
}

function Write-ODSPortalProcessOwnership([string]$Path, $Plan, $Nodes) {
    $records = @($Nodes | ForEach-Object {
        @{ ProcessId = $_.ProcessId; ExecutablePath = $_.ExecutablePath
            StartedAt = $_.CreationDate.ToUniversalTime().ToString('o')
            ExitedAt = if ($_.ExitedAt) { $_.ExitedAt.ToUniversalTime().ToString('o') } else { $null } }
    })
    $ownership = @{ ExecutablePath = $Plan.ExecutablePath; Port = $Plan.Port; Processes = $records }
    Write-ODSPrivateEnvFile -Path $Path -Content ($ownership | ConvertTo-Json -Depth 4 -Compress)
}

function Assert-ODSNativeLlamaListener([int]$Port, [int]$ProcessId, [string]$ExecutablePath) {
    # llama-server is its own listener: exactly one loopback socket, owned by
    # the launched process itself.
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1' -or [int]$listeners[0].OwningProcess -ne $ProcessId) {
        throw "The listener on port $Port is not the launched loopback llama-server."
    }
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    if (-not ([string]$process.Path).Equals($ExecutablePath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The listener on port $Port is not the pinned llama-server.exe."
    }
}

function Test-ODSNativeLlamaWanted([string]$Path) {
    # Plans without intent predate it. Explicit stops always publish it first.
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $true }
    $intent = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($intent.State -cnotin @('running', 'stopped')) { throw 'The saved runtime intent is invalid.' }
    return $intent.State -ceq 'running'
}

function Read-ODSNativeLlamaJson([string]$Path) {
    return ([IO.File]::ReadAllText($Path, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json -ErrorAction Stop)
}

# --- Memory at model start (CUDA) -----------------------------------------------
# Windows charges every GPU allocation against its commit limit (visible RAM
# plus the page file), even inside the GPU's firmware carve-out. A model the
# commit limit cannot hold fails mid-load or makes other programs fail, so a
# CUDA start is checked first against the figures setup or the model
# controller saved in memory-plan.json. Nothing here ever changes a setting.

function Get-ODSNativeOtherCommitGiB([double]$CommittedGiB, [double]$RunningModelGiB, $WslNowGiB) {
    # What Windows commits besides the ODS model and WSL: never less than the
    # reserve, and the reserve when WSL's own commit cannot be read.
    if ($null -eq $WslNowGiB) { return [double]$script:ODSNativeWindowsReserveGiB }
    return [math]::Max([double]$script:ODSNativeWindowsReserveGiB, $CommittedGiB - $RunningModelGiB - [double]$WslNowGiB)
}

function Get-ODSNativeCommitFit {
    <#
    .SYNOPSIS
        The commit rule (pure), shared by the RTX Spark memory planner, this
        launcher and the model controller so they cannot disagree.
    .DESCRIPTION
        CapacityGiB is what Windows can lend the model while WSL uses its whole
        memory limit: CommitLimitGiB - OtherCommitGiB - WslGiB - 2 GiB. WSL is
        planned at its limit, not its current use, because pages it touched
        stay committed on Windows until they are reclaimed.
    #>
    param([double]$CommitNeedGiB, [double]$CommitLimitGiB, [double]$OtherCommitGiB, [double]$WslGiB)
    $capacity = $CommitLimitGiB - $OtherCommitGiB - $WslGiB - $script:ODSNativeCommitMarginGiB
    return [pscustomobject]@{ CapacityGiB = [math]::Round($capacity, 2); Fits = ($CommitNeedGiB -le $capacity + 1e-9) }
}

function Get-ODSNativeCommitState {
    <#
    .SYNOPSIS
        Visible RAM, commit limit and commit charge in GiB, read unelevated
        from Win32_OperatingSystem (the figures Task Manager shows), or $null
        when WMI cannot answer.
    #>
    try { $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop }
    catch [Microsoft.Management.Infrastructure.CimException] { return $null }
    # Win32_OperatingSystem reports kilobytes.
    $limit = [double]$os.TotalVirtualMemorySize / 1MB
    return [pscustomobject]@{
        VisibleGiB = [math]::Round([double]$os.TotalVisibleMemorySize / 1MB, 2)
        CommitLimitGiB = [math]::Round($limit, 2)
        CommittedGiB = [math]::Round($limit - [double]$os.FreeVirtualMemory / 1MB, 2)
    }
}

function ConvertFrom-ODSNativeGpuMemoryCsv([string[]]$Lines, [string]$GpuName = '') {
    # nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version
    # --format=csv,noheader,nounits. The GPU named, else the only row with a
    # memory size (the NPU has none), else $null.
    $rows = @()
    foreach ($line in @($Lines)) {
        $fields = @(([string]$line).Split(','))
        if ($fields.Count -lt 4) { continue }
        $tail = @($fields[($fields.Count - 3)..($fields.Count - 1)] | ForEach-Object { $_.Trim() })
        $name = (($fields[0..($fields.Count - 4)]) -join ',').Trim()
        if ($tail[0] -notmatch '^[0-9]+$' -or $tail[1] -notmatch '^[0-9]+$') { continue }
        $rows += [pscustomobject]@{ Name = $name; TotalMiB = [long]$tail[0]; UsedMiB = [long]$tail[1]; DriverVersion = $tail[2] }
    }
    if ($GpuName) {
        $named = @($rows | Where-Object { $_.Name -ieq $GpuName })
        if ($named.Count -eq 1) { return $named[0] }
    }
    if ($rows.Count -eq 1) { return $rows[0] }
    return $null
}

function Get-ODSNativeGpuMemoryState([string]$GpuName = '') {
    # The NVIDIA GPU's memory size and use from nvidia-smi, or $null when
    # nvidia-smi is missing or fails. Never throws.
    $smi = Get-Command nvidia-smi.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $smi) { return $null }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& $smi.Source '--query-gpu=name,memory.total,memory.used,driver_version' '--format=csv,noheader,nounits' 2>$null)
        $code = $LASTEXITCODE
    } catch [System.Management.Automation.ApplicationFailedException] {
        return $null
    } finally { $ErrorActionPreference = $previousPreference }
    if ($code -ne 0) { return $null }
    return (ConvertFrom-ODSNativeGpuMemoryCsv ([string[]]$lines) $GpuName)
}

function Format-ODSNativeGiB([double]$Value) {
    return ([math]::Round([math]::Max($Value, 0), 1)).ToString('0.0', [Globalization.CultureInfo]::InvariantCulture)
}

function Get-ODSNativeModelStartRefusal {
    <#
    .SYNOPSIS
        Why a CUDA model must not start now (pure): '' when it may, otherwise
        Code (memory_refused or gpu_busy), Kind (structural: this page file
        can never hold it; transient: other programs hold the memory now) and
        the owner-facing Text, which names the cause and the way out.
    #>
    param($MemoryPlan, $Commit, $Gpu = $null)
    $need = [double]$MemoryPlan.CommitNeedGiB
    $device = [double]$MemoryPlan.DeviceNeedGiB
    $limit = [double]$Commit.CommitLimitGiB
    $fit = Get-ODSNativeCommitFit -CommitNeedGiB $need -CommitLimitGiB $limit `
        -OtherCommitGiB $script:ODSNativeWindowsReserveGiB -WslGiB ([double]$MemoryPlan.WslGiB)
    if (-not $fit.Fits) {
        return [pscustomobject]@{ Code = 'memory_refused'; Kind = 'structural'
            Text = ("The model was not started: it needs about $(Format-ODSNativeGiB $need) GiB, and with this page file Windows can never " +
                "lend it more than about $(Format-ODSNativeGiB $fit.CapacityGiB) GiB (memory limit $(Format-ODSNativeGiB $limit) GiB). " +
                'Rerun install.ps1 and answer yes to the page-file offer, or choose a smaller model on the Models page. ' +
                "Start anyway is possible but may make other programs fail. $script:ODSNativeHelpLine") }
    }
    $headroom = $limit - [double]$Commit.CommittedGiB
    if ($headroom -lt $need) {
        return [pscustomobject]@{ Code = 'memory_refused'; Kind = 'transient'
            Text = ("The model was not started: Windows can lend it only $(Format-ODSNativeGiB $headroom) GiB right now (memory limit " +
                "$(Format-ODSNativeGiB $limit) GiB, $(Format-ODSNativeGiB $Commit.CommittedGiB) GiB in use) and it needs about " +
                "$(Format-ODSNativeGiB $need) GiB. Close large programs, then press Start anyway on the Models page (if memory really " +
                "runs out, other programs can fail). $script:ODSNativeHelpLine") }
    }
    if ($Gpu) {
        $freeGiB = ([double]$Gpu.TotalMiB - [double]$Gpu.UsedMiB) / 1024
        if ($freeGiB -lt $device) {
            $name = ([string]$Gpu.Name -replace '[^\x20-\x7e]', '?')
            return [pscustomobject]@{ Code = 'gpu_busy'; Kind = 'transient'
                Text = ("The model was not started: another program is using $(Format-ODSNativeGiB ([double]$Gpu.UsedMiB / 1024)) GiB of " +
                    "$name ($(Format-ODSNativeGiB $freeGiB) GiB free; the model needs about $(Format-ODSNativeGiB $device) GiB). " +
                    "Close the other AI app or model server, then press Start anyway on the Models page. $script:ODSNativeHelpLine") }
        }
    }
    return ''
}

function Test-ODSNativeMemoryNumber($Value) {
    # ConvertFrom-Json gives Int32/Int64 on both shells, Decimal on 5.1 and
    # Double on 7 for fractions.
    return (($Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal]) -and
        [double]$Value -ge 0 -and [double]$Value -le 4096)
}

function Test-ODSNativeMemoryPlanRecord($Record) {
    # memory-plan.json: { GgufFile; ContextSize; DeviceNeedGiB; CommitNeedGiB;
    # WslGiB; Source = catalog | files }.
    if ($null -eq $Record -or $Record -is [array]) { return $false }
    foreach ($name in @('DeviceNeedGiB', 'CommitNeedGiB', 'WslGiB')) {
        if (-not (Test-ODSNativeMemoryNumber $Record.$name)) { return $false }
    }
    return ($Record.GgufFile -is [string] -and $Record.GgufFile -cmatch '^[\x20-\x7e]{1,240}$' -and
        ($Record.ContextSize -is [int] -or $Record.ContextSize -is [long]) -and $Record.ContextSize -ge 1 -and
        [string]$Record.Source -cin @('catalog', 'files'))
}

function Read-ODSNativeLlamaJsonOrNull([string]$Path) {
    # $null when the file is not valid UTF-8 JSON: ArgumentException, which
    # covers DecoderFallbackException and arrives wrapped by the .NET call.
    try { return (Read-ODSNativeLlamaJson $Path) } catch {
        $cause = $_.Exception
        while ($cause -and $cause -isnot [ArgumentException]) { $cause = $cause.InnerException }
        if (-not $cause) { throw }
        return $null
    }
}

function Read-ODSNativeMemoryPlan([string]$Path, $Plan) {
    <#
    .SYNOPSIS
        The memory plan saved for exactly this model and context, or Plan
        $null with the Reason the check is skipped (missing, unreadable, or
        written for another plan).
    #>
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return [pscustomobject]@{ Plan = $null; Reason = 'no memory-plan.json' } }
    $record = Read-ODSNativeLlamaJsonOrNull $Path
    if ($null -eq $record) { return [pscustomobject]@{ Plan = $null; Reason = 'memory-plan.json is not valid UTF-8 JSON' } }
    if (-not (Test-ODSNativeMemoryPlanRecord $record)) { return [pscustomobject]@{ Plan = $null; Reason = 'memory-plan.json is invalid' } }
    if ([string]$record.GgufFile -cne [string]$Plan.GgufFile -or [long]$record.ContextSize -ne [long]$Plan.ContextSize) {
        return [pscustomobject]@{ Plan = $null; Reason = "memory-plan.json is for $($record.GgufFile) at $($record.ContextSize), not this plan" }
    }
    return [pscustomobject]@{ Plan = $record; Reason = '' }
}

function Use-ODSNativeStartOverride([string]$Path, $Plan) {
    # The Models page's Start anyway: true once, for exactly this model and
    # context. The file is consumed whatever plan it names.
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $record = Read-ODSNativeLlamaJsonOrNull $Path
    Remove-Item -LiteralPath $Path -Force
    return ($null -ne $record -and [string]$record.GgufFile -ceq [string]$Plan.GgufFile -and
        ($record.ContextSize -is [int] -or $record.ContextSize -is [long]) -and [long]$record.ContextSize -eq [long]$Plan.ContextSize)
}

function Write-ODSNativeLaunchLog([string]$RuntimeDir, [string]$Message) {
    Add-Content -LiteralPath (Join-Path $RuntimeDir 'native-llama-launch.log') -Encoding UTF8 -Value ("{0:o} {1}" -f (Get-Date), $Message)
}

function New-ODSNativeStartRefusal($Refusal, $Plan) {
    # The launcher's catch writes Data into ready.json (Error, Code, GgufFile,
    # ContextSize) for the model controller and the Models page.
    $exception = [Management.Automation.RuntimeException]::new([string]$Refusal.Text)
    $exception.Data['refusal'] = [string]$Refusal.Code
    $exception.Data['GgufFile'] = [string]$Plan.GgufFile
    $exception.Data['ContextSize'] = [long]$Plan.ContextSize
    return $exception
}

function Wait-ODSNativeLlamaMemory {
    <#
    .SYNOPSIS
        The CUDA start check: returns once the model may start, or throws the
        refusal after the sign-in wait. A refusal only this page file causes
        (structural) is final at once: waiting cannot change it.
    .OUTPUTS
        Checked, Override, CommitBeforeGiB and Gpu (for ready.json).
    #>
    param($Plan, [string]$RuntimeDir, [int]$WaitSeconds = $script:ODSNativeStartWaitSeconds)
    $memory = Read-ODSNativeMemoryPlan (Join-Path $RuntimeDir 'memory-plan.json') $Plan
    if (-not $memory.Plan) {
        Write-ODSNativeLaunchLog $RuntimeDir "Memory check skipped: $($memory.Reason)."
        return [pscustomobject]@{ Checked = $false; Override = $false; CommitBeforeGiB = $null; Gpu = (Get-ODSNativeGpuMemoryState) }
    }
    $override = Use-ODSNativeStartOverride (Join-Path $RuntimeDir 'start-override.json') $Plan
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ($true) {
        $commit = Get-ODSNativeCommitState
        $gpu = Get-ODSNativeGpuMemoryState
        if (-not $commit) {
            Write-ODSNativeLaunchLog $RuntimeDir 'Memory check skipped: Windows did not report its commit limit.'
            return [pscustomobject]@{ Checked = $false; Override = $false; CommitBeforeGiB = $null; Gpu = $gpu }
        }
        $refusal = Get-ODSNativeModelStartRefusal $memory.Plan $commit $gpu
        if (-not $refusal) {
            return [pscustomobject]@{ Checked = $true; Override = $false; CommitBeforeGiB = $commit.CommittedGiB; Gpu = $gpu }
        }
        if ($override) {
            Write-ODSNativeLaunchLog $RuntimeDir "Started although memory looked short (Start anyway): $($refusal.Text)"
            return [pscustomobject]@{ Checked = $true; Override = $true; CommitBeforeGiB = $commit.CommittedGiB; Gpu = $gpu }
        }
        if ($refusal.Kind -ceq 'structural' -or (Get-Date) -ge $deadline) {
            Write-ODSNativeLaunchLog $RuntimeDir "Model start refused ($($refusal.Code), $($refusal.Kind)): $($refusal.Text)"
            throw (New-ODSNativeStartRefusal $refusal $Plan)
        }
        Start-Sleep -Seconds $script:ODSNativeStartPollSeconds
    }
}

function Get-ODSNativeReadyMemoryFields($Memory, $Process) {
    # Facts recorded in ready.json after a CUDA start. Nothing decides on them:
    # the driver version keys the qualification cache, and the commit figures
    # are measurements for the hardware verification runs.
    $fields = [ordered]@{}
    if ($Memory.Gpu -and $Memory.Gpu.DriverVersion -match '^[0-9]{2,4}\.[0-9]{1,3}$') { $fields.DriverVersion = [string]$Memory.Gpu.DriverVersion }
    if ($Memory.Override) { $fields.MemoryOverride = $true }
    if ($null -ne $Memory.CommitBeforeGiB) { $fields.CommitBeforeGiB = $Memory.CommitBeforeGiB }
    $after = Get-ODSNativeCommitState
    if ($after) { $fields.CommitAfterGiB = $after.CommittedGiB }
    try {
        $Process.Refresh()
        $fields.ProcessCommitGiB = [math]::Round([double]$Process.PagedMemorySize64 / 1GB, 2)
    } catch {
        # The server already exited (InvalidOperationException, wrapped by the
        # method call): the launcher reports that next; a measurement never
        # decides anything. Anything else is a real error.
        $cause = $_.Exception
        while ($cause -and $cause -isnot [InvalidOperationException]) { $cause = $cause.InnerException }
        if (-not $cause) { throw }
    }
    return $fields
}

# --- Launch -----------------------------------------------------------------------

function Wait-ODSNativeLlamaStartup {
    <#
    .SYNOPSIS
        Wait until /health is 200 (503 means loading). A process that exits
        first fails immediately with its exit code explained.
    #>
    param([int]$Port, $Process, [int]$TimeoutSeconds = $script:ODSNativeLlamaStartupSeconds,
        [string]$Backend = 'vulkan', [string]$Architecture = 'x64')
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            $hint = Get-ODSNativeLlamaExitHint ([int]$Process.ExitCode) $Backend $Architecture
            throw ("llama-server exited during startup with code $($Process.ExitCode). $hint").Trim()
        }
        if ((Get-ODSNativeLlamaHealthState $Port) -eq 'ready') { return }
        Start-Sleep -Seconds 2
    }
    throw "llama-server did not finish loading its model within $TimeoutSeconds seconds."
}

function Invoke-ODSNativeLlamaRuntime($Plan, $Options, [string]$ReadyPath) {
    <#
    .SYNOPSIS
        Durable launcher body: start the pinned llama-server, prove its model
        and context, publish ready.json, then wait for the process to exit.
    #>
    Assert-ODSNativeLlamaPlan $Plan
    Assert-ODSNativeLlamaOptions $Options
    # Antivirus quarantine or a partial copy must stop here, not mid-load.
    $null = Test-ODSNativeLlamaInstall -Directory (Split-Path -Parent ([string]$Plan.ExecutablePath)) `
        -ExpectedZipSha256 ([string]$Options.ZipSha256) -ExpectedReleaseTag ([string]$Options.ReleaseTag)
    $apiKey = Read-ODSNativeLlamaApiKey ([string]$Options.ApiKeyPath)
    $memory = $null
    if ((Get-ODSNativeLlamaOptionsBackend $Options) -ceq 'cuda') {
        # Before the old record goes: while sign-in settles, a refusal the
        # last attempt recorded stays visible, so Start anyway still works.
        $memory = Wait-ODSNativeLlamaMemory -Plan $Plan -RuntimeDir (Split-Path -Parent $ReadyPath)
    }
    if (Test-Path -LiteralPath $ReadyPath) { Remove-Item -LiteralPath $ReadyPath -Force }
    if (@(Get-NetTCPConnection -LocalPort $Plan.Port -State Listen -ErrorAction SilentlyContinue).Count) {
        throw "Port $($Plan.Port) is already occupied; no existing process was changed."
    }
    $arguments = New-ODSNativeLlamaLaunchArguments $Plan $Options
    $modelArgument = $arguments[1]
    if (Test-Path -LiteralPath $Options.LogPath -PathType Leaf) {
        Move-Item -LiteralPath $Options.LogPath -Destination ([string]$Options.LogPath + '.1') -Force
    }
    $child = $null
    $identity = $null
    $ownershipPath = Join-Path (Split-Path -Parent $ReadyPath) 'process-ownership.json'
    try {
        $child = Start-Process -FilePath $Plan.ExecutablePath -ArgumentList (ConvertTo-ODSNativeLlamaArgumentString $arguments) `
            -WorkingDirectory (Split-Path -Parent $Plan.ExecutablePath) -WindowStyle Hidden -PassThru
        $null = $child.Handle
        $identity = [pscustomobject]@{ ProcessId = $child.Id; CreationDate = $child.StartTime
            ExecutablePath = $Plan.ExecutablePath; ExitedAt = $null }
        Write-ODSPortalProcessOwnership $ownershipPath $Plan @($identity)
        Wait-ODSNativeLlamaStartup -Port $Plan.Port -Process $child -Backend (Get-ODSNativeLlamaOptionsBackend $Options) `
            -Architecture (Get-ODSNativeLlamaOptionsArchitecture $Options)
        Assert-ODSNativeLlamaListener $Plan.Port $child.Id $Plan.ExecutablePath
        $proof = Get-ODSNativeLlamaModelProof -Port $Plan.Port -GgufFile $Plan.GgufFile `
            -ModelPaths @($modelArgument, (Join-Path $Plan.ModelsDir $Plan.GgufFile)) -ContextSize $Plan.ContextSize -ApiKey $apiKey `
            -Vision:($null -ne $Plan.MmprojFile)
        if (-not $proof.ContextVerified) { throw $proof.Message }
        Assert-ODSNativeLlamaListener $Plan.Port $child.Id $Plan.ExecutablePath
        $ready = [ordered]@{ ProcessId = $child.Id; StartedAt = $child.StartTime.ToUniversalTime().ToString('o')
            Port = $Plan.Port; ModelId = $proof.ModelId; ContextSize = $Plan.ContextSize; RuntimeContext = $proof.RuntimeContext }
        if ($memory) {
            # CUDA only; readers ignore keys they do not know.
            $fields = Get-ODSNativeReadyMemoryFields $memory $child
            foreach ($key in $fields.Keys) { $ready[$key] = $fields[$key] }
        }
        Write-ODSPrivateEnvFile -Path $ReadyPath -Content ($ready | ConvertTo-Json -Compress)
        $child.WaitForExit()
        if ($child.ExitCode -ne 0) { throw "llama-server exited with code $($child.ExitCode)." }
        return $child.ExitCode
    } catch {
        $startupFailure = $_.Exception.Message
        if ($identity) {
            if ($child.HasExited) { $identity.ExitedAt = $child.ExitTime }
            try {
                try {
                    if (Test-Path -LiteralPath $ReadyPath) { Remove-Item -LiteralPath $ReadyPath -Force }
                    Write-ODSPortalProcessOwnership $ownershipPath $Plan @($identity)
                } finally {
                    # Cleanup must still run when disk space or ACLs prevent
                    # writing the failure record or removing stale readiness.
                    try {
                        $owned = Get-ODSPortalOwnedProcessTree @($identity) @(Get-CimInstance Win32_Process -ErrorAction Stop) $Plan.ExecutablePath
                    } catch {
                        # This retained handle cannot point at a recycled PID.
                        # An unproved descendant is deliberately left untouched.
                        if (-not $child.HasExited) {
                            $child.Kill()
                            if (-not $child.WaitForExit(60000)) { throw 'The launched llama-server did not exit within 60 seconds of being stopped.' }
                        }
                        $identity.ExitedAt = $child.ExitTime
                        Write-ODSPortalProcessOwnership $ownershipPath $Plan @($identity)
                        throw
                    }
                    try {
                        # Preserve every child identity before stopping its
                        # parent, so interrupted cleanup can resume on rerun.
                        try { Write-ODSPortalProcessOwnership $ownershipPath $Plan $owned.Nodes }
                        finally { Stop-ODSPortalOwnedProcesses $owned.Handles }
                        Remove-Item -LiteralPath $ownershipPath -Force
                    } finally {
                        foreach ($process in $owned.Handles) { $process.Dispose() }
                    }
                }
            } catch {
                throw "llama-server startup failed: $startupFailure Cleanup could not be completed or recorded: $($_.Exception.Message)"
            }
        }
        throw
    } finally {
        if ($child) { $child.Dispose() }
    }
}
