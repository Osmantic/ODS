# ============================================================================
# ODS Windows Installer -- native llama.cpp runtime (AMD GPUs on Windows)
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Acquire, qualify, launch and prove ggml-org's llama-server.exe
#          (Vulkan build) for the Windows Portal task and the legacy native
#          installer. WSL2 cannot drive an AMD GPU, so the model runs here.
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
#
# The Portal's durable launcher copies this file and private-file.ps1 beside
# its private plan. Keep both ASCII-only and free of other ODS dependencies.
# ============================================================================

. (Join-Path $PSScriptRoot 'private-file.ps1')

$script:ODSNativeLlamaRequiredFiles = @('llama-server.exe', 'ggml-vulkan.dll')
$script:ODSNativeLlamaExecutableExtensions = @('.exe', '.dll', '.sys', '.com', '.bat', '.cmd', '.ps1', '.psm1', '.vbs', '.js', '.msi', '.scr', '.cpl', '.ocx')
$script:ODSNativeLlamaStartupSeconds = 900
$script:ODSNativeLlamaContextAlignment = 256

# --- Pin ----------------------------------------------------------------------

function Assert-ODSNativeLlamaPin($Pin) {
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

function Get-ODSNativeLlamaPin {
    <#
    .SYNOPSIS
        The pinned llama.cpp Windows Vulkan release, from config/backends/amd.json
        runtime.llama_server.windows, or constants.ps1 until amd.json has it.
    #>
    param([string]$SourceRoot = '')

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
    return [pscustomobject]@{
        ReleaseTag = $tag
        Build = [int]$tag.Substring(1)
        Asset = [string]$pin.asset
        Sha256 = [string]$pin.sha256
        Size = [long]$pin.size
        Url = "https://github.com/ggml-org/llama.cpp/releases/download/$tag/$([string]$pin.asset)"
        Source = $source
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
    return (Join-Path $Root ("{0}-win-vulkan-x64" -f $Pin.ReleaseTag))
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
        [string]$ExpectedReleaseTag = ''
    )
    $pinPath = Join-Path $Directory 'pin.json'
    if (-not (Test-Path -LiteralPath $pinPath -PathType Leaf)) { throw "The llama.cpp runtime has no pin.json: $Directory" }
    $record = [IO.File]::ReadAllText($pinPath, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json -ErrorAction Stop
    if ($record.schemaVersion -ne 1 -or [string]$record.releaseTag -cnotmatch '^b[0-9]{3,6}$' -or
        [string]$record.zipSha256 -cnotmatch '^[0-9a-f]{64}$' -or @($record.files).Count -eq 0) {
        throw "The llama.cpp pin.json is invalid: $pinPath"
    }
    if ($ExpectedReleaseTag -and [string]$record.releaseTag -cne $ExpectedReleaseTag) {
        throw "The llama.cpp runtime is $($record.releaseTag), not the pinned $ExpectedReleaseTag."
    }
    if ($ExpectedZipSha256 -and [string]$record.zipSha256 -cne $ExpectedZipSha256) {
        throw 'The llama.cpp runtime was installed from a different archive than the pinned one.'
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
    foreach ($required in $script:ODSNativeLlamaRequiredFiles) {
        if (-not $listed.ContainsKey($required)) { throw "The llama.cpp runtime is missing $required." }
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
    }
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
    #>
    param(
        [Parameter(Mandatory = $true)]$Pin,
        [string]$Root = (Get-ODSNativeLlamaRoot)
    )
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
        if ($child.Name -notmatch '^(b[0-9]{3,6}-win-vulkan-x64|\.staging-[0-9a-f]{32}|\.damaged-b[0-9]{3,6}-[0-9a-f]{32})$') { continue }
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

function Get-ODSNativeLlamaExitHint([int]$ExitCode) {
    # NTSTATUS values arrive as negative 32-bit exit codes.
    if ($ExitCode -eq -1073741515) { return 'A DLL it needs is missing (0xC0000135), most likely the Microsoft Visual C++ 2015-2022 Redistributable (x64). Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe or with: winget install Microsoft.VCRedist.2015+.x64' }
    if ($ExitCode -eq -1073740760) { return 'Windows code integrity refused one of its files (0xC0000428). Smart App Control, Windows Defender Application Control or another allow-list policy blocks the unsigned llama.cpp binaries.' }
    if ($ExitCode -eq -1073741701) { return 'Windows reports a bad image (0xC000007B): the download is damaged or not built for x64.' }
    if ($ExitCode -eq -1073741502) { return 'A DLL failed to initialize (0xC0000142). Restart Windows, update the AMD graphics driver, then rerun setup.' }
    if ($ExitCode -eq -1073741819) { return 'It crashed (0xC0000005), usually inside the graphics driver. Update the AMD Adrenalin driver from amd.com/support, then rerun setup.' }
    return ''
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
    # llama.cpp b9014 prints "  Vulkan0: <name> (<total> MiB, <free> MiB free)".
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
        name match, a single AMD-looking Vulkan device is accepted.
    #>
    param([object[]]$Devices, [string]$AdapterName)
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

function Test-ODSNativeLlamaQualification {
    <#
    .SYNOPSIS
        Qualify an installed llama-server.exe before any cutover.
    .OUTPUTS
        Build, Devices and Device ($null when no usable Vulkan device exists).
        Throws with guidance when the binary cannot run at all.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ExecutablePath,
        [Parameter(Mandatory = $true)][int]$ExpectedBuild,
        [string]$AdapterName = '',
        $CodeIntegrity = $null
    )
    if ($null -eq $CodeIntegrity) { $CodeIntegrity = Get-ODSNativeLlamaCodeIntegrityState }
    $policyHint = Format-ODSNativeLlamaPolicyHint $CodeIntegrity
    $version = Invoke-ODSNativeLlamaProbe -ExecutablePath $ExecutablePath -Arguments @('--version')
    if (-not $version.Started) {
        throw ("llama-server.exe could not start: $($version.StartError) " + (Get-ODSNativeLlamaStartHint $version.NativeError) + " $policyHint").Trim()
    }
    if ($version.TimedOut) { throw 'llama-server.exe --version did not finish within 60 seconds.' }
    if ($version.ExitCode -ne 0) {
        $hint = Get-ODSNativeLlamaExitHint $version.ExitCode
        throw ("llama-server.exe --version failed with exit code $($version.ExitCode). $hint $policyHint").Trim()
    }
    $match = [regex]::Match([string]$version.Output, '(?m)^version:\s*([0-9]+)\s*\(([0-9a-f]+)\)')
    if (-not $match.Success) { throw 'llama-server.exe --version did not report a build number.' }
    $build = [int]$match.Groups[1].Value
    if ($build -ne $ExpectedBuild) { throw "llama-server.exe reports build $build, not the pinned b$ExpectedBuild." }
    $list = Invoke-ODSNativeLlamaProbe -ExecutablePath $ExecutablePath -Arguments @('--list-devices')
    if (-not $list.Started -or $list.TimedOut -or $list.ExitCode -ne 0) {
        $code = if ($list.Started -and -not $list.TimedOut) { $list.ExitCode } else { 0 }
        throw ("llama-server.exe --list-devices failed. " + (Get-ODSNativeLlamaExitHint $code) + " $policyHint").Trim()
    }
    $devices = @(ConvertFrom-ODSNativeLlamaDeviceList ([string]$list.Output))
    $device = Select-ODSNativeLlamaDevice -Devices $devices -AdapterName $AdapterName
    return [pscustomobject]@{
        Build = $build
        Commit = $match.Groups[2].Value
        Devices = $devices
        Device = $device
        PolicyHint = $policyHint
    }
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

function Assert-ODSNativeLlamaOptions($Options) {
    # Device 'none' is the explicit CPU route (no usable Vulkan device), and
    # only with zero GPU layers; never a silent fallback. NGpuLayers takes
    # every N_GPU_LAYERS value .env.schema.json allows: auto, all or a layer
    # count, which llama.cpp range-checks itself.
    $cpuRoute = [string]$Options.Device -ceq 'none'
    if ($Options.schemaVersion -ne 1 -or
        ([string]$Options.Device -cnotmatch '^Vulkan[0-9]{1,2}$' -and -not $cpuRoute) -or
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
        One bounded GET against the loopback llama-server. Returns StatusCode
        (0 when nothing answered) and Body. The key is sent only as a header.
    #>
    param([int]$Port, [string]$Path, [string]$ApiKey = '', [int]$TimeoutMilliseconds = 5000)
    $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$Port$Path")
    $request.Method = 'GET'
    $request.Timeout = $TimeoutMilliseconds
    $request.ReadWriteTimeout = $TimeoutMilliseconds
    $request.Proxy = $null
    if ($ApiKey) { $request.Headers['Authorization'] = 'Bearer ' + $ApiKey }
    $response = $null
    try {
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

function Get-ODSPortalOwnedProcessTree([object[]]$Roots, [object[]]$Nodes) {
    $tree = [Collections.Generic.List[object]]::new()
    foreach ($root in $Roots) { $tree.Add($root) }
    for ($index = 0; $index -lt $tree.Count; $index++) {
        $parent = $tree[$index]
        if ($parent.CreationDate -isnot [datetime] -or -not $parent.ExecutablePath) {
            throw "Cannot read the owned runtime process identity (PID $($parent.ProcessId)); no process was stopped."
        }
        foreach ($node in @($Nodes | Where-Object { $_.ParentProcessId -eq $parent.ProcessId })) {
            if ($node.CreationDate -isnot [datetime] -or $node.CreationDate.ToUniversalTime() -lt $parent.CreationDate.ToUniversalTime()) {
                throw 'The runtime process ancestry is stale or ambiguous; no process was stopped.'
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

# --- Launch -----------------------------------------------------------------------

function Wait-ODSNativeLlamaStartup {
    <#
    .SYNOPSIS
        Wait until /health is 200 (503 means loading). A process that exits
        first fails immediately with its exit code explained.
    #>
    param([int]$Port, $Process, [int]$TimeoutSeconds = $script:ODSNativeLlamaStartupSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            $hint = Get-ODSNativeLlamaExitHint ([int]$Process.ExitCode)
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
        Wait-ODSNativeLlamaStartup -Port $Plan.Port -Process $child
        Assert-ODSNativeLlamaListener $Plan.Port $child.Id $Plan.ExecutablePath
        $proof = Get-ODSNativeLlamaModelProof -Port $Plan.Port -GgufFile $Plan.GgufFile `
            -ModelPaths @($modelArgument, (Join-Path $Plan.ModelsDir $Plan.GgufFile)) -ContextSize $Plan.ContextSize -ApiKey $apiKey `
            -Vision:($null -ne $Plan.MmprojFile)
        if (-not $proof.ContextVerified) { throw $proof.Message }
        Assert-ODSNativeLlamaListener $Plan.Port $child.Id $Plan.ExecutablePath
        $ready = [ordered]@{ ProcessId = $child.Id; StartedAt = $child.StartTime.ToUniversalTime().ToString('o')
            Port = $Plan.Port; ModelId = $proof.ModelId; ContextSize = $Plan.ContextSize; RuntimeContext = $proof.RuntimeContext }
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
                        $owned = Get-ODSPortalOwnedProcessTree @($identity) @(Get-CimInstance Win32_Process -ErrorAction Stop)
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
