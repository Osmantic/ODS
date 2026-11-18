# ============================================================================
# ODS Windows Desktop -- Runtime plan, app files and prerequisites
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: What windows-desktop.ps1 installs and runs, kept apart from the
#          steps that do it: folder layout, the CPU llama.cpp options, the
#          dashboard-api environment, which app files ship, and the
#          Microsoft Visual C++ runtime that llama.cpp's Windows build needs
#          but does not include.
# Expects: wsl-portal-amd.ps1 (New-ODSPortalRuntimeOptions,
#          Get-ODSPortalPortOwner, Test-ODSPortalPortBindable,
#          Get-ODSPortalUserSid), native-llama-runtime.ps1
#          (Assert-ODSNativeLlamaOptions), desktop-model.ps1
#          (Assert-ODSDesktopModelPin, Read-ODSDesktopModelStamp,
#          Test-ODSDesktopModelStamp), desktop-python.ps1
#          (Invoke-ODSDesktopStep, Invoke-ODSDesktopPinnedDownload),
#          app-shortcut.ps1 (Get-ODSEdgePath).
# Provides: Get-ODSDesktopPaths, Test-ODSDesktopVcRuntime,
#           Install-ODSDesktopVcRuntime, Get-ODSDesktopModelPin,
#           Get-ODSDesktopModelDownloadBytes,
#           Resolve-ODSDesktopContextSize, New-ODSDesktopLlamaOptions,
#           Assert-ODSDesktopRuntimeTakeover, New-ODSDesktopApiEnvironment,
#           ConvertTo-ODSDesktopEnvText, ConvertFrom-ODSDesktopEnvText,
#           Get-ODSDesktopAppFiles, Publish-ODSDesktopFolder,
#           Select-ODSDesktopApiPort, Get-ODSDesktopApiTaskName,
#           Get-ODSDesktopApiLauncherArguments, New-ODSDesktopStartPage,
#           Publish-ODSDesktopShortcut,
#           Format-ODSDesktopCommand
# Modder notes: everything lives under %LOCALAPPDATA%\ODS\desktop except the
#   model runtime, which shares main's ODS\lemonade\portal-runtime task and
#   model store (see Assert-ODSDesktopRuntimeTakeover for who may own it).
# ============================================================================

$script:ODSDesktopApiTaskPrefix = 'ODSDesktopApi-'
$script:ODSDesktopApiPortCandidates = @(3101, 13101, 23101)
$script:ODSDesktopRuntimeTaskDescription = 'ODS Desktop: llama.cpp model server on the CPU (127.0.0.1, API key). Starts at sign-in.'
# llama.cpp's MSVC build links the 2015-2022 runtime. Binaries from MSVC 17.10
# and later need its 14.40 DLLs: older ones crash in std::mutex.
$script:ODSDesktopVcRuntimeMinimum = [version]'14.40'
$script:ODSDesktopVcRuntimeFiles = @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
$script:ODSDesktopVcRedistUrl = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
# 0 installed, 1638 a newer one is already there, 3010 installed (restart later).
$script:ODSDesktopVcRedistSuccessCodes = @(0, 1638, 3010)
# The CPU route's context. Prompt processing on a laptop CPU is the slow part,
# and a restart loses llama.cpp's prompt cache; 16K keeps a full re-read of a
# long chat to about a minute instead of many.
$script:ODSDesktopCpuContextCap = 16384

function Get-ODSDesktopPaths([string]$Root = (Join-Path $env:LOCALAPPDATA 'ODS\desktop')) {
    $app = Join-Path $Root 'app'
    $launcher = Join-Path $Root 'launcher'
    return [pscustomobject]@{
        Root = $Root
        Python = Join-Path $Root 'python'
        App = $app
        AppSource = Join-Path $app 'dashboard-api'
        InstallDir = Join-Path $app 'ods'
        Dist = Join-Path $Root 'dist'
        Data = Join-Path $Root 'data'
        Logs = Join-Path $Root 'logs'
        Launcher = $launcher
        LauncherScript = Join-Path $launcher 'launch-api.ps1'
        Settings = Join-Path $Root 'settings.json'
        Environment = Join-Path $Root 'dashboard.env'
        Intent = Join-Path $Root 'api-intent.json'
        Ownership = Join-Path $Root 'api-ownership.json'
        Ready = Join-Path $Root 'api-ready.json'
        RuntimeOwner = Join-Path $Root 'runtime-owner.json'
        StartPage = Join-Path $Root 'open.html'
        Icon = Join-Path $Root 'ods.ico'
    }
}

# --- Microsoft Visual C++ runtime --------------------------------------------

function Get-ODSDesktopDllVersion([string]$Path) {
    $info = (Get-Item -LiteralPath $Path).VersionInfo
    return [version]::new($info.FileMajorPart, $info.FileMinorPart, $info.FileBuildPart, $info.FilePrivatePart)
}

function Test-ODSDesktopVcRuntime {
    <#
    .SYNOPSIS
        $null when the Visual C++ runtime llama.cpp needs is present and new
        enough, otherwise the reason in plain words.
    #>
    param([string]$SystemDirectory = [Environment]::SystemDirectory,
        [scriptblock]$ReadVersion = ${function:Get-ODSDesktopDllVersion})
    foreach ($name in $script:ODSDesktopVcRuntimeFiles) {
        $path = Join-Path $SystemDirectory $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return "The Microsoft Visual C++ runtime is not installed ($name is missing)." }
        $version = & $ReadVersion $path
        if ($version -lt $script:ODSDesktopVcRuntimeMinimum) {
            return "The Microsoft Visual C++ runtime is too old ($name is $version; ODS needs $($script:ODSDesktopVcRuntimeMinimum) or newer)."
        }
    }
    return $null
}

function Test-ODSDesktopMicrosoftSignature([string]$Path) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    return ($signature.Status -eq 'Valid' -and
        [string]$signature.SignerCertificate.Subject -match '^CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US$')
}

function Invoke-ODSDesktopElevatedInstaller([string]$Path) {
    # Only this Microsoft installer runs elevated (Windows asks first);
    # setup itself stays a normal user process.
    $process = Start-Process -FilePath $Path -ArgumentList @('/install', '/passive', '/norestart') -Verb RunAs -Wait -PassThru
    return $process.ExitCode
}

function Read-ODSDesktopConsent([string]$Question) {
    return (Read-Host "$Question [Y/n]") -notmatch '^(n|no)$'
}

function Install-ODSDesktopVcRuntime {
    <#
    .SYNOPSIS
        Install Microsoft's Visual C++ runtime with the owner's consent:
        downloaded over HTTPS from Microsoft, run only when Windows confirms
        a valid Microsoft Corporation signature. Returns $true when the
        runtime is usable afterwards, $false when the owner declined.
    #>
    param([string]$Reason, [bool]$NonInteractive,
        [scriptblock]$Ask = ${function:Read-ODSDesktopConsent},
        [scriptblock]$Download = ${function:Invoke-ODSDesktopPinnedDownload},
        [scriptblock]$Verify = ${function:Test-ODSDesktopMicrosoftSignature},
        [scriptblock]$Run = ${function:Invoke-ODSDesktopElevatedInstaller},
        [scriptblock]$Check = { Test-ODSDesktopVcRuntime })
    Write-Host "         $Reason"
    Write-Host '         The model engine needs it. ODS can install it from microsoft.com (about 25 MB); Windows asks for permission once.'
    if ($NonInteractive) {
        Write-Host "         Install it from $($script:ODSDesktopVcRedistUrl), or run setup without -NonInteractive, then run setup again."
        return $false
    }
    if (-not (& $Ask 'Install the Microsoft Visual C++ runtime now?')) { return $false }
    $folder = Join-Path ([IO.Path]::GetTempPath()) ('ods-vc-runtime-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $folder
    try {
        $installer = Join-Path $folder 'vc_redist.x64.exe'
        $code = Invoke-ODSDesktopStep $Download @($script:ODSDesktopVcRedistUrl, $installer)
        if ($code -ne 0) { throw "Could not download the Microsoft Visual C++ runtime (curl exit $code). Check the connection and run setup again." }
        if (-not (& $Verify $installer)) {
            throw 'The downloaded Visual C++ runtime installer is not signed by Microsoft, so it was not run. Run setup again.'
        }
        $exit = [int](& $Run $installer)
        if ($exit -notin $script:ODSDesktopVcRedistSuccessCodes) {
            throw "The Microsoft Visual C++ runtime installer stopped with code $exit. Run setup again, or install it from $($script:ODSDesktopVcRedistUrl)."
        }
    } finally {
        Remove-Item -LiteralPath $folder -Recurse -Force
    }
    $after = & $Check
    if ($after) { throw "$after Restart Windows, then run setup again." }
    return $true
}

# --- Model engine ------------------------------------------------------------

function Get-ODSDesktopModelPin($Recommendation) {
    # The catalog's choice for this PC as a desktop-model.ps1 pin.
    $pin = [pscustomobject]@{ Url = [string]$Recommendation.GgufUrl; File = [string]$Recommendation.GgufFile
        Sha256 = ([string]$Recommendation.GgufSha256).ToLowerInvariant(); Size = $null }
    Assert-ODSDesktopModelPin $pin
    return $pin
}

function Get-ODSDesktopModelDownloadBytes($Selection, $Config, [string]$ModelsDir) {
    # What the model step may still write. A file counts as done only when
    # its verified stamp matches: a damaged copy is downloaded again beside
    # it, so it needs the full size. A resumable .partial needs only the rest.
    if ($Selection.Carried) { return [long]0 }
    $target = Join-Path $ModelsDir ([string]$Selection.GgufFile)
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $stamp = Read-ODSDesktopModelStamp "$target.ods-verified.json"
        if (Test-ODSDesktopModelStamp $stamp (Get-Item -LiteralPath $target) ([string]$Config.GgufSha256).ToLowerInvariant()) { return [long]0 }
    }
    $remaining = [long]$Config.ModelSizeMB * 1MB
    $partial = "$target.partial"
    if (Test-Path -LiteralPath $partial -PathType Leaf) { $remaining -= (Get-Item -LiteralPath $partial).Length }
    # [long]0: Windows PowerShell picks Max(int, int) for a bare 0 and fails
    # on any model over 2 GB.
    return [math]::Max([long]0, $remaining)
}

function Resolve-ODSDesktopContextSize([int]$CatalogContext, [int]$Cap = $script:ODSDesktopCpuContextCap) {
    $size = [math]::Min($CatalogContext, $Cap)
    if ($size -lt 4096) { throw "The recommended model allows only $CatalogContext tokens of context; ODS needs at least 4096." }
    return [int]$size
}

function New-ODSDesktopLlamaOptions($Runtime, $Qualification) {
    # main's options with the explicit CPU route: --device none and no GPU
    # layers, never llama.cpp's silent fallback.
    $options = New-ODSPortalRuntimeOptions $Runtime $Qualification
    $options.Device = 'none'
    $options.NGpuLayers = '0'
    Assert-ODSNativeLlamaOptions ([pscustomobject]$options)
    return $options
}

function Assert-ODSDesktopRuntimeTakeover($Existing, [string]$OwnerPath) {
    <#
    .SYNOPSIS
        Desktop starts a model runtime only where none runs, or where the one
        that runs is its own. A WSL Portal's runtime, or one ODS cannot prove
        Desktop made, is never stopped or replaced.
    #>
    if ($Existing.Kind -eq 'none') { return }
    if ($Existing.Kind -eq 'lemonade') {
        throw 'An ODS Portal (WSL) install on this PC runs its model with Lemonade. ODS Desktop does not replace it; nothing was changed.'
    }
    $plan = $Existing.Launch.Plan
    if ($plan.PSObject.Properties['WslDistro'] -and $plan.WslDistro) {
        throw "The ODS Portal in WSL ($($plan.WslDistro)) uses this PC's model runtime. ODS Desktop does not take it over; nothing was changed."
    }
    if (-not (Test-Path -LiteralPath $OwnerPath -PathType Leaf)) {
        throw "Another ODS install on this PC already runs a model runtime ($($Existing.Launch.TaskName)). ODS Desktop does not take it over; nothing was changed."
    }
}

# --- dashboard-api -----------------------------------------------------------

function New-ODSDesktopApiEnvironment($Paths, [int]$ApiPort, [int]$LlamaPort, [string]$RuntimeDir, [string]$ApiKey) {
    # The whole environment dashboard-api runs with (runtime_profile.py,
    # native_llm.py, native_front.py read it).
    if ($ApiKey -cnotmatch '^[A-Za-z0-9_-]{32,128}$') { throw 'The dashboard API key is invalid.' }
    return [ordered]@{
        ODS_RUNTIME_PROFILE = 'windows-desktop'
        ODS_DATA_DIR = $Paths.Data
        ODS_INSTALL_DIR = $Paths.InstallDir
        ODS_EXTENSIONS_DIR = Join-Path $Paths.InstallDir 'extensions\services'
        DASHBOARD_API_KEY = $ApiKey
        DASHBOARD_API_PORT = [string]$ApiPort
        ODS_NATIVE_LLM_URL = "http://127.0.0.1:$LlamaPort"
        ODS_NATIVE_LLM_RUNTIME_DIR = $RuntimeDir
        ODS_DASHBOARD_DIST = $Paths.Dist
        PYTHONUTF8 = '1'
    }
}

function ConvertTo-ODSDesktopEnvText($Environment) {
    $lines = foreach ($key in $Environment.Keys) {
        $value = [string]$Environment[$key]
        if ($key -cnotmatch '^[A-Z][A-Z0-9_]{0,63}$' -or $value -match '[\r\n\x00]') { throw "The dashboard setting $key cannot be saved." }
        "$key=$value"
    }
    return (($lines -join "`n") + "`n")
}

function ConvertFrom-ODSDesktopEnvText([string]$Text) {
    $environment = [ordered]@{}
    foreach ($line in ($Text -split "`n")) {
        if (-not $line) { continue }
        $at = $line.IndexOf('=')
        if ($at -lt 1) { throw 'The saved dashboard settings are damaged; run setup again.' }
        $environment[$line.Substring(0, $at)] = $line.Substring($at + 1)
    }
    return $environment
}

function Get-ODSDesktopAppFiles([string]$SourceDir) {
    # What the dashboard-api image ships (its Dockerfile COPY lines): the
    # modules, routers, the performance evidence and the lock. Never tests.
    $root = [IO.Path]::GetFullPath($SourceDir).TrimEnd('\') + '\'
    $files = @(Get-ChildItem -LiteralPath $SourceDir -File -Filter '*.py') +
        @(Get-ChildItem -LiteralPath $SourceDir -File | Where-Object { $_.Name -cin @('performance_evidence.json', 'requirements.lock') }) +
        @(Get-ChildItem -LiteralPath (Join-Path $SourceDir 'routers') -File -Recurse | Where-Object { $_.FullName -notmatch '\\__pycache__\\' })
    return @($files | ForEach-Object { $_.FullName.Substring($root.Length) } | Sort-Object -Unique)
}

function Publish-ODSDesktopFolder([string]$Destination, [scriptblock]$Fill) {
    # Build the folder beside its destination, then swap it in whole, so a
    # failed or interrupted update never leaves half of a new app.
    $parent = Split-Path -Parent $Destination
    $null = New-Item -ItemType Directory -Force -Path $parent
    $staging = Join-Path $parent ('.ods-staging-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $staging
    try {
        & $Fill $staging
        $previous = $null
        if (Test-Path -LiteralPath $Destination) {
            $previous = "$Destination.previous-" + [guid]::NewGuid().ToString('N')
            Move-Item -LiteralPath $Destination -Destination $previous
        }
        try {
            Move-Item -LiteralPath $staging -Destination $Destination
        } catch {
            if ($null -ne $previous) { Move-Item -LiteralPath $previous -Destination $Destination }
            throw
        }
        if ($null -ne $previous) { Remove-Item -LiteralPath $previous -Recurse -Force }
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
}

function Copy-ODSDesktopFile([string]$Source, [string]$Destination) {
    $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination)
    Copy-Item -LiteralPath $Source -Destination $Destination
}

function Publish-ODSDesktopApp([string]$SourceRoot, $Paths) {
    # dashboard-api plus the read-only slice of the ODS tree it reads: config,
    # manifest.json and each service's manifest.yaml.
    $apiSource = Join-Path $SourceRoot 'extensions\services\dashboard-api'
    Publish-ODSDesktopFolder $Paths.App {
        param($stage)
        foreach ($relative in (Get-ODSDesktopAppFiles $apiSource)) {
            Copy-ODSDesktopFile (Join-Path $apiSource $relative) (Join-Path (Join-Path $stage 'dashboard-api') $relative)
        }
        $ods = Join-Path $stage 'ods'
        $null = New-Item -ItemType Directory -Force -Path $ods
        Copy-Item -LiteralPath (Join-Path $SourceRoot 'config') -Destination (Join-Path $ods 'config') -Recurse
        Copy-ODSDesktopFile (Join-Path $SourceRoot 'manifest.json') (Join-Path $ods 'manifest.json')
        foreach ($service in @(Get-ChildItem -LiteralPath (Join-Path $SourceRoot 'extensions\services') -Directory)) {
            $manifest = Join-Path $service.FullName 'manifest.yaml'
            if (Test-Path -LiteralPath $manifest -PathType Leaf) {
                Copy-ODSDesktopFile $manifest (Join-Path $ods "extensions\services\$($service.Name)\manifest.yaml")
            }
        }
    }
}

function Publish-ODSDesktopDist([string]$DistSource, $Paths) {
    if (-not (Test-Path -LiteralPath (Join-Path $DistSource 'index.html') -PathType Leaf)) {
        throw "The built dashboard was not found at $DistSource. Build it (npm ci, then npm run build in ods\extensions\services\dashboard) or pass -DashboardDist, then run setup again."
    }
    Publish-ODSDesktopFolder $Paths.Dist {
        param($stage)
        foreach ($item in @(Get-ChildItem -LiteralPath $DistSource -Force)) { Copy-Item -LiteralPath $item.FullName -Destination $stage -Recurse }
    }
}

function Select-ODSDesktopApiPort([int]$Saved, [int]$Current = 0) {
    # A rerun keeps the owner's port: chats are saved per address, so a new
    # port would hide them. The app setup just stopped may leave the port
    # briefly unbindable, so with no listener it is reused as is; another
    # program listening on it stops setup with its name. A new install takes
    # the first quiet candidate.
    $candidates = if ($Saved -ge 1 -and $Saved -le 65535) { @($Saved) } else { $script:ODSDesktopApiPortCandidates }
    $taken = @()
    foreach ($port in $candidates) {
        $owner = Get-ODSPortalPortOwner $port
        if ($port -eq $Current -and -not $owner) { return $port }
        if (-not $owner -and (Test-ODSPortalPortBindable $port)) { return $port }
        $taken += "$port ($(if ($owner) { $owner } else { 'reserved' }))"
    }
    throw "No free port for the ODS window: $($taken -join ', '). Close the program using it or pass -Port with a free port, then run setup again."
}

function Get-ODSDesktopApiTaskName { return ($script:ODSDesktopApiTaskPrefix + (Get-ODSPortalUserSid)) }

function Format-ODSDesktopCommand([string]$Script, [string]$Arguments = '') {
    # Every command ODS prints runs under the default execution policy.
    $command = 'powershell -NoProfile -ExecutionPolicy Bypass -File "' + $Script + '"'
    if ($Arguments) { $command += " $Arguments" }
    return $command
}

function Get-ODSDesktopApiLauncherArguments($Paths) {
    return ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Paths.LauncherScript + '"')
}

# The ODS window opens this page first. At sign-in the app needs a few
# seconds; Edge would show "can't reach this page" and stop. The page asks
# the app once a second and switches to it the moment it answers (any
# answer counts, so mode no-cors is enough). ASCII only.
$script:ODSDesktopStartPage = @'
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ODS</title>
<meta name="color-scheme" content="dark light">
<style>
:root { --bg: #111214; --fg: #ececee; --muted: #a1a1aa; }
@media (prefers-color-scheme: light) { :root { --bg: #f7f7f8; --fg: #18181b; --muted: #52525b; } }
html, body { height: 100%; margin: 0; }
body { display: grid; place-items: center; background: var(--bg); color: var(--fg); font: 16px/1.5 "Segoe UI", system-ui, sans-serif; }
main { max-width: 30rem; padding: 0 16px; text-align: center; }
h1 { font-size: 1.5rem; font-weight: 600; margin: 0 0 .5rem; }
p { color: var(--muted); margin: 0; }
</style>
</head>
<body>
<main>
<h1>ODS is starting</h1>
<p id="note">This takes a few seconds after you sign in to Windows.</p>
</main>
<script>
const app = 'http://localhost:__PORT__/';
const started = Date.now();
async function check() {
  try {
    await fetch(app + 'health', { mode: 'no-cors', cache: 'no-store' });
    location.replace(app);
    return;
  } catch (error) {
    // Not answering yet: try again in a second.
  }
  if (Date.now() - started > 120000) {
    document.getElementById('note').textContent =
      'ODS has not started. Restart your PC, or run ODS setup again to repair it.';
  }
  setTimeout(check, 1000);
}
check();
</script>
</body>
</html>
'@

function New-ODSDesktopStartPage([int]$Port) {
    if ($Port -lt 1 -or $Port -gt 65535) { throw "The ODS window needs a port between 1 and 65535, not $Port." }
    return $script:ODSDesktopStartPage.Replace('__PORT__', [string]$Port)
}

function New-ODSDesktopAppShortcut([string]$Edge, [string]$Page, [string]$Icon, [string[]]$Folders) {
    # main's New-ODSAppShortcut opens a localhost URL; this one opens the
    # start page, which then opens that URL.
    $arguments = '--app=' + ([Uri]::new($Page)).AbsoluteUri
    $shell = New-Object -ComObject WScript.Shell
    try {
        $created = @()
        foreach ($folder in $Folders) {
            if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
            $path = Join-Path $folder 'ODS.lnk'
            try {
                $link = $shell.CreateShortcut($path)
                $link.TargetPath = $Edge
                $link.Arguments = $arguments
                $link.WorkingDirectory = Split-Path -Parent $Edge
                $link.Description = 'ODS: your local AI in its own window. Press F11 for full screen.'
                if (Test-Path -LiteralPath $Icon -PathType Leaf) { $link.IconLocation = "$Icon,0" }
                $link.Save()
                $created += $path
            } catch {
                # Controlled folder access or a read-only folder: the other
                # shortcut still gets made.
                Write-Warning "The ODS shortcut could not be saved in ${folder}: $($_.Exception.Message)"
            }
        }
        return $created
    } finally {
        $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
}

function Publish-ODSDesktopShortcut {
    # The Edge app window, opening the start page, when Edge is there;
    # otherwise a web link that opens the default browser. A shortcut
    # problem never fails the install.
    param([Parameter(Mandatory = $true)]$Paths, [int]$Port, [string]$IconSource = '', [string]$Edge = (Get-ODSEdgePath),
        [string[]]$Folders = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')))
    $null = New-Item -ItemType Directory -Force -Path $Paths.Root
    [IO.File]::WriteAllText($Paths.StartPage, (New-ODSDesktopStartPage $Port), [Text.Encoding]::ASCII)
    if ($IconSource -and (Test-Path -LiteralPath $IconSource -PathType Leaf)) { Copy-Item -LiteralPath $IconSource -Destination $Paths.Icon -Force }
    if ($Edge) {
        $made = @(New-ODSDesktopAppShortcut $Edge $Paths.StartPage $Paths.Icon $Folders)
        if ($made.Count) { return $made }
    }
    $made = @()
    foreach ($folder in $Folders) {
        if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
        $path = Join-Path $folder 'ODS.url'
        [IO.File]::WriteAllText($path, "[InternetShortcut]`r`nURL=http://localhost:$Port/`r`n", [Text.Encoding]::ASCII)
        $made += $path
    }
    return $made
}
