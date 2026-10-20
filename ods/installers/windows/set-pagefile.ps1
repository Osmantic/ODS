# Sets a fixed-size page file on the system drive so the Windows commit limit
# covers the GPU memory of an NVIDIA RTX Spark machine. Windows charges every
# GPU allocation against the commit limit (visible RAM + page file), even in
# the GPU's carve-out, so without enough page file the model cannot load.
#
# Run elevated (Windows setup starts it with a UAC prompt). Takes effect after
# a restart. Writes the same value System Properties > Virtual memory writes,
# keeps page files on other drives, never makes the system drive's page file
# smaller, and turns off automatic management, which would override the fixed
# size. Ported from the RTX Spark branch (21ae748de).
param(
    [Parameter(Mandatory = $true)][ValidateRange(16, 256)][int]$SizeGB
)
$ErrorActionPreference = 'Stop'

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Setting the page file needs administrator rights.'
}

. (Join-Path (Join-Path $PSScriptRoot 'lib') 'pagefile-entries.ps1')
$drive = $env:SystemDrive
$key = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management'
$sizeMB = $SizeGB * 1024
# A system-managed or automatic page file has no fixed size in the registry;
# what Windows allocated now is its size, and the new one is never smaller.
$allocated = @(Get-CimInstance Win32_PageFileUsage | Where-Object { ([string]$_.Name).StartsWith("$drive\", [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object { [long]$_.AllocatedBaseSize })
if ($allocated.Count -and ($allocated | Measure-Object -Maximum).Maximum -gt $sizeMB) {
    throw "The page file on $drive is already $(($allocated | Measure-Object -Maximum).Maximum) MB; ODS never makes it smaller (asked for $sizeMB MB)."
}
# No PagingFiles value means no page file is configured on any drive.
$existing = @((Get-ItemProperty -Path $key -Name PagingFiles -ErrorAction SilentlyContinue).PagingFiles)
# Initial = maximum, so Windows never has to grow the file.
$entries = Get-ODSPagingFilesEntries $existing $drive $sizeMB
Set-ItemProperty -Path $key -Name PagingFiles -Value ([string[]]$entries) -Type MultiString
$computer = Get-CimInstance Win32_ComputerSystem
if ($computer.AutomaticManagedPagefile) {
    Set-CimInstance -InputObject $computer -Property @{ AutomaticManagedPagefile = $false }
}
exit 0
