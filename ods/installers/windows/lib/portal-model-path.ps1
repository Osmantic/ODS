# Lemonade 10 on Windows cannot reliably index WSL UNC directories. Keep one
# model store on a local Windows drive and expose that same directory to WSL.
function Get-ODSPortalModelStorePaths {
    param([Parameter(Mandatory=$true)][string]$WindowsPath)
    if ($WindowsPath -notmatch '^([A-Za-z]):[\\/](.+)$' -or $WindowsPath -match '[\x00-\x1f"<>|?*]') {
        throw 'Portal GPU models require an absolute local Windows drive directory.'
    }
    $drive = $Matches[1].ToLowerInvariant()
    $tail = $Matches[2].Replace('\','/')
    $parts = @($tail -split '/')
    if (@($parts | Where-Object { $_ -in @('','.', '..') -or $_.Contains(':') -or $_ -match '[. ]$' }).Count) {
        throw 'Portal model directory must be normalized and cannot contain traversal segments.'
    }
    return @{ WindowsPath=($drive.ToUpperInvariant() + ':\' + ($parts -join '\')); WslPath=('/mnt/' + $drive + '/' + ($parts -join '/')) }
}
