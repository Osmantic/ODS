$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/env-generator.ps1')
. (Join-Path $root 'installers/windows/lib/ui.ps1')
. (Join-Path $root 'installers/windows/lib/readiness-summary.ps1')

function Assert-Shortcut([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Test-ODSReadinessHttp { param([string]$Url); return @{ Code = 200; Ready = $true } }
function Get-ODSReadinessContainerState { param([string]$Container); return 'healthy' }

$installer = Get-Content -LiteralPath (Join-Path $root 'installers/windows/install-windows.ps1') -Raw
Assert-Shortcut ($installer.Contains('$chatUrl = "http://localhost:$webuiPort"')) 'Installer did not select the configured chat port'
Assert-Shortcut ($installer.Contains('-ChatUrl $chatUrl')) 'Installer summary and shortcut do not share the chat URL'
Assert-Shortcut ($installer.Contains('Write-ODSWindowsShortcuts -ChatUrl $chatUrl')) 'Installer does not create the model-chat shortcut'

$scratch = Join-Path ([IO.Path]::GetTempPath()) ('ods-shortcut-' + [guid]::NewGuid().ToString('N'))
try {
    $desktop = Join-Path $scratch 'Desktop'
    $startMenu = Join-Path $scratch 'Start Menu'
    New-Item -ItemType Directory -Path $desktop, $startMenu -Force | Out-Null
    $icon = Join-Path $scratch 'ods.ico'
    [IO.File]::WriteAllBytes($icon, [byte[]]@(0, 0, 1, 0))
    foreach ($port in @(3000, 4317)) {
        $chatUrl = "http://localhost:$port"
        Write-ODSWindowsShortcuts -ChatUrl $chatUrl -IconPath $icon -DesktopDir $desktop -StartMenuDir $startMenu
        $summaryOutput = & {
            Write-ODSInstallReadinessSummary -Checks @(@{ Name = 'Chat UI'; Url = $chatUrl; Container = 'ods-webui' }) -ChatUrl $chatUrl
        } 6>&1 | Out-String
        Assert-Shortcut ($summaryOutput.Contains("Open model chat: $chatUrl")) 'Install summary points away from selected model chat'
        foreach ($directory in @($desktop, $startMenu)) {
            $path = Join-Path $directory 'ODS.url'
            $bytes = [IO.File]::ReadAllBytes($path)
            $content = [Text.Encoding]::UTF8.GetString($bytes)
            Assert-Shortcut ($content.Contains("URL=$chatUrl`n")) "Shortcut in $directory missed selected chat URL"
            Assert-Shortcut (-not $content.Contains('URL=http://localhost:3001')) "Shortcut in $directory still opens Portal"
            Assert-Shortcut ($content.Contains("IconFile=$icon`n")) "Shortcut in $directory lost its icon"
            Assert-Shortcut (-not ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191)) "Shortcut in $directory gained a BOM"
        }
    }
    Write-Output 'PASS: Windows desktop and Start Menu shortcuts open configured native model chat'
} finally {
    $resolved = [IO.Path]::GetFullPath($scratch)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing cleanup outside the temporary root'
    }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
