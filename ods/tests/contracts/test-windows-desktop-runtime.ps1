# Windows Desktop runtime plan: the Visual C++ prerequisite, the CPU model
# options, who may own the model runtime, the dashboard-api environment, the
# shipped app files, whole-folder publishing, ports, shortcuts and the printed
# commands. Downloads, Windows prompts and elevation are fixtures; only
# temporary files are written. Runs under Windows PowerShell 5.1 and 7.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$lib = Join-Path $root 'installers/windows/lib'
. (Join-Path $lib 'wsl-portal-amd.ps1')
. (Join-Path $lib 'app-shortcut.ps1')
foreach ($name in 'desktop-model.ps1', 'desktop-settings.ps1', 'desktop-python.ps1', 'desktop-runtime.ps1', 'desktop-api-launcher.ps1') {
    . (Join-Path $lib $name)
}
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}
# The real one runs llama-server.exe to ask which flags it has.
function Get-ODSNativeReasoningArgs { param($Executable, $Mode, $FallbackFormat) return @('--reasoning', 'off') }

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-desktop-runtime-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
try {
    # --- Visual C++ runtime: present, missing or too old -----------------------
    $system = Join-Path $fixture 'System32'
    $null = New-Item -ItemType Directory -Path $system
    foreach ($name in 'msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll') { [IO.File]::WriteAllText((Join-Path $system $name), 'fixture') }
    $current = { param($Path) [version]'14.51.36247.0' }
    Check ($null -eq (Test-ODSDesktopVcRuntime -SystemDirectory $system -ReadVersion $current)) 'a 14.51 Visual C++ runtime is accepted'
    $old = { param($Path) if ($Path -like '*msvcp140.dll') { [version]'14.38.33135.0' } else { [version]'14.51.36247.0' } }
    $reason = Test-ODSDesktopVcRuntime -SystemDirectory $system -ReadVersion $old
    Check ($reason -match 'too old' -and $reason -match 'msvcp140\.dll is 14\.38' -and $reason -match '14\.40') 'a runtime older than 14.40 is named as too old (newer MSVC builds crash on it)'
    Remove-Item -LiteralPath (Join-Path $system 'vcruntime140_1.dll')
    Check ((Test-ODSDesktopVcRuntime -SystemDirectory $system -ReadVersion $current) -match 'not installed \(vcruntime140_1\.dll is missing\)') 'a missing runtime DLL is named'
    Check ($null -eq (Test-ODSDesktopVcRuntime)) 'this machine has the runtime the pinned llama.cpp needs'

    # --- Installing it: consent, Microsoft signature, exit codes ---------------
    $script:log = @()
    $download = { param($Url, $Destination) $script:log += "download $Url"; [IO.File]::WriteAllText($Destination, 'installer'); 0 }
    $signed = { param($Path) $script:log += 'verify'; $true }
    $ok = { $null }
    # Each case overrides some fixtures; the rest are the happy path.
    function Install-Case([hashtable]$Override) {
        $arguments = @{ Reason = 'The Microsoft Visual C++ runtime is not installed.'; NonInteractive = $false; Ask = { param($Question) $true }
            Download = $download; Verify = $signed; Run = { param($Path) 0 }; Check = { $null } }
        foreach ($key in $Override.Keys) { $arguments[$key] = $Override[$key] }
        return (Install-ODSDesktopVcRuntime @arguments)
    }
    $result = Install-Case @{ NonInteractive = $true }
    Check ($result -eq $false -and $script:log.Count -eq 0) 'non-interactive setup never downloads or elevates; it explains instead'
    $result = Install-Case @{ Ask = { param($Question) $false } }
    Check ($result -eq $false -and $script:log.Count -eq 0) 'an owner who declines gets nothing downloaded'
    $script:ran = ''
    $result = Install-Case @{ Run = { param($Path) $script:ran = $Path; 1638 } }
    Check ($result -eq $true -and $script:log[0] -ceq 'download https://aka.ms/vs/17/release/vc_redist.x64.exe' -and $script:log[1] -ceq 'verify') 'consent downloads Microsoft''s installer and verifies it before running'
    Check ($script:ran -like '*vc_redist.x64.exe' -and -not (Test-Path -LiteralPath (Split-Path -Parent $script:ran))) 'exit 1638 (a newer runtime is installed) is success, and the download is removed'
    $failure = Get-Failure { Install-Case @{ Verify = { param($Path) $false }; Run = { param($Path) $script:ran = 'ran'; 0 } } }
    Check ($failure -match 'not signed by Microsoft' -and $script:ran -ne 'ran') 'an installer without a valid Microsoft signature is never run'
    $failure = Get-Failure { Install-Case @{ Run = { param($Path) 1603 } } }
    Check ($failure -match 'stopped with code 1603') 'an installer failure is reported with its code'
    $failure = Get-Failure { Install-Case @{ Run = { param($Path) 3010 }; Check = { 'still missing.' } } }
    Check ($failure -match 'still missing\. Restart Windows') 'a runtime still unusable after install asks for a restart'
    $failure = Get-Failure { Install-Case @{ Download = { param($Url, $Destination) 6 } } }
    Check ($failure -match 'curl exit 6') 'a failed download stops before anything runs'

    # --- Model pin and context --------------------------------------------------
    $recommendation = [pscustomobject]@{ GgufFile = 'Qwen3.5-4B-Q4_K_M.gguf'; GgufSha256 = ('AB' * 32)
        GgufUrl = 'https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q4_K_M.gguf' }
    $pin = Get-ODSDesktopModelPin $recommendation
    Check ($pin.Sha256 -ceq ('ab' * 32) -and $pin.File -ceq 'Qwen3.5-4B-Q4_K_M.gguf') 'the catalog choice becomes a model pin with a lowercase SHA-256'
    $recommendation.GgufUrl = 'https://example.com/Qwen3.5-4B-Q4_K_M.gguf'
    Check ((Get-Failure { Get-ODSDesktopModelPin $recommendation }) -match 'unexpected location') 'a catalog URL off huggingface.co is refused'
    Check ((Resolve-ODSDesktopContextSize 65536) -eq 16384 -and (Resolve-ODSDesktopContextSize 8192) -eq 8192) 'the CPU route caps context at 16K tokens'
    Check ((Get-Failure { Resolve-ODSDesktopContextSize 2048 }) -match 'at least 4096') 'a context below 4096 tokens is refused'

    # --- What the model step may still download (the #8001 bug class) ----------
    $store = Join-Path $fixture 'models'
    $null = New-Item -ItemType Directory -Path $store
    $config = [pscustomobject]@{ ModelSizeMB = 4; GgufSha256 = ('AB' * 32) }
    $fresh = [pscustomobject]@{ GgufFile = 'Model-Q4_K_M.gguf'; Carried = $false }
    Check ((Get-ODSDesktopModelDownloadBytes $fresh $config $store) -eq 4MB) 'a model not downloaded yet needs its full size'
    [IO.File]::WriteAllBytes((Join-Path $store 'Model-Q4_K_M.gguf.partial'), [byte[]]::new(1MB))
    Check ((Get-ODSDesktopModelDownloadBytes $fresh $config $store) -eq 3MB) 'a resumable partial download needs only the rest'
    Remove-Item -LiteralPath (Join-Path $store 'Model-Q4_K_M.gguf.partial')
    $model = Join-Path $store 'Model-Q4_K_M.gguf'
    [IO.File]::WriteAllBytes($model, [byte[]]::new(4MB))
    Check ((Get-ODSDesktopModelDownloadBytes $fresh $config $store) -eq 4MB) 'a model file without a verified stamp may be replaced, so it needs space for a fresh copy'
    Write-ODSDesktopModelStamp "$model.ods-verified.json" (New-ODSDesktopModelStamp (Get-Item -LiteralPath $model) ('ab' * 32))
    Check ((Get-ODSDesktopModelDownloadBytes $fresh $config $store) -eq 0) 'a verified model needs no space'
    [IO.File]::WriteAllBytes($model, [byte[]]::new(4MB - 1))
    Check ((Get-ODSDesktopModelDownloadBytes $fresh $config $store) -eq 4MB) 'a model changed since it was verified needs space for a fresh copy'
    Check ((Get-ODSDesktopModelDownloadBytes ([pscustomobject]@{ GgufFile = 'Other.gguf'; Carried = $true }) $config $store) -eq 0) 'a rerun that keeps the owner''s own model downloads nothing'
    $catalogSize = [pscustomobject]@{ ModelSizeMB = 2741; GgufSha256 = ('AB' * 32) }
    Check ((Get-ODSDesktopModelDownloadBytes ([pscustomobject]@{ GgufFile = 'Qwen3.5-4B-Q4_K_M.gguf'; Carried = $false }) $catalogSize $store) -eq 2741MB) 'a real catalog model over 2 GB is sized without overflowing'

    # --- CPU options through main's builder ------------------------------------
    $runtime = [pscustomobject]@{ ExecutablePath = 'C:\ODS\llama.cpp\b9014-win-vulkan-x64\llama-server.exe'; ReleaseTag = 'b9014'; ZipSha256 = ('c' * 64) }
    $options = New-ODSDesktopLlamaOptions $runtime ([pscustomobject]@{ Device = $null })
    Check ($options.Device -ceq 'none' -and $options.NGpuLayers -ceq '0' -and -not $options.Contains('Backend')) 'Desktop runs the Vulkan build on the explicit CPU route'
    $plan = [pscustomobject]@{ ExecutablePath = $runtime.ExecutablePath; Port = 18080; ModelsDir = 'C:\ODS\models'; ContextSize = 16384; GgufFile = 'Qwen3.5-4B-Q4_K_M.gguf' }
    $arguments = ' ' + ((New-ODSNativeLlamaLaunchArguments $plan ([pscustomobject]$options)) -join ' ') + ' '
    Check ($arguments.Contains(' --device none ') -and $arguments.Contains(' --n-gpu-layers 0 ') -and $arguments.Contains(' --host 127.0.0.1 ') -and
        $arguments.Contains(' --api-key-file ')) 'the launch arguments are loopback, keyed and CPU-only'

    # --- Who may own the model runtime -----------------------------------------
    $owner = Join-Path $fixture 'runtime-owner.json'
    Assert-ODSDesktopRuntimeTakeover ([pscustomobject]@{ Kind = 'none' }) $owner
    Check $true 'a PC with no ODS model runtime is free to use'
    Check ((Get-Failure { Assert-ODSDesktopRuntimeTakeover ([pscustomobject]@{ Kind = 'lemonade' }) $owner }) -match 'Lemonade.*nothing was changed') 'a WSL Portal on Lemonade is never replaced'
    $portal = [pscustomobject]@{ Kind = 'llama-server'; Launch = [pscustomobject]@{ TaskName = 'ODSLlamaServerRuntime-S-1'
        Plan = [pscustomobject]@{ WslDistro = 'Ubuntu-24.04'; WslInstallDir = '/home/a/ods' } } }
    Check ((Get-Failure { Assert-ODSDesktopRuntimeTakeover $portal $owner }) -match 'Portal in WSL \(Ubuntu-24\.04\)') 'a WSL Portal''s llama.cpp runtime is never taken over'
    $unknown = [pscustomobject]@{ Kind = 'llama-server'; Launch = [pscustomobject]@{ TaskName = 'ODSLlamaServerRuntime-S-1'; Plan = [pscustomobject]@{ Port = 8080 } } }
    Check ((Get-Failure { Assert-ODSDesktopRuntimeTakeover $unknown $owner }) -match 'Another ODS install') 'a runtime Desktop cannot prove it made is left alone'
    [IO.File]::WriteAllText($owner, '{"Profile":"windows-desktop"}')
    Assert-ODSDesktopRuntimeTakeover $unknown $owner
    Check $true 'Desktop''s own runtime is updated on a rerun'

    # --- dashboard-api environment ---------------------------------------------
    $paths = Get-ODSDesktopPaths (Join-Path $fixture ('App [QA] Jos' + [char]0x00E9 + '\desktop'))
    $key = 'k' * 64
    $environment = New-ODSDesktopApiEnvironment $paths 3101 18080 'C:\ODS\portal-runtime' $key
    Check ($environment.ODS_RUNTIME_PROFILE -ceq 'windows-desktop' -and $environment.ODS_NATIVE_LLM_URL -ceq 'http://127.0.0.1:18080' -and
        $environment.DASHBOARD_API_PORT -ceq '3101' -and $environment.ODS_DASHBOARD_DIST -ceq $paths.Dist -and
        $environment.ODS_EXTENSIONS_DIR -ceq (Join-Path $paths.InstallDir 'extensions\services')) 'the app environment names the profile, the loopback model and every folder'
    Check ((Get-Failure { New-ODSDesktopApiEnvironment $paths 3101 18080 'C:\r' 'short' }) -match 'key is invalid') 'a malformed API key is refused'
    $text = ConvertTo-ODSDesktopEnvText $environment
    $back = ConvertFrom-ODSDesktopEnvText $text
    Check (($back.Keys -join ',') -ceq ($environment.Keys -join ',') -and $back.ODS_DATA_DIR -ceq $paths.Data -and $back.ODS_DATA_DIR.Contains([char]0x00E9)) 'the saved environment round-trips, including a non-ASCII profile folder'
    Check ((Get-Failure { ConvertTo-ODSDesktopEnvText ([ordered]@{ ODS_X = "a`nODS_RUNTIME_PROFILE=other" }) }) -match 'cannot be saved') 'a value cannot smuggle in a second setting'
    Check ((Get-Failure { ConvertFrom-ODSDesktopEnvText "no-equals-sign`n" }) -match 'damaged') 'a damaged environment file stops startup'

    # --- Shipped app files -----------------------------------------------------
    $api = Join-Path $fixture 'dashboard-api'
    foreach ($relative in 'main.py', 'config.py', 'requirements.lock', 'performance_evidence.json', 'Dockerfile', 'README.md',
        'routers\local_chat.py', 'routers\__pycache__\local_chat.cpython-313.pyc', 'tests\test_main.py') {
        $path = Join-Path $api $relative
        $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path)
        [IO.File]::WriteAllText($path, $relative)
    }
    $files = @(Get-ODSDesktopAppFiles $api)
    Check (($files -join ',') -ceq 'config.py,main.py,performance_evidence.json,requirements.lock,routers\local_chat.py') 'the app ships what the dashboard-api image ships: modules, routers, evidence and the lock'
    $real = @(Get-ODSDesktopAppFiles (Join-Path $root 'extensions\services\dashboard-api'))
    Check ($real -contains 'main.py' -and $real -contains 'routers\local_chat.py' -and $real -contains 'native_front.py' -and
        -not ($real | Where-Object { $_ -like 'tests*' })) 'the real dashboard-api ships its Desktop modules and none of its tests'

    # --- Whole-folder publishing ------------------------------------------------
    $target = Join-Path $fixture 'published'
    Publish-ODSDesktopFolder $target { param($stage) [IO.File]::WriteAllText((Join-Path $stage 'v'), '1') }
    Publish-ODSDesktopFolder $target { param($stage) [IO.File]::WriteAllText((Join-Path $stage 'v'), '2') }
    Check ([IO.File]::ReadAllText((Join-Path $target 'v')) -ceq '2') 'an update replaces the folder'
    $failure = Get-Failure { Publish-ODSDesktopFolder $target { param($stage) [IO.File]::WriteAllText((Join-Path $stage 'v'), '3'); throw 'disk full' } }
    $left = @(Get-ChildItem -LiteralPath $fixture -Force | Where-Object { $_.Name -like '.ods-staging-*' -or $_.Name -like 'published.previous-*' })
    Check ($failure -ceq 'disk full' -and [IO.File]::ReadAllText((Join-Path $target 'v')) -ceq '2' -and $left.Count -eq 0) 'a failed update keeps the previous folder whole and leaves no staging behind'

    $source = Join-Path $fixture 'ods'
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $source 'config'), (Join-Path $source 'extensions\services\llama-server'), (Join-Path $source 'extensions\services\empty')
    Move-Item -LiteralPath $api -Destination (Join-Path $source 'extensions\services\dashboard-api')
    [IO.File]::WriteAllText((Join-Path $source 'config\model-library.json'), '{}')
    [IO.File]::WriteAllText((Join-Path $source 'manifest.json'), '{}')
    [IO.File]::WriteAllText((Join-Path $source 'extensions\services\llama-server\manifest.yaml'), 'service: {}')
    [IO.File]::WriteAllText((Join-Path $source 'extensions\services\llama-server\compose.yaml'), 'services: {}')
    Publish-ODSDesktopApp $source $paths
    Check ((Test-Path -LiteralPath (Join-Path $paths.AppSource 'routers\local_chat.py')) -and
        (Test-Path -LiteralPath (Join-Path $paths.InstallDir 'config\model-library.json')) -and
        (Test-Path -LiteralPath (Join-Path $paths.InstallDir 'manifest.json')) -and
        (Test-Path -LiteralPath (Join-Path $paths.InstallDir 'extensions\services\llama-server\manifest.yaml')) -and
        -not (Test-Path -LiteralPath (Join-Path $paths.InstallDir 'extensions\services\llama-server\compose.yaml')) -and
        -not (Test-Path -LiteralPath (Join-Path $paths.AppSource 'tests'))) 'the app is published with config and service manifests, without compose files or tests'

    $dist = Join-Path $fixture 'dist [build]'
    Check ((Get-Failure { Publish-ODSDesktopDist $dist $paths }) -match 'npm run build') 'a missing dashboard build explains how to make one'
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $dist 'assets')
    [IO.File]::WriteAllText((Join-Path $dist 'index.html'), '<!doctype html>')
    [IO.File]::WriteAllText((Join-Path $dist 'assets\app-1.js'), 'js')
    Publish-ODSDesktopDist $dist $paths
    Check ((Test-Path -LiteralPath (Join-Path $paths.Dist 'index.html')) -and (Test-Path -LiteralPath (Join-Path $paths.Dist 'assets\app-1.js'))) 'the dashboard build is copied from a folder with brackets in its name'

    # --- Ports ------------------------------------------------------------------
    $script:busy = @{ 3101 = 'node' }
    function Get-ODSPortalPortOwner([int]$Port) { return $script:busy[$Port] }
    function Test-ODSPortalPortBindable([int]$Port) { return $true }
    Check ((Select-ODSDesktopApiPort 0) -eq 13101) 'a new install skips a port another program uses'
    function Test-ODSPortalPortBindable([int]$Port) { return $Port -ne 13101 }
    Check ((Select-ODSDesktopApiPort 13101 13101) -eq 13101) 'a rerun keeps its port while the stopped app''s socket still blocks binding'
    Check ((Get-Failure { Select-ODSDesktopApiPort 3101 3101 }) -match '3101 \(node\)') 'a rerun never reuses its port once another program listens there'
    function Test-ODSPortalPortBindable([int]$Port) { return $true }
    Check ((Get-Failure { Select-ODSDesktopApiPort 3101 }) -match '3101 \(node\).*-Port') 'a saved port now taken by another program is named, with the way out'

    # --- Commands and shortcuts -------------------------------------------------
    Check ((Format-ODSDesktopCommand 'C:\Users\A B\ods\installers\windows-desktop.ps1' '-Repair') -ceq
        'powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\A B\ods\installers\windows-desktop.ps1" -Repair') 'printed commands run under the default execution policy'
    Check ((Get-ODSDesktopApiLauncherArguments $paths) -ceq ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $paths.LauncherScript + '"')) 'the sign-in task runs the copied launcher hidden and policy-independent'
    $arguments = New-ODSDesktopApiArguments $paths.AppSource 3101
    Check ($arguments -ceq ('-m uvicorn --app-dir "' + $paths.AppSource + '" main:app --host 127.0.0.1 --port 3101 --log-level warning --no-access-log')) 'uvicorn binds 127.0.0.1 with --app-dir (the ._pth makes Python isolated)'
    Check ((Get-Failure { New-ODSDesktopApiArguments 'C:\a"b' 3101 }) -match 'cannot be passed') 'a path that would break quoting is refused'
    $desktop = Join-Path $fixture 'Desktop'; $programs = Join-Path $fixture 'Programs'
    $null = New-Item -ItemType Directory -Path $desktop, $programs
    $page = New-ODSDesktopStartPage 3101
    Check ($page.Contains("const app = 'http://localhost:3101/';") -and -not $page.Contains('__PORT__') -and
        -not ([Text.Encoding]::UTF8.GetBytes($page) | Where-Object { $_ -gt 127 })) 'the start page opens this install''s port and is plain ASCII'
    Check ($page.Contains("fetch(app + 'health', { mode: 'no-cors'") -and $page.Contains('location.replace(app)') -and $page.Contains('120000')) 'the start page waits for the app, switches to it, and explains after two minutes'
    Check ((Get-Failure { New-ODSDesktopStartPage 0 }) -match 'between 1 and 65535') 'a start page needs a real port'
    $icon = Join-Path $fixture 'source.ico'
    [IO.File]::WriteAllBytes($icon, [byte[]]::new(64))
    $edge = Join-Path ([Environment]::SystemDirectory) 'notepad.exe'
    $links = @(Publish-ODSDesktopShortcut -Paths $paths -Port 3101 -IconSource $icon -Edge $edge -Folders @($desktop, $programs))
    $shell = New-Object -ComObject WScript.Shell
    $made = $shell.CreateShortcut($links[0])
    $target = [string]$made.TargetPath; $arguments = [string]$made.Arguments
    $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    Check ($links.Count -eq 2 -and $target -ieq $edge -and $arguments -ceq ('--app=' + ([Uri]::new($paths.StartPage)).AbsoluteUri) -and
        $arguments.Contains('%C3%A9')) 'the ODS icon opens the start page in an Edge app window, from a non-ASCII profile folder'
    Check ([IO.File]::ReadAllText($paths.StartPage) -ceq $page -and (Test-Path -LiteralPath $paths.Icon)) 'the start page and icon live in the Desktop folder, so uninstall removes them'
    foreach ($link in $links) { Remove-Item -LiteralPath $link }
    $links = @(Publish-ODSDesktopShortcut -Paths $paths -Port 3101 -Edge '' -Folders @($desktop, $programs))
    Check ($links.Count -eq 2 -and [IO.File]::ReadAllText($links[0]) -ceq "[InternetShortcut]`r`nURL=http://localhost:3101/`r`n") 'without Edge the owner still gets a link that opens the default browser'

    # --- Static rules over every Desktop script -------------------------------
    $scripts = @((Join-Path $root 'installers/windows-desktop.ps1'), (Join-Path $lib 'desktop-runtime.ps1'), (Join-Path $lib 'desktop-api-launcher.ps1'))
    $pathCommands = 'Test-Path', 'Get-Item', 'Get-ChildItem', 'Remove-Item', 'Move-Item', 'Copy-Item', 'Get-Content', 'Set-Content', 'Add-Content'
    foreach ($file in $scripts) {
        $tokens = $null; $errors = $null
        $ast = [Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors)
        $name = Split-Path -Leaf $file
        Check ($errors.Count -eq 0) "$name parses without errors"
        $wildcard = @($ast.FindAll({ param($node)
            $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -in $pathCommands -and
            -not ($node.CommandElements | Where-Object { $_ -is [Management.Automation.Language.CommandParameterAst] -and $_.ParameterName -eq 'LiteralPath' })
        }, $true))
        Check ($wildcard.Count -eq 0) "$name uses -LiteralPath for every path cmdlet"
        Check (-not ([IO.File]::ReadAllBytes($file) | Where-Object { $_ -gt 127 })) "$name is ASCII-only for Windows PowerShell 5.1"
    }
    $x86 = Join-Path $env:SystemRoot 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'
    if (Test-Path -LiteralPath $x86 -PathType Leaf) {
        # Setup itself, from the Start menu's "Windows PowerShell (x86)": it
        # must stop at its first check, naming the right window.
        $output = (& $x86 -NoProfile -ExecutionPolicy Bypass -File $scripts[0] -NonInteractive 2>&1 | Out-String)
        Check ($LASTEXITCODE -eq 1 -and $output -match 'Windows PowerShell \(x86\)' -and $output -match 'the one without x86') 'setup in 32-bit PowerShell stops and names the 64-bit window to use'
    }
    $entry = [IO.File]::ReadAllText($scripts[0])
    Check ($entry -match 'Test-ODSDesktopElevated\)\s*\{\s*throw') 'setup refuses to run elevated, so no ODS state is ever owned by Administrators'
    $printed = @([regex]::Matches($entry, 'powershell[^\r\n]*-File') | Where-Object { $_.Value -notmatch '-ExecutionPolicy Bypass' })
    Check ($printed.Count -eq 0) 'no command in setup omits -ExecutionPolicy Bypass'
    $tokens = $null; $errors = $null
    $entryAst = [Management.Automation.Language.Parser]::ParseFile($scripts[0], [ref]$tokens, [ref]$errors)
    $launch = ($entryAst.FindAll({ param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] -and
        [string]$node.Left -ceq '$script:ODSDesktopLaunchScript' }, $true) | Select-Object -First 1).Right.Extent.Text
    $copied = ($entryAst.FindAll({ param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] -and
        [string]$node.Left -ceq '$script:ODSDesktopLauncherLibraries' }, $true) | Select-Object -First 1).Right.Extent.Text
    $sourced = @([regex]::Matches($launch, "'([a-z-]+\.ps1)'") | ForEach-Object { $_.Groups[1].Value })
    Check ($sourced.Count -eq 4 -and -not ($sourced | Where-Object { -not $copied.Contains("'$_'") }) -and $copied.Contains("'private-file.ps1'")) 'the sign-in launcher loads only libraries setup copies beside it'
} finally {
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
Microsoft.PowerShell.Utility\Write-Host "All $script:checks Windows Desktop runtime checks passed."
