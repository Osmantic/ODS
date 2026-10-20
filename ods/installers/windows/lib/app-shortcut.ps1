# The ODS dashboard as a desktop app on Windows: a shortcut that opens it in
# its own Microsoft Edge app window (no browser tabs or address bar) with the
# ODS icon, on the desktop and in the Start menu, ready to pin to the taskbar.
# Windows lets only the owner pin, so setup creates the shortcuts and stops.
# The dashboard is also an installable web app (its manifest.webmanifest); a
# shortcut needs no browser prompt and works on every Windows 11 machine.

function Get-ODSEdgePath {
    foreach ($root in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
        if (-not $root) { continue }
        $candidate = Join-Path $root 'Microsoft\Edge\Application\msedge.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

function New-ODSAppShortcut {
    <#
    .SYNOPSIS
        Create the ODS app shortcuts for a loopback dashboard URL. Returns the
        shortcut paths it saved, or none when Edge is missing (the caller
        keeps its plain web link then). A folder whose shortcut cannot be
        saved is skipped with a warning.
    #>
    param([Parameter(Mandatory = $true)][string]$Url, [string]$IconSource = '',
        [string[]]$Folders = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')))
    if ($Url -cnotmatch '^http://localhost:[0-9]{1,5}/$') { throw "The ODS app shortcut needs a loopback dashboard URL, not '$Url'." }
    $edge = Get-ODSEdgePath
    if (-not $edge) { return @() }
    $stateDir = Join-Path $env:LOCALAPPDATA 'ODS'
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    # A copy outside the installer checkout, which may move or be deleted.
    $icon = Join-Path $stateDir 'ods-app.ico'
    if ($IconSource -and (Test-Path -LiteralPath $IconSource -PathType Leaf)) {
        Copy-Item -LiteralPath $IconSource -Destination $icon -Force
    }
    $shell = New-Object -ComObject WScript.Shell
    try {
        $created = @()
        foreach ($folder in $Folders) {
            if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
            $path = Join-Path $folder 'ODS.lnk'
            try {
                $link = $shell.CreateShortcut($path)
                $link.TargetPath = $edge
                $link.Arguments = "--app=$Url"
                $link.WorkingDirectory = Split-Path -Parent $edge
                $link.Description = 'ODS: your local AI in its own window. Press F11 for full screen.'
                if (Test-Path -LiteralPath $icon -PathType Leaf) { $link.IconLocation = "$icon,0" }
                $link.Save()
                $created += $path
            } catch {
                # Controlled folder access or a read-only folder: the other
                # shortcut, or the caller's web link, still gets made.
                Write-Warning "The ODS app shortcut could not be saved in ${folder}: $($_.Exception.Message)"
            }
        }
        return $created
    } finally {
        $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
}

function Publish-ODSPortalShortcut {
    <#
    .SYNOPSIS
        After a verified install: the ODS app on the desktop and in the Start
        menu, or the 'ODS Portal' web link on the desktop when Edge or the app
        shortcut is unavailable. A shortcut problem never fails the install.
        Returns the paths it wrote.
    #>
    param([Parameter(Mandatory = $true)][string]$PortalUrl, [string]$IconSource = '',
        [string]$DesktopFolder = [Environment]::GetFolderPath('Desktop'),
        [string]$ProgramsFolder = [Environment]::GetFolderPath('Programs'))
    if ($PortalUrl -cnotmatch '^(http://localhost:[0-9]{1,5}/)pixel$') { throw "The Portal shortcut needs the loopback Portal URL, not '$PortalUrl'." }
    $appUrl = $Matches[1]
    $made = @()
    try {
        $made = @(New-ODSAppShortcut -Url $appUrl -IconSource $IconSource -Folders @($DesktopFolder, $ProgramsFolder))
    } catch {
        Write-Warning "The ODS app shortcut was not created: $($_.Exception.Message) A desktop web link is used instead."
    }
    if ($made.Count) {
        $places = @(foreach ($path in $made) {
            if ($DesktopFolder -and (Split-Path -Parent $path) -ieq $DesktopFolder) { 'on the desktop' } else { 'in the Start menu' }
        })
        Write-Host "Created the ODS app $($places -join ' and '). It opens ODS in its own window; to keep it on the taskbar, right-click its taskbar icon and choose Pin to taskbar."
        return $made
    }
    if (-not $DesktopFolder) { return @() }
    $webLink = Join-Path $DesktopFolder 'ODS Portal.url'
    try {
        Set-Content -LiteralPath $webLink -Value @('[InternetShortcut]', "URL=$PortalUrl") -Encoding ASCII -ErrorAction Stop
    } catch {
        Write-Warning ("The desktop shortcut 'ODS Portal' was not created: $($_.Exception.Message) ODS is installed; open it at " +
            "$PortalUrl, or run setup again to retry the shortcut. Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC")
        return @()
    }
    Write-Host "Created the desktop shortcut 'ODS Portal'."
    return @($webLink)
}
