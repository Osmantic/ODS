# ============================================================================
# ODS Windows Desktop -- Apps & Features entry and uninstall
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Make ODS Desktop removable like any Windows program: a per-user
#          Apps & Features entry, and an uninstaller that stops only what
#          Desktop started, removes its tasks and shortcuts, and asks before
#          deleting the owner's data, saying what is kept, where and how big.
# Expects: native-llama-runtime.ps1 (Read-ODSNativeLlamaJson), private-file.ps1,
#          desktop-runtime.ps1 (Get-ODSDesktopPaths), desktop-api-launcher.ps1
#          (Stop-ODSDesktopApi), app-shortcut.ps1 (Remove-ODSPortalShortcut,
#          Get-ODSInternetShortcutUrl). setup copies them beside uninstall.ps1,
#          so uninstall works after the ODS checkout is gone.
# Provides: Get-ODSDesktopUninstallKey, Get-ODSDesktopFolderBytes,
#           Register-ODSDesktopUninstallEntry, Get-ODSDesktopDataPlan,
#           Format-ODSDesktopBytes, Remove-ODSDesktopShortcuts,
#           Stop-ODSDesktopTask, Get-ODSDesktopOwnedRuntime,
#           Get-ODSDesktopOwnedPaths, Invoke-ODSDesktopUninstall
# Modder notes: the model runtime task is touched only when runtime-owner.json
#   says Desktop made it and its plan has no WSL binding. Chats are saved by
#   the ODS window (Microsoft Edge site data for this localhost address), not
#   in these folders; uninstall says so instead of reaching into Edge.
# ============================================================================

$script:ODSDesktopUninstallRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'

function Get-ODSDesktopUninstallKey([string]$Root = $script:ODSDesktopUninstallRoot) { return (Join-Path $Root 'ODSDesktop') }

function Get-ODSDesktopFolderBytes([string]$Path) {
    # Links are not followed: their targets are not ODS's to count or delete.
    if (-not (Test-Path -LiteralPath $Path)) { return [long]0 }
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) { return [long]$item.Length }
    $total = [long]0
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($item.FullName)
    while ($pending.Count) {
        foreach ($child in @(Get-ChildItem -LiteralPath $pending.Pop() -Force)) {
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            if ($child.PSIsContainer) { $pending.Push($child.FullName) } else { $total += $child.Length }
        }
    }
    return $total
}

function Format-ODSDesktopBytes([long]$Bytes) {
    if ($Bytes -ge 1GB) { return ('{0:N1} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -lt 1MB) { return 'under 1 MB' }
    return ('{0:N0} MB' -f [math]::Round($Bytes / 1MB))
}

function Register-ODSDesktopUninstallEntry {
    param([Parameter(Mandatory = $true)]$Paths, [string]$Version, [long]$Bytes, [string]$IconPath = '',
        [string]$Root = $script:ODSDesktopUninstallRoot)
    $key = Get-ODSDesktopUninstallKey $Root
    $null = New-Item -Path $key -Force
    $command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Paths.Root 'uninstall.ps1') + '"'
    $values = [ordered]@{
        DisplayName = 'ODS Desktop (preview)'
        DisplayVersion = $Version
        Publisher = 'ODS'
        InstallLocation = $Paths.Root
        UninstallString = $command
        QuietUninstallString = "$command -NonInteractive -KeepData"
        URLInfoAbout = 'https://github.com/Osmantic/ODS'
    }
    foreach ($name in $values.Keys) { $null = New-ItemProperty -LiteralPath $key -Name $name -Value ([string]$values[$name]) -PropertyType String -Force }
    # Apps & Features shows EstimatedSize in KB; the model is most of it.
    $null = New-ItemProperty -LiteralPath $key -Name 'EstimatedSize' -Value ([int][math]::Ceiling($Bytes / 1KB)) -PropertyType DWord -Force
    foreach ($flag in 'NoModify', 'NoRepair') { $null = New-ItemProperty -LiteralPath $key -Name $flag -Value 1 -PropertyType DWord -Force }
    if ($IconPath) { $null = New-ItemProperty -LiteralPath $key -Name 'DisplayIcon' -Value $IconPath -PropertyType String -Force }
    return $key
}

function Get-ODSDesktopDataPlan($Paths, [string[]]$OwnedPaths, [bool]$RemoveData) {
    <#
    .SYNOPSIS
        Every path uninstall deletes or keeps, with its size. The app,
        Python, launcher and dashboard build are always deleted (setup
        restores them); the app's data and the model follow the owner.
    #>
    $always = @($Paths.App, $Paths.Python, $Paths.Launcher, $Paths.Dist, $Paths.Logs)
    $owned = @($Paths.Data) + @($OwnedPaths)
    $plan = @()
    foreach ($path in $always) { $plan += [pscustomobject]@{ Path = $path; Bytes = (Get-ODSDesktopFolderBytes $path); Remove = $true } }
    foreach ($path in $owned) { $plan += [pscustomobject]@{ Path = $path; Bytes = (Get-ODSDesktopFolderBytes $path); Remove = $RemoveData } }
    return @($plan | Where-Object { Test-Path -LiteralPath $_.Path })
}

function Remove-ODSDesktopShortcuts([string]$RecordPath) {
    # Only shortcuts that still match what setup recorded; an edited or
    # replaced one is the owner's now.
    if (-not (Test-Path -LiteralPath $RecordPath -PathType Leaf)) { return @() }
    $records = @(Read-ODSNativeLlamaJson $RecordPath)
    $removed = @((Remove-ODSPortalShortcut -Records @($records | Where-Object { (Split-Path -Leaf ([string]$_.path)) -ceq 'ODS.lnk' })).removed)
    foreach ($record in @($records | Where-Object { (Split-Path -Leaf ([string]$_.path)) -ceq 'ODS.url' })) {
        $path = [string]$record.path
        if ([IO.Path]::IsPathRooted($path) -and (Test-Path -LiteralPath $path -PathType Leaf) -and (Get-ODSInternetShortcutUrl $path) -ceq [string]$record.url) {
            Remove-Item -LiteralPath $path -Force
            $removed += $path
        }
    }
    return $removed
}

function Stop-ODSDesktopTask([string]$TaskName, [string]$IntentPath, [string]$OwnershipPath) {
    # Intent first, so a sign-in start that is already queued exits at once;
    # then the task's own process, then only the processes the record proves.
    if (Test-Path -LiteralPath (Split-Path -Parent $IntentPath)) { Write-ODSPrivateEnvFile -Path $IntentPath -Content '{"State":"stopped"}' }
    $task = @(Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue)
    if ($task.Count) {
        Disable-ScheduledTask -TaskName $TaskName -TaskPath '\' | Out-Null
        Stop-ScheduledTask -TaskName $TaskName -TaskPath '\'
    }
    $null = Stop-ODSDesktopApi $OwnershipPath
    if ($task.Count) { Unregister-ScheduledTask -TaskName $TaskName -TaskPath '\' -Confirm:$false }
    return [bool]$task.Count
}

function Get-ODSDesktopOwnedRuntime($Paths) {
    # The model runtime Desktop made, as runtime-owner.json records it, or
    # $null (none, or a WSL Portal has since bound it).
    if (-not (Test-Path -LiteralPath $Paths.RuntimeOwner -PathType Leaf)) { return $null }
    $owner = Read-ODSNativeLlamaJson $Paths.RuntimeOwner
    $runtimeDir = [string]$owner.RuntimeDir
    if (-not [IO.Path]::IsPathRooted($runtimeDir)) { throw 'runtime-owner.json does not name the model runtime folder; nothing was changed.' }
    $planPath = Join-Path $runtimeDir 'runtime.json'
    $plan = $null
    if (Test-Path -LiteralPath $planPath -PathType Leaf) {
        $plan = Read-ODSNativeLlamaJson $planPath
        if ($plan.PSObject.Properties['WslDistro'] -and $plan.WslDistro) { return $null }
    }
    return [pscustomobject]@{ TaskName = [string]$owner.Task; RuntimeDir = $runtimeDir; Plan = $plan }
}

function Read-ODSDesktopDataChoice([string]$Summary) {
    Write-Host $Summary
    return (Read-Host 'Also delete your ODS data and the AI model? [y/N]') -match '^(y|yes)$'
}

function Get-ODSDesktopOwnedPaths($Runtime) {
    # The model Desktop downloaded and the llama.cpp build it runs. The build
    # stays when a Docker-based ODS install on this PC may also use it.
    if (-not $Runtime -or -not $Runtime.Plan) { return @() }
    $model = Join-Path ([string]$Runtime.Plan.ModelsDir) ([string]$Runtime.Plan.GgufFile)
    $owned = @($model, "$model.ods-verified.json")
    if (-not (Test-Path -LiteralPath (Join-Path $env:USERPROFILE 'ods\ods.ps1'))) {
        $owned += Split-Path -Parent ([string]$Runtime.Plan.ExecutablePath)
    }
    return $owned
}

function Resolve-ODSDesktopDataChoice($Paths, [string[]]$OwnedPaths, [bool]$KeepData, [bool]$RemoveData, [bool]$NonInteractive, [scriptblock]$Ask) {
    if ($KeepData -and $RemoveData) { throw 'Choose -KeepData or -RemoveData, not both.' }
    if ($KeepData -or $RemoveData) { return $RemoveData }
    if ($NonInteractive) { return $false }
    $data = @(Get-ODSDesktopDataPlan $Paths $OwnedPaths $true | Where-Object { $_.Path -eq $Paths.Data -or $_.Path -in $OwnedPaths })
    $lines = @($data | ForEach-Object { "           $($_.Path) ($(Format-ODSDesktopBytes $_.Bytes))" })
    return [bool](& $Ask ("         ODS keeps these unless you choose to delete them:`n" + ($lines -join "`n")))
}

function Remove-ODSDesktopLeftovers($Paths, [string[]]$Folders) {
    # Folders the removal left empty (the model store and its parents), then
    # everything in the Desktop folder except kept data.
    foreach ($folder in @($Folders | Sort-Object -Unique -Descending)) {
        if ((Test-Path -LiteralPath $folder -PathType Container) -and -not @(Get-ChildItem -LiteralPath $folder -Force).Count) {
            Remove-Item -LiteralPath $folder -Force
        }
    }
    if (-not (Test-Path -LiteralPath $Paths.Root)) { return }
    foreach ($item in @(Get-ChildItem -LiteralPath $Paths.Root -Force)) {
        if ($item.FullName -ne $Paths.Data) { Remove-Item -LiteralPath $item.FullName -Recurse -Force }
    }
    if (-not @(Get-ChildItem -LiteralPath $Paths.Root -Force).Count) { Remove-Item -LiteralPath $Paths.Root -Force }
    # The app shortcut's icon copy (app-shortcut.ps1) is shared with the WSL
    # Portal's shortcut; it goes only when it is all that is left of ODS. A
    # Desktop-only PC never has it, so an empty ODS folder goes as well.
    $ods = Split-Path -Parent $Paths.Root
    if (Test-Path -LiteralPath $ods -PathType Container) {
        $rest = @(Get-ChildItem -LiteralPath $ods -Force)
        if ($rest.Count -eq 0 -or ($rest.Count -eq 1 -and $rest[0].Name -ceq 'ods-app.ico')) { Remove-Item -LiteralPath $ods -Recurse -Force }
    }
}

function Invoke-ODSDesktopUninstall {
    <#
    .SYNOPSIS
        Remove ODS Desktop. Returns what was stopped, removed and kept.
    #>
    param([Parameter(Mandatory = $true)]$Paths,
        [switch]$KeepData, [switch]$RemoveData, [switch]$NonInteractive,
        [string]$RegistryRoot = $script:ODSDesktopUninstallRoot,
        [string]$ApiTaskName = ('ODSDesktopApi-' + [Security.Principal.WindowsIdentity]::GetCurrent().User.Value),
        [scriptblock]$Ask = ${function:Read-ODSDesktopDataChoice})
    $runtime = Get-ODSDesktopOwnedRuntime $Paths
    $owned = @(Get-ODSDesktopOwnedPaths $runtime)
    $remove = Resolve-ODSDesktopDataChoice $Paths $owned $KeepData $RemoveData $NonInteractive $Ask
    $stoppedApp = Stop-ODSDesktopTask $ApiTaskName $Paths.Intent $Paths.Ownership
    $stoppedModel = $false
    if ($runtime -and $runtime.TaskName) {
        $stoppedModel = Stop-ODSDesktopTask $runtime.TaskName (Join-Path $runtime.RuntimeDir 'intent.json') (Join-Path $runtime.RuntimeDir 'process-ownership.json')
    }
    $shortcuts = @(Remove-ODSDesktopShortcuts (Join-Path $Paths.Root 'shortcuts.json'))
    $plan = @(Get-ODSDesktopDataPlan $Paths $owned $remove)
    foreach ($entry in @($plan | Where-Object { $_.Remove })) { Remove-Item -LiteralPath $entry.Path -Recurse -Force }
    $folders = @($owned | ForEach-Object { Split-Path -Parent $_ })
    if ($runtime) {
        if (Test-Path -LiteralPath $runtime.RuntimeDir) { Remove-Item -LiteralPath $runtime.RuntimeDir -Recurse -Force }
        $folders += Split-Path -Parent $runtime.RuntimeDir
    }
    $key = Get-ODSDesktopUninstallKey $RegistryRoot
    if (Test-Path -LiteralPath $key) { Remove-Item -LiteralPath $key -Recurse -Force }
    Remove-ODSDesktopLeftovers $Paths $folders
    return [pscustomobject]@{ StoppedApp = $stoppedApp; StoppedModel = $stoppedModel; Shortcuts = $shortcuts
        Removed = @($plan | Where-Object { $_.Remove }); Kept = @($plan | Where-Object { -not $_.Remove }) }
}
