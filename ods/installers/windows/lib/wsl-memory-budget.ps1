# WSL memory budget for a PC whose model runs on Windows in memory Windows
# also lends to WSL (an NVIDIA RTX Spark; an AMD unified-memory APU such as
# Strix Halo), so WSL cannot grow until Windows runs out.
#
# Why: WSL may use half of the RAM Windows sees unless .wslconfig says
# otherwise, and Windows charges that against the same commit limit (the RAM
# it sees plus the page file) as the model server. On an RTX Spark (30.5 GiB
# visible, 96 GB page file, commit limit 126.5 GiB, a model server holding
# about 96 GB) WSL grew to about 15 GB while an app installed and an image
# turn ran; the commit charge reached the limit, Windows logged event 2004,
# wslservice.exe crashed and WSL restarted (2026-10-25). Limited by hand to
# memory=12GB with autoMemoryReclaim=dropcache, WSL reported 11,933 MiB and
# about 13.5 GiB of commit stayed free.
#
# The limit, in GiB:
#   room  = commit limit - model server's commit - Windows reserve - headroom
#   limit = room rounded down to a whole GB, never below 12 (what ODS needs:
#           the full stack with Portal ran at 12 GB), written only when it is
#           below WSL's own default (half of the RAM Windows sees)
# The Windows reserve (16) is the launcher's commit rule: Windows itself
# commits about 12 GiB idle and about 15 GiB beside the full stack. The
# headroom (8) is where `ods doctor` starts warning: below it, an install or
# an image turn can use the rest. On that RTX Spark: 126.49 - 96 - 16 - 8 is
# 6.49, under the floor, so 12 GB, and a note that a larger page file would
# give Windows more room.
#
# Rules: no administrator rights (.wslconfig is in the user profile); the
# page file and every other system setting stay the owner's call (setup only
# says how); an owner's own memory= or autoMemoryReclaim= is never changed;
# other settings stay; the old file is backed up and
# restore-windows-settings.ps1 puts it back (wsl-memory-config.ps1). WSL uses
# the new values after it restarts.

. (Join-Path $PSScriptRoot 'wsl-memory-config.ps1')

# The launcher's $script:ODSNativeWindowsReserveGiB (native-llama-runtime.ps1).
$script:ODSWslBudgetWindowsReserveGiB = 16
$script:ODSWslBudgetHeadroomGiB = 8
# WSL gives cached memory back to Windows at once; WSL 2.0.0 added the setting.
$script:ODSWslReclaimValue = 'dropcache'
$script:ODSWslReclaimMinVersion = [version]'2.0.0'
$script:ODSWslBudgetHelpLine = 'Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC'

function Get-ODSWindowsMemoryStatus {
    <#
    .SYNOPSIS
        RAM Windows sees, its commit limit and its commit headroom in GiB,
        from GlobalMemoryStatusEx (ullTotalPhys, ullTotalPageFile,
        ullAvailPageFile), read unelevated and without WMI; $null when
        Windows does not answer.
    #>
    if (-not ('ODSMemoryStatusEx' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ODSMemoryStatusEx {
    [StructLayout(LayoutKind.Sequential)]
    public class Status {
        public uint Length = (uint)Marshal.SizeOf(typeof(Status));
        public uint MemoryLoad;
        public ulong TotalPhys, AvailPhys, TotalPageFile, AvailPageFile, TotalVirtual, AvailVirtual, AvailExtendedVirtual;
    }
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool GlobalMemoryStatusEx([In, Out] Status status);
    public static Status Read() { Status status = new Status(); return GlobalMemoryStatusEx(status) ? status : null; }
}
'@
    }
    $status = [ODSMemoryStatusEx]::Read()
    if ($null -eq $status) { return $null }
    return [pscustomobject]@{
        VisibleGiB = [math]::Round([double]$status.TotalPhys / 1GB, 2)
        CommitLimitGiB = [math]::Round([double]$status.TotalPageFile / 1GB, 2)
        CommitAvailableGiB = [math]::Round([double]$status.AvailPageFile / 1GB, 2)
    }
}

function Get-ODSWslMemoryBudget {
    <#
    .SYNOPSIS
        The WSL memory limit for this PC (pure; the formula above).
    .OUTPUTS
        DefaultGiB (WSL's own limit), RoomGiB, CapGiB (whole GB to write, or
        $null when WSL's default already fits), BelowFloor (even 12 GB does
        not fit) and PageFileShortGiB (how much more page file would make 12
        GB fit).
    #>
    param([double]$VisibleGiB, [double]$CommitLimitGiB, [double]$EngineCommitGiB)
    $default = Get-ODSWslDefaultMemoryGiB $VisibleGiB
    $room = $CommitLimitGiB - $EngineCommitGiB - $script:ODSWslBudgetWindowsReserveGiB - $script:ODSWslBudgetHeadroomGiB
    $cap = $null
    if ($room -lt $default - 1e-9) {
        $cap = [int][math]::Max($script:ODSWslMemoryFloorGiB, [math]::Floor($room))
        # ODS only ever lowers WSL's limit: never 12 GB on a PC whose default is less.
        if ($cap -ge $default - 1e-9) { $cap = $null }
    }
    $short = 0
    if ($room -lt $script:ODSWslMemoryFloorGiB) { $short = [int][math]::Ceiling($script:ODSWslMemoryFloorGiB - $room) }
    return [pscustomobject]@{ DefaultGiB = $default; RoomGiB = [math]::Round($room, 2); CapGiB = $cap
        BelowFloor = ($short -gt 0); PageFileShortGiB = $short; CommitLimitGiB = $CommitLimitGiB; EngineCommitGiB = $EngineCommitGiB }
}

function Test-ODSWslReclaimSupported([string]$VersionText) {
    # `wsl --version` output: the first dotted version is WSL itself (its
    # labels are localized). autoMemoryReclaim needs WSL 2.0.0 or newer.
    $match = [regex]::Match([string]$VersionText, '(?m)^.*?:\s*(\d+\.\d+\.\d+(?:\.\d+)?)\s*\r?$')
    return ($match.Success -and [version]$match.Groups[1].Value -ge $script:ODSWslReclaimMinVersion)
}

function Format-ODSWslGiB([double]$Value) {
    return ([math]::Round($Value, 1)).ToString('0.#', [Globalization.CultureInfo]::InvariantCulture)
}

function Get-ODSWslMemoryBudgetNotice {
    <#
    .SYNOPSIS
        What setup tells the owner (pure): the lines to print now, and
        whether WSL must restart to use what changed.
    #>
    param($Budget, [string]$Engine, $Current, $Update, [string]$Path, [string]$Backup, [string]$Result)
    $lines = [Collections.Generic.List[string]]::new()
    $why = ("The model ($Engine) runs on Windows in memory Windows also lends to WSL. Windows can lend " +
        "$(Format-ODSWslGiB $Budget.CommitLimitGiB) GiB in total (its commit limit) and the model needs about " +
        "$(Format-ODSWslGiB $Budget.EngineCommitGiB) GiB of it.")
    if ($Result -ceq 'unsupported') {
        $lines.Add("$why ODS did not change $Path because it is not a plain text file ODS can edit safely. Add memory=$($Budget.CapGiB)GB under [wsl2] yourself, then restart WSL. $script:ODSWslBudgetHelpLine")
        return [pscustomobject]@{ Lines = @($lines); RestartNeeded = $false }
    }
    $changed = @()
    if ($Update.Memory -ceq 'written') { $changed += "memory=$($Budget.CapGiB)GB under [wsl2] (WSL may use at most $($Budget.CapGiB) GB, instead of $(Format-ODSWslGiB $Budget.DefaultGiB) GB)" }
    if ($Update.Reclaim -ceq 'written') { $changed += "autoMemoryReclaim=$script:ODSWslReclaimValue under [experimental] (WSL gives memory it no longer uses back to Windows)" }
    if ($changed.Count) {
        $kept = if ($Backup) { " Your other settings are unchanged; the old file is saved as $Backup." } else { '' }
        $lines.Add("$why So ODS set " + ($changed -join ' and ') + " in $Path.$kept To undo it, run $(Get-ODSWindowsRestoreScriptPath).")
    }
    if ($Current -and $Current.Owner -ceq 'owner' -and $Budget.CapGiB -and ($null -eq $Current.GiB -or $Current.GiB -gt $Budget.CapGiB + 1e-9)) {
        $value = if ($null -ne $Current.GiB) { "memory=$(Format-ODSWslGiB $Current.GiB)GB" } else { 'its own memory=' }
        $lines.Add("$why Your $Path sets $value, so ODS left it. With the model running, WSL should use at most $($Budget.CapGiB) GB; " +
            "if Windows runs out of memory, WSL can crash. Set memory=$($Budget.CapGiB)GB under [wsl2] when that suits you.")
    }
    if ($Budget.BelowFloor) {
        $lines.Add("Windows has little memory to spare beside this model: even with WSL at $script:ODSWslMemoryFloorGiB GB (the least ODS needs) " +
            "it keeps under $script:ODSWslBudgetHeadroomGiB GiB free. ODS does not change the page file; that is your call. To give Windows more " +
            "room, an administrator can make the page file about $($Budget.PageFileShortGiB) GB larger: System Properties > Advanced > " +
            'Performance > Settings > Advanced > Virtual memory > Change, then restart Windows.')
    }
    return [pscustomobject]@{ Lines = @($lines); RestartNeeded = [bool]$changed.Count }
}

function Get-ODSWslRestartNotice {
    # Plain words for the end of setup.
    return ('WSL uses its new memory settings only after it restarts. When nothing important runs in Ubuntu, ' +
        'restart Windows, or run: wsl --shutdown (in PowerShell), then start Docker Desktop again. ' +
        'ODS checks and repairs its containers by itself after WSL starts again. Until then WSL keeps its current limit.')
}

function Invoke-ODSPortalWslMemoryBudget {
    <#
    .SYNOPSIS
        Set the WSL memory budget for a model server that runs on Windows in
        shared memory: merge memory= (and autoMemoryReclaim= where WSL
        supports it) into %USERPROFILE%\.wslconfig, never over the owner's
        own values, and say what changed. Never elevates; never touches the
        page file. Nothing to do: nothing is written, not even a backup.
    .OUTPUTS
        Result ('written', 'unchanged', 'owner', 'unsupported' or
        'unavailable'), RestartNeeded, Budget and Lines (also printed).
    #>
    param([double]$EngineCommitGiB, [string]$Engine, [string]$WslVersionText)
    $status = Get-ODSWindowsMemoryStatus
    if (-not $status) {
        $line = 'Could not read how much memory Windows can lend, so the WSL memory limit was not changed. Run ods doctor in Ubuntu later to check it. ' + $script:ODSWslBudgetHelpLine
        Write-Host "         $line"
        return [pscustomobject]@{ Result = 'unavailable'; RestartNeeded = $false; Budget = $null; Lines = @($line) }
    }
    $budget = Get-ODSWslMemoryBudget $status.VisibleGiB $status.CommitLimitGiB $EngineCommitGiB
    $reclaim = if (Test-ODSWslReclaimSupported $WslVersionText) { $script:ODSWslReclaimValue } else { '' }
    $file = Read-ODSWslConfigFile
    $recordedGiB = Get-ODSWslRecordedGiB
    $current = if ($file.Text) { Read-ODSWslConfigMemory $file.Text $recordedGiB } else { $null }
    # The limit goes in only where ODS may write it; the owner's stays.
    $memory = if ($null -ne $budget.CapGiB -and -not ($current -and $current.Owner -ceq 'owner')) { [int]$budget.CapGiB } else { 0 }
    $result = 'unchanged'
    $backup = ''
    $update = [pscustomobject]@{ Memory = 'skipped'; Reclaim = 'skipped' }
    if (-not $file.Editable) {
        $result = if ($null -ne $budget.CapGiB) { 'unsupported' } else { 'unchanged' }
    } else {
        $update = Get-ODSWslConfigUpdate $file.Text $memory $reclaim $recordedGiB (Get-ODSWslRecordedReclaim)
        if ($update.Text -cne $file.Text) {
            $written = Write-ODSWslConfig $memory $reclaim
            $result = $written.Result
            $backup = $written.Backup
        } else {
            # Values already in place: nothing to report as changed.
            $update = [pscustomobject]@{ Memory = $(if ($update.Memory -ceq 'owner') { 'owner' } else { 'skipped' }); Reclaim = 'skipped' }
            if ($current -and $current.Owner -ceq 'owner') { $result = 'owner' }
        }
    }
    $notice = Get-ODSWslMemoryBudgetNotice -Budget $budget -Engine $Engine -Current $current -Update $update `
        -Path $file.Path -Backup $backup -Result $result
    foreach ($line in $notice.Lines) { Write-Host "         $line" }
    return [pscustomobject]@{ Result = $result; RestartNeeded = $notice.RestartNeeded; Budget = $budget; Lines = $notice.Lines }
}
