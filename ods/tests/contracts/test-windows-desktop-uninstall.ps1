# Windows Desktop uninstall: the Apps & Features entry, stopping only what
# Desktop started (real ping.exe stand-ins), removing only shortcuts that
# still match setup's record, and the owner's keep/remove choice for data,
# with what is kept named and sized. A WSL Portal's runtime and a model
# runtime Desktop cannot prove it made are never touched. Scheduled-task
# cmdlets are fixtures; the registry entry goes under a throwaway HKCU key.
# Runs under Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$lib = Join-Path $root 'installers/windows/lib'
foreach ($name in 'native-llama-runtime.ps1', 'desktop-runtime.ps1', 'desktop-api-launcher.ps1', 'app-shortcut.ps1', 'desktop-uninstall.ps1') { . (Join-Path $lib $name) }
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}

# Scheduled tasks: fixtures that record what uninstall asked for.
$script:tasks = @{}
$script:taskCalls = @()
function Get-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) if ($script:tasks.ContainsKey($TaskName)) { [pscustomobject]@{ TaskName = $TaskName; State = 'Ready' } } }
function Disable-ScheduledTask { param($TaskName, $TaskPath) $script:taskCalls += "disable $TaskName" }
$script:intentProbe = ''
$script:intentAtStop = @()
function Stop-ScheduledTask {
    param($TaskName, $TaskPath)
    $script:taskCalls += "stop $TaskName"
    if ($script:intentProbe -and (Test-Path -LiteralPath $script:intentProbe)) { $script:intentAtStop += (Read-ODSNativeLlamaJson $script:intentProbe).State }
}
function Unregister-ScheduledTask { param($TaskName, $TaskPath, $Confirm) $script:taskCalls += "unregister $TaskName"; $script:tasks.Remove($TaskName) }

$ping = Join-Path ([Environment]::SystemDirectory) 'PING.EXE'
$started = @()
function Start-Recorded([string]$Path) {
    $process = Start-Process -FilePath $ping -ArgumentList '-n 120 127.0.0.1' -WindowStyle Hidden -PassThru
    $null = $process.Handle
    $script:started += $process
    # $ping, not $process.Path: Windows can report that empty right after start.
    $identity = [pscustomobject]@{ ProcessId = $process.Id; CreationDate = $process.StartTime; ExecutablePath = $ping; ExitedAt = $null }
    Write-ODSPortalProcessOwnership $Path ([pscustomobject]@{ ExecutablePath = $ping; Port = 1 }) @($identity)
    return $process
}

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-desktop-uninstall-' + [guid]::NewGuid().ToString('N'))
$registry = 'HKCU:\Software\ODS-Contract-' + [guid]::NewGuid().ToString('N') + '\Uninstall'
function New-Install([string]$Name, [hashtable]$PlanExtra = @{}) {
    # A Desktop install as setup leaves it, under $fixture\$Name.
    $base = Join-Path $fixture $Name
    $paths = Get-ODSDesktopPaths (Join-Path $base 'ODS\desktop')
    foreach ($folder in $paths.App, $paths.Python, $paths.Launcher, $paths.Dist, $paths.Logs, $paths.Data) {
        $null = New-Item -ItemType Directory -Force -Path $folder
        [IO.File]::WriteAllBytes((Join-Path $folder 'file.bin'), [byte[]]::new(4096))
    }
    $runtimeDir = Join-Path $base 'ODS\lemonade\portal-runtime'
    $models = Join-Path $base 'ODS\lemonade\models'
    $engine = Join-Path $base 'ODS\llama.cpp\b9014-win-vulkan-x64'
    $null = New-Item -ItemType Directory -Force -Path $runtimeDir, $models, $engine
    [IO.File]::WriteAllBytes((Join-Path $models 'Model-Q4_K_M.gguf'), [byte[]]::new(3MB))
    [IO.File]::WriteAllText((Join-Path $models 'Model-Q4_K_M.gguf.ods-verified.json'), '{}')
    [IO.File]::WriteAllBytes((Join-Path $models 'Portal-model.gguf'), [byte[]]::new(1024))
    [IO.File]::WriteAllText((Join-Path $engine 'llama-server.exe'), 'fixture')
    [IO.File]::WriteAllBytes((Join-Path $base 'ODS\ods-app.ico'), [byte[]]::new(512))
    $plan = @{ ExecutablePath = (Join-Path $engine 'llama-server.exe'); Port = 18080; ModelsDir = $models; ContextSize = 16384; GgufFile = 'Model-Q4_K_M.gguf' }
    foreach ($key in $PlanExtra.Keys) { $plan[$key] = $PlanExtra[$key] }
    [IO.File]::WriteAllText((Join-Path $runtimeDir 'runtime.json'), ($plan | ConvertTo-Json -Compress))
    [IO.File]::WriteAllText($paths.RuntimeOwner, (@{ Task = 'ODSLlamaServerRuntime-TEST'; RuntimeDir = $runtimeDir; Profile = 'windows-desktop' } | ConvertTo-Json -Compress))
    $script:tasks = @{ 'ODSDesktopApi-TEST' = $true; 'ODSLlamaServerRuntime-TEST' = $true }
    $script:taskCalls = @()
    return [pscustomobject]@{ Base = $base; Paths = $paths; RuntimeDir = $runtimeDir; Models = $models; Engine = $engine }
}
function Invoke-Case($Install, [hashtable]$Options) {
    $arguments = @{ Paths = $Install.Paths; RegistryRoot = $registry; ApiTaskName = 'ODSDesktopApi-TEST' }
    foreach ($key in $Options.Keys) { $arguments[$key] = $Options[$key] }
    return (Invoke-ODSDesktopUninstall @arguments)
}

$null = New-Item -ItemType Directory -Path $fixture
$profileHome = $env:USERPROFILE
try {
    $env:USERPROFILE = Join-Path $fixture 'home'

    # --- Apps & Features entry ---------------------------------------------------
    $install = New-Install 'entry'
    $key = Register-ODSDesktopUninstallEntry -Paths $install.Paths -Version '3.0.0' -Bytes (3GB + 512KB) -Root $registry
    $entry = Get-ItemProperty -LiteralPath $key
    Check ($entry.DisplayName -ceq 'ODS Desktop (preview)' -and $entry.DisplayVersion -ceq '3.0.0' -and $entry.InstallLocation -ceq $install.Paths.Root) 'Apps & Features lists ODS Desktop with its version and folder'
    Check ($entry.UninstallString -ceq ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $install.Paths.Root 'uninstall.ps1') + '"')) 'the uninstall command runs the copied uninstaller under any execution policy'
    Check ($entry.QuietUninstallString -like '* -NonInteractive -KeepData' -and $entry.NoModify -eq 1 -and $entry.NoRepair -eq 1) 'a quiet uninstall keeps the owner''s data; Modify and Repair are not offered'
    Check ($entry.EstimatedSize -eq 3146240) 'the size shown includes the model, in KB'

    # --- Keep data: the app goes, the owner's data and model stay --------------
    $app = Start-Recorded $install.Paths.Ownership
    $model = Start-Recorded (Join-Path $install.RuntimeDir 'process-ownership.json')
    $desktopFolder = Join-Path $install.Base 'Desktop'
    $null = New-Item -ItemType Directory -Force -Path $desktopFolder
    $link = Join-Path $desktopFolder 'ODS.url'; $edited = Join-Path $install.Base 'ODS.url'
    foreach ($path in $link, $edited) { [IO.File]::WriteAllText($path, "[InternetShortcut]`r`nURL=http://localhost:3101/`r`n") }
    $shell = New-Object -ComObject WScript.Shell
    $lnk = Join-Path $desktopFolder 'ODS.lnk'
    $made = $shell.CreateShortcut($lnk); $made.TargetPath = $ping; $made.Arguments = '--app=http://localhost:3101/'; $made.Save()
    $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    $records = @(Get-ODSPortalShortcutRecord -Paths @($link, $edited, $lnk))
    [IO.File]::WriteAllText((Join-Path $install.Paths.Root 'shortcuts.json'), (ConvertTo-Json -InputObject $records -Depth 3 -Compress))
    [IO.File]::WriteAllText($edited, "[InternetShortcut]`r`nURL=http://example.com/`r`n")

    $script:intentProbe = $install.Paths.Intent
    $result = Invoke-Case $install @{ KeepData = $true }
    $script:intentProbe = ''
    Check ($app.WaitForExit(10000) -and $model.WaitForExit(10000)) 'the app and the model server Desktop started are stopped'
    Check (($script:taskCalls -join ',') -ceq 'disable ODSDesktopApi-TEST,stop ODSDesktopApi-TEST,unregister ODSDesktopApi-TEST,disable ODSLlamaServerRuntime-TEST,stop ODSLlamaServerRuntime-TEST,unregister ODSLlamaServerRuntime-TEST') 'both sign-in tasks are disarmed, stopped and removed'
    Check (($script:intentAtStop -join ',') -ceq 'stopped,stopped') 'the app''s intent already read stopped when its task was stopped'
    Check (-not (Test-Path -LiteralPath $link) -and -not (Test-Path -LiteralPath $lnk) -and (Test-Path -LiteralPath $edited)) 'shortcuts setup made are removed; one the owner changed stays'
    Check (-not (Test-Path -LiteralPath $key)) 'the Apps & Features entry is removed'
    $kept = @($result.Kept | ForEach-Object { $_.Path })
    Check ($kept -contains $install.Paths.Data -and $kept -contains (Join-Path $install.Models 'Model-Q4_K_M.gguf') -and $kept -contains $install.Engine) 'keeping data keeps the app data, the model and its engine, and says so'
    Check ((@($result.Kept | Where-Object { $_.Path -like '*Model-Q4_K_M.gguf' })[0].Bytes) -eq 3MB) 'each kept path is reported with its size'
    $left = @(Get-ChildItem -LiteralPath $install.Paths.Root -Force | ForEach-Object { $_.Name })
    Check (($left -join ',') -ceq 'data' -and -not (Test-Path -LiteralPath $install.RuntimeDir)) 'only the data folder is left in the Desktop folder; the runtime plan and key are gone'
    Check (Test-Path -LiteralPath (Join-Path $install.Models 'Portal-model.gguf')) 'a model Desktop did not download is never touched'
    Check (Test-Path -LiteralPath (Join-Path $install.Base 'ODS\ods-app.ico')) 'the shared shortcut icon stays while other ODS files remain'

    # --- Remove everything ------------------------------------------------------
    $install = New-Install 'remove'
    $null = Register-ODSDesktopUninstallEntry -Paths $install.Paths -Version '3.0.0' -Bytes 1 -Root $registry
    [IO.File]::Delete((Join-Path $install.Models 'Portal-model.gguf'))
    $result = Invoke-Case $install @{ RemoveData = $true }
    Check (-not (Test-Path -LiteralPath $install.Paths.Root) -and -not (Test-Path -LiteralPath (Join-Path $install.Base 'ODS\lemonade')) -and
        -not (Test-Path -LiteralPath (Join-Path $install.Base 'ODS\llama.cpp')) -and $result.Kept.Count -eq 0) 'remove everything leaves no ODS Desktop folder, model, engine or empty parent behind'
    Check (-not (Test-Path -LiteralPath (Join-Path $install.Base 'ODS'))) 'with nothing else of ODS left, the shortcut icon and the ODS folder go too'
    $install = New-Install 'remove-desktop-only'
    [IO.File]::Delete((Join-Path $install.Models 'Portal-model.gguf'))
    [IO.File]::Delete((Join-Path $install.Base 'ODS\ods-app.ico'))
    $result = Invoke-Case $install @{ RemoveData = $true }
    Check (-not (Test-Path -LiteralPath (Join-Path $install.Base 'ODS'))) 'on a PC without the shared icon, no empty ODS folder is left behind'

    # --- The owner is asked, and shown what is at stake -------------------------
    $install = New-Install 'ask'
    $script:summary = ''
    $result = Invoke-Case $install @{ Ask = { param($Summary) $script:summary = $Summary; $false } }
    Check ($script:summary -match [regex]::Escape((Join-Path $install.Models 'Model-Q4_K_M.gguf')) -and $script:summary -match '\(3 MB\)' -and
        $script:summary -match [regex]::Escape($install.Paths.Data)) 'the question names each data path and its size'
    Check (Test-Path -LiteralPath (Join-Path $install.Models 'Model-Q4_K_M.gguf')) 'answering no keeps the model'
    $install = New-Install 'quiet'
    $result = Invoke-Case $install @{ NonInteractive = $true; Ask = { throw 'asked' } }
    Check ((Test-Path -LiteralPath $install.Paths.Data) -and (Test-Path -LiteralPath (Join-Path $install.Models 'Model-Q4_K_M.gguf'))) 'a non-interactive uninstall never asks and keeps the data'

    # --- What uninstall never touches -----------------------------------------
    $install = New-Install 'portal' @{ WslDistro = 'Ubuntu-24.04'; WslInstallDir = '/home/a/ods' }
    $result = Invoke-Case $install @{ RemoveData = $true }
    Check (-not $result.StoppedModel -and $script:tasks.ContainsKey('ODSLlamaServerRuntime-TEST') -and (Test-Path -LiteralPath (Join-Path $install.RuntimeDir 'runtime.json')) -and
        (Test-Path -LiteralPath (Join-Path $install.Models 'Model-Q4_K_M.gguf'))) 'a runtime a WSL Portal has since bound, and its model, are left alone'
    $install = New-Install 'docker'
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $env:USERPROFILE 'ods')
    [IO.File]::WriteAllText((Join-Path $env:USERPROFILE 'ods\ods.ps1'), '# fixture')
    $result = Invoke-Case $install @{ RemoveData = $true }
    Check ((Test-Path -LiteralPath $install.Engine) -and -not (Test-Path -LiteralPath (Join-Path $install.Models 'Model-Q4_K_M.gguf'))) 'with a Docker-based ODS on this PC the shared llama.cpp build stays'
    Remove-Item -LiteralPath (Join-Path $env:USERPROFILE 'ods') -Recurse -Force
    $install = New-Install 'both'
    Check ((Get-Failure { Invoke-Case $install @{ KeepData = $true; RemoveData = $true } }) -match 'not both' -and $script:taskCalls.Count -eq 0) 'contradictory choices stop before anything changes'
    [IO.File]::WriteAllText($install.Paths.RuntimeOwner, '{"Task":"ODSLlamaServerRuntime-TEST"}')
    Check ((Get-Failure { Invoke-Case $install @{ KeepData = $true } }) -match 'nothing was changed' -and $script:taskCalls.Count -eq 0) 'an owner record without its runtime folder changes nothing'

    # --- Sizes ----------------------------------------------------------------
    $sized = Join-Path $fixture 'sized'
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $sized 'inner')
    [IO.File]::WriteAllBytes((Join-Path $sized 'inner\a.bin'), [byte[]]::new(2048))
    $target = Join-Path $fixture 'link-target'
    $null = New-Item -ItemType Directory -Force -Path $target
    [IO.File]::WriteAllBytes((Join-Path $target 'big.bin'), [byte[]]::new(1MB))
    $null = New-Item -ItemType Junction -Path (Join-Path $sized 'link') -Value $target
    Check ((Get-ODSDesktopFolderBytes $sized) -eq 2048) 'sizes never follow a link out of the folder'
    Check ((Format-ODSDesktopBytes 3MB) -ceq '3 MB' -and (Format-ODSDesktopBytes 100) -ceq 'under 1 MB' -and (Format-ODSDesktopBytes ([long](2.75 * 1GB))) -match '^2[.,]8 GB$') 'sizes read in MB or GB'

    # --- Static rules -----------------------------------------------------------
    $file = Join-Path $lib 'desktop-uninstall.ps1'
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors)
    $pathCommands = 'Test-Path', 'Get-Item', 'Get-ChildItem', 'Remove-Item', 'Move-Item', 'Copy-Item', 'Get-Content', 'Set-Content', 'Add-Content', 'Get-ItemProperty'
    $wildcard = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -in $pathCommands -and
        -not ($node.CommandElements | Where-Object { $_ -is [Management.Automation.Language.CommandParameterAst] -and $_.ParameterName -eq 'LiteralPath' })
    }, $true))
    Check ($errors.Count -eq 0 -and $wildcard.Count -eq 0) 'desktop-uninstall.ps1 parses and uses -LiteralPath for every path cmdlet'
    Check (-not ([IO.File]::ReadAllBytes($file) | Where-Object { $_ -gt 127 })) 'desktop-uninstall.ps1 is ASCII-only for Windows PowerShell 5.1'
    $entryAst = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows-desktop.ps1'), [ref]$tokens, [ref]$errors)
    $text = { param($Name) ($entryAst.FindAll({ param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] -and
        [string]$node.Left -ceq $Name }, $true) | Select-Object -First 1).Right.Extent.Text }
    $uninstaller = & $text '$script:ODSDesktopUninstallScript'
    $copied = & $text '$script:ODSDesktopLauncherLibraries'
    $sourced = @([regex]::Matches($uninstaller, "'([a-z-]+\.ps1)'") | ForEach-Object { $_.Groups[1].Value })
    Check ($sourced.Count -eq 5 -and -not ($sourced | Where-Object { -not $copied.Contains("'$_'") })) 'uninstall.ps1 loads only libraries setup copies beside it, so it works after the checkout is gone'
} finally {
    $env:USERPROFILE = $profileHome
    foreach ($process in $started) { if (-not $process.HasExited) { $process.Kill() }; $process.Dispose() }
    $testRoot = Split-Path -Parent $registry
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
    if (Test-Path -LiteralPath $fixture) {
        # Junctions first, so removing the fixture never descends into a target.
        foreach ($link in @(Get-ChildItem -LiteralPath (Join-Path $fixture 'sized') -Force -ErrorAction SilentlyContinue | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })) {
            [IO.Directory]::Delete($link.FullName)
        }
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
Microsoft.PowerShell.Utility\Write-Host "All $script:checks Windows Desktop uninstall checks passed."
