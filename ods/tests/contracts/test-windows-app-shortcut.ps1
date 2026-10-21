# The ODS app shortcut Windows setup makes after a verified install: an Edge
# app window for the local dashboard with the ODS icon, or the 'ODS Portal'
# web link when Edge or the shortcut is unavailable. Edge is a fixture file
# that never runs; shortcuts go to temporary folders, never the real desktop
# or Start menu, and LOCALAPPDATA points into the fixture.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$library = Join-Path $root 'installers/windows/lib/app-shortcut.ps1'
. $library
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}
$script:output = [Collections.Generic.List[string]]::new()
function Write-Host { param([Parameter(ValueFromRemainingArguments = $true)]$Text) $script:output.Add([string]($Text -join ' ')) }
function Write-Warning { param([string]$Message) $script:output.Add("WARNING: $Message") }
$onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-app-shortcut-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$previousLocalAppData = $env:LOCALAPPDATA
$env:LOCALAPPDATA = Join-Path $fixture 'LocalAppData'
try {
    $desktop = Join-Path $fixture 'Desktop'
    $programs = Join-Path $fixture 'Programs'
    $null = New-Item -ItemType Directory -Path $desktop, $programs
    $portal = 'http://localhost:3001/pixel'
    $iconSource = Join-Path $root 'extensions/services/dashboard/public/osmantic-os.ico'
    function Reset-Folders {
        foreach ($folder in @($desktop, $programs)) { Get-ChildItem -LiteralPath $folder -Force | Remove-Item -Force }
        $script:output.Clear()
    }

    # --- Only the local dashboard ------------------------------------------------
    foreach ($url in @('https://example.com/', 'http://127.0.0.1:3001/', 'http://localhost:3001/pixel', 'http://localhost:3001/ --flag')) {
        Check ((Get-Failure { $null = New-ODSAppShortcut -Url $url -Folders @($desktop) }) -match 'loopback dashboard URL') "the app shortcut refuses '$url'"
    }
    Check ((Get-Failure { $null = Publish-ODSPortalShortcut -PortalUrl 'http://example.com:3001/pixel' -DesktopFolder $desktop -ProgramsFolder $programs }) -match 'loopback Portal URL') 'setup publishes a shortcut only for the loopback Portal URL'
    Check (@(Get-ChildItem -LiteralPath $desktop, $programs -Force).Count -eq 0) 'a refused URL writes no shortcut'
    Check (Test-Path -LiteralPath $iconSource -PathType Leaf) 'the ODS icon setup names ships with the dashboard'

    # --- No Edge: the earlier desktop web link -----------------------------------
    function Get-ODSEdgePath { return $null }
    Check (@(New-ODSAppShortcut -Url 'http://localhost:3001/' -Folders @($desktop, $programs)).Count -eq 0) 'without Edge no app shortcut is made'
    Reset-Folders
    $made = @(Publish-ODSPortalShortcut -PortalUrl $portal -IconSource $iconSource -DesktopFolder $desktop -ProgramsFolder $programs)
    $webLink = Join-Path $desktop 'ODS Portal.url'
    Check ($made.Count -eq 1 -and $made[0] -eq $webLink -and
        ((Get-Content -LiteralPath $webLink) -join '|') -ceq "[InternetShortcut]|URL=$portal" -and
        @(Get-ChildItem -LiteralPath $programs -Force).Count -eq 0) 'without Edge setup keeps the desktop web link to Portal'
    Check (($script:output -join "`n") -match "Created the desktop shortcut 'ODS Portal'\.") 'the web link is announced as before'
    Reset-Folders
    Check (@(Publish-ODSPortalShortcut -PortalUrl $portal -DesktopFolder '' -ProgramsFolder $programs).Count -eq 0) 'an account without a desktop folder gets no shortcut and no error'

    # --- A shortcut that cannot be made never fails the install ------------------
    function New-ODSAppShortcut { param($Url, $IconSource, $Folders) throw 'The WScript.Shell COM object is unavailable.' }
    Reset-Folders
    $made = @(Publish-ODSPortalShortcut -PortalUrl $portal -DesktopFolder $desktop -ProgramsFolder $programs)
    Check ($made.Count -eq 1 -and (Test-Path -LiteralPath $webLink) -and
        ($script:output -join "`n") -match 'WARNING: The ODS app shortcut was not created: The WScript\.Shell COM object is unavailable\. A desktop web link is used instead\.') 'a failed app shortcut warns with its cause and falls back to the web link'
    if ($onWindows) {
        # Controlled folder access, or a read-only web link left by an older
        # install: the fallback warns with the cause and the Portal address.
        Reset-Folders
        [IO.File]::WriteAllText($webLink, "[InternetShortcut]`r`nURL=http://localhost:3000/pixel`r`n")
        (Get-Item -LiteralPath $webLink).Attributes = 'ReadOnly'
        $made = @(Publish-ODSPortalShortcut -PortalUrl $portal -DesktopFolder $desktop -ProgramsFolder $programs)
        $said = $script:output -join "`n"
        Check ($made.Count -eq 0 -and $said -match "WARNING: The desktop shortcut 'ODS Portal' was not created: .+ open it at http://localhost:3001/pixel" -and
            $said -match 'discord\.gg/' -and $said -notmatch "Created the desktop shortcut") 'a web link that cannot be written warns with its cause and the Portal address instead of failing the install'
        (Get-Item -LiteralPath $webLink).Attributes = 'Normal'
    }
    . $library

    if ($onWindows) {
        # --- The real Edge lookup ------------------------------------------------
        # The Program Files variables point at fixture roots. Edge lives in its
        # Application folder, and the 32-bit root, where Edge installs, wins.
        $previousX86 = ${env:ProgramFiles(x86)}
        $previousProgramFiles = $env:ProgramFiles
        $x86Root = Join-Path $fixture 'ProgramFilesX86'
        $programFilesRoot = Join-Path $fixture 'ProgramFiles'
        try {
            [Environment]::SetEnvironmentVariable('ProgramFiles(x86)', $x86Root, 'Process')
            [Environment]::SetEnvironmentVariable('ProgramFiles', $programFilesRoot, 'Process')
            Check ($null -eq (Get-ODSEdgePath)) 'no msedge.exe under either Program Files root means no Edge'
            $programFilesEdge = Join-Path $programFilesRoot 'Microsoft\Edge\Application\msedge.exe'
            $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $programFilesEdge)
            [IO.File]::WriteAllText($programFilesEdge, 'edge fixture, never executed')
            Check ((Get-ODSEdgePath) -ceq $programFilesEdge) 'Edge is found in Microsoft\Edge\Application under Program Files'
            $x86Edge = Join-Path $x86Root 'Microsoft\Edge\Application\msedge.exe'
            $null = New-Item -ItemType Directory -Force -Path $x86Edge
            Check ((Get-ODSEdgePath) -ceq $programFilesEdge) 'a folder named msedge.exe is not Edge'
            Remove-Item -LiteralPath $x86Edge
            [IO.File]::WriteAllText($x86Edge, 'edge fixture, never executed')
            Check ((Get-ODSEdgePath) -ceq $x86Edge) 'Edge in the 32-bit Program Files folder, where it installs, comes first'
            [Environment]::SetEnvironmentVariable('ProgramFiles(x86)', $null, 'Process')
            Check ((Get-ODSEdgePath) -ceq $programFilesEdge) 'without a 32-bit Program Files variable Edge is still found'
        } finally {
            [Environment]::SetEnvironmentVariable('ProgramFiles(x86)', $previousX86, 'Process')
            [Environment]::SetEnvironmentVariable('ProgramFiles', $previousProgramFiles, 'Process')
        }

        # --- Edge present: the ODS app on the desktop and in the Start menu ------
        $edgeFixture = Join-Path (Join-Path $fixture 'Edge') 'msedge.exe'
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $edgeFixture)
        # Windows reads a saved target back in its long form, and a runner's
        # TEMP can be an 8.3 short path, so the target is matched by content.
        $edgeMarker = 'edge fixture, never executed ' + [guid]::NewGuid().ToString('N')
        [IO.File]::WriteAllText($edgeFixture, $edgeMarker)
        function Get-ODSEdgePath { return $edgeFixture }
        Reset-Folders
        $made = @(New-ODSAppShortcut -Url 'http://localhost:3001/' -IconSource $iconSource -Folders @($desktop, (Join-Path $fixture 'missing')))
        $shell = New-Object -ComObject WScript.Shell
        try {
            $link = $shell.CreateShortcut($made[0])
            $copiedIcon = Join-Path $env:LOCALAPPDATA 'ODS\ods-app.ico'
            Check ($made.Count -eq 1 -and $made[0] -eq (Join-Path $desktop 'ODS.lnk') -and
                (Split-Path -Leaf $link.TargetPath) -eq 'msedge.exe' -and [IO.File]::ReadAllText($link.TargetPath) -ceq $edgeMarker -and
                $link.Arguments -ceq '--app=http://localhost:3001/' -and
                [IO.File]::ReadAllText((Join-Path $link.WorkingDirectory 'msedge.exe')) -ceq $edgeMarker -and
                $link.IconLocation -like '*\ODS\ods-app.ico,0' -and
                (Get-FileHash -LiteralPath $copiedIcon).Hash -eq (Get-FileHash -LiteralPath $iconSource).Hash) 'the app shortcut opens the dashboard in an Edge app window with a private copy of the ODS icon'
        } finally { $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($shell) }
        Reset-Folders
        $made = @(Publish-ODSPortalShortcut -PortalUrl $portal -IconSource $iconSource -DesktopFolder $desktop -ProgramsFolder $programs)
        Check ($made.Count -eq 2 -and (Test-Path -LiteralPath (Join-Path $desktop 'ODS.lnk')) -and (Test-Path -LiteralPath (Join-Path $programs 'ODS.lnk')) -and
            -not (Test-Path -LiteralPath $webLink)) 'with Edge setup makes the ODS app on the desktop and in the Start menu instead of the web link'
        Check (($script:output -join "`n") -match 'Created the ODS app on the desktop and in the Start menu\.' -and
            ($script:output -join "`n") -match 'Pin to taskbar') 'the owner is told where the app is and how to pin it'
        Reset-Folders
        $null = Publish-ODSPortalShortcut -PortalUrl $portal -IconSource $iconSource -DesktopFolder $desktop -ProgramsFolder (Join-Path $fixture 'missing')
        Check (($script:output -join "`n") -match 'Created the ODS app on the desktop\.') 'the note names only the places a shortcut was made'
        # The Start-menu save fails after the desktop one succeeded (a
        # read-only ODS.lnk there): only the desktop app is reported.
        Reset-Folders
        $blocked = Join-Path $programs 'ODS.lnk'
        [IO.File]::WriteAllText($blocked, 'read-only placeholder')
        (Get-Item -LiteralPath $blocked).Attributes = 'ReadOnly'
        $made = @(Publish-ODSPortalShortcut -PortalUrl $portal -IconSource $iconSource -DesktopFolder $desktop -ProgramsFolder $programs)
        $said = $script:output -join "`n"
        Check ($made.Count -eq 1 -and $made[0] -eq (Join-Path $desktop 'ODS.lnk') -and -not (Test-Path -LiteralPath $webLink) -and
            $said -match ([regex]::Escape("WARNING: The ODS app shortcut could not be saved in ${programs}:")) -and
            $said -match 'Created the ODS app on the desktop\.' -and $said -notmatch 'was not created') 'a Start-menu shortcut that cannot be saved keeps the desktop app and reports only it'
        (Get-Item -LiteralPath $blocked).Attributes = 'Normal'
    }

    # --- windows.ps1 uses this helper after a verified install -------------------
    $installer = [IO.File]::ReadAllText((Join-Path $root 'installers/windows.ps1'))
    Check ($installer.Contains(". (Join-Path `$PSScriptRoot 'windows/lib/app-shortcut.ps1')") -and
        $installer.Contains('Publish-ODSPortalShortcut -PortalUrl $portalUrl[0] -IconSource $appIcon') -and
        $installer.Contains("'extensions/services/dashboard/public/osmantic-os.ico'") -and
        -not $installer.Contains("'ODS Portal.url'")) 'windows.ps1 publishes the Portal shortcut through the helper'

    Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks Windows ODS app shortcut contracts."
} finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to clean a fixture outside the temporary directory.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
