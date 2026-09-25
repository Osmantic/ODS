# Pure-function contracts for the Windows Portal prerequisites.
# No WSL, Docker, registry, downloads or prompts are touched.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-prereqs.ps1')
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Write-Host "PASS $Message"
}

foreach ($name in @('maria', 'joao-silva', 'dev_1', 'a')) { Check (Test-ODSPortalLinuxUsername $name) "accepts Linux username $name" }
foreach ($name in @('', 'Maria', '1abc', 'root', 'ods', 'docker', 'with space', 'joão', ('a' * 33), 'x;rm')) { Check (-not (Test-ODSPortalLinuxUsername $name)) "rejects Linux username '$name'" }

Check ((Get-ODSPortalDistroLauncherName 'Ubuntu-24.04') -eq 'ubuntu2404.exe') 'maps Ubuntu-24.04 to its launcher'
Check ((Get-ODSPortalDistroLauncherName 'Ubuntu') -eq 'ubuntu.exe') 'maps Ubuntu to its launcher'
Check ($null -eq (Get-ODSPortalDistroLauncherName 'Debian')) 'no launcher guess for other distros'

# The resume script must re-run the same entry point with the same options,
# and hostile-looking values must stay literal strings.
$options = [ordered]@{ Distro='Ubuntu-24.04'; Voice=[switch]$true; NoLangfuse=$true; DryRun=$true; InstallDir="/home/o'brien/ods `$(x)"; Tier=''; Rag=$false }
$script = New-ODSPortalResumeScript "C:\Users\Ana Maria\AppData\Local\Temp\ods-install-1\ODS-main\install.ps1" $options
$errors = $null
$tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($script, [ref]$tokens, [ref]$errors)
Check ($errors.Count -eq 0) 'resume script parses'
$call = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.InvocationOperator -eq 'Ampersand' }, $true) | Select-Object -Last 1
Check ($call.CommandElements[0].Value -eq "C:\Users\Ana Maria\AppData\Local\Temp\ods-install-1\ODS-main\install.ps1") 'resume invokes the original entry script'
$text = $call.Extent.Text
Check ($text -match '-Distro ''Ubuntu-24.04''' -and $text -match '-Voice' -and $text -match '-NoLangfuse') 'resume keeps switches and values'
Check ($text -notmatch 'DryRun' -and $text -notmatch '-Rag' -and $text -notmatch '-Tier') 'resume drops dry-run, false switches and empty values'
$installDir = $call.CommandElements | Where-Object { $_ -is [System.Management.Automation.Language.StringConstantExpressionAst] -and $_.Value -like '/home/*' }
Check ($installDir.Value -eq "/home/o'brien/ods `$(x)" -and $installDir.StringConstantType -eq 'SingleQuoted') 'resume values are single-quoted literals'

# Docker Desktop settings: current PascalCase store, older camelCase file.
$store = Update-ODSPortalDockerSettings '{"AutoStart":true,"IntegratedWslDistros":["Other"],"FeatureFlags":{"X":true}}' 'settings-store.json' 'Ubuntu-24.04' | ConvertFrom-Json
Check ((@($store.IntegratedWslDistros) -join ',') -eq 'Other,Ubuntu-24.04') 'adds distro to existing PascalCase list'
Check ($store.AutoStart -eq $true -and $store.FeatureFlags.X -eq $true) 'keeps unrelated Docker settings'
$fresh = Update-ODSPortalDockerSettings '{"AutoStart":false}' 'settings-store.json' 'Ubuntu' | ConvertFrom-Json
Check ((@($fresh.IntegratedWslDistros) -join ',') -eq 'Ubuntu' -and -not ($fresh.PSObject.Properties.Name -ccontains 'integratedWslDistros')) 'creates PascalCase key in settings-store.json'
$legacy = Update-ODSPortalDockerSettings '{"integratedWslDistros":[]}' 'settings.json' 'Ubuntu' | ConvertFrom-Json
Check ((@($legacy.integratedWslDistros) -join ',') -eq 'Ubuntu') 'uses camelCase key in legacy settings.json'
$same = '{"IntegratedWslDistros":["Ubuntu-24.04"]}'
Check ((Update-ODSPortalDockerSettings $same 'settings-store.json' 'Ubuntu-24.04') -eq $same) 'already integrated distro leaves the file unchanged'
Check ($null -eq (Update-ODSPortalDockerSettings '{"WslEngineEnabled":false}' 'settings-store.json' 'Ubuntu')) 'Hyper-V engine is reported instead of edited'
Check ($null -eq (Update-ODSPortalDockerSettings '{"wslEngineEnabled":false}' 'settings.json' 'Ubuntu')) 'legacy Hyper-V engine is reported instead of edited'
$rejected = $false
try { $null = Update-ODSPortalDockerSettings '[1,2]' 'settings-store.json' 'Ubuntu' } catch { $rejected = $true }
Check $rejected 'non-object Docker settings are refused'

# Capacity gate: disk first, then virtualization only when WSL is not ready.
function Get-ODSPortalFreeSystemGB { return $script:free }
function Test-ODSPortalVirtualization { $script:virtChecked = $true; return $script:virt }
foreach ($case in @(
    @{ free=39; virt=$true; ready=$true; ok=$false; name='39 GB free is refused' },
    @{ free=40; virt=$true; ready=$true; ok=$true; name='40 GB free passes' },
    @{ free=80; virt=$false; ready=$false; ok=$false; name='disabled virtualization is refused before WSL setup' },
    @{ free=80; virt=$false; ready=$true; ok=$true; name='working WSL does not depend on the firmware flag' })) {
    $script:free = $case.free; $script:virt = $case.virt; $script:virtChecked = $false
    $passed = $true
    try { Assert-ODSPortalHostCapacity $case.ready } catch { $passed = $false }
    Check ($passed -eq $case.ok) $case.name
}

# Password bytes reach the distro exactly: UTF-8, LF only, no console code page.
# Runnable only where a fake wsl.exe script can execute.
if ($IsLinux) {
    $fake = Join-Path ([IO.Path]::GetTempPath()) ('ods-fake-wsl-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $fake
    try {
        Set-Content -LiteralPath (Join-Path $fake 'wsl.exe') -Value "#!/bin/sh`nprintf '%s\n' `"`$*`" > `"`$(dirname `"`$0`")/args`"`ncat > `"`$(dirname `"`$0`")/stdin`"`nexit 0" -NoNewline
        chmod +x (Join-Path $fake 'wsl.exe')
        $previousPath = $env:PATH
        $env:PATH = $fake + [IO.Path]::PathSeparator + $env:PATH
        try { $result = Invoke-ODSPortalWslInput 'Ubuntu-24.04' @('chpasswd') 'maria:Senha çã:1 "x"' } finally { $env:PATH = $previousPath }
        $bytes = [IO.File]::ReadAllBytes((Join-Path $fake 'stdin'))
        Check ($result.Code -eq 0) 'stdin helper reports the command exit code'
        Check ([Text.Encoding]::UTF8.GetString($bytes) -ceq "maria:Senha çã:1 `"x`"`n") 'password is sent as UTF-8 with a single LF'
        Check (-not ($bytes -contains 13)) 'password stdin contains no carriage return'
        Check ((Get-Content -LiteralPath (Join-Path $fake 'args') -Raw).Trim() -eq '--distribution Ubuntu-24.04 --user root --exec chpasswd') 'password never appears in arguments'
    } finally { Remove-Item -LiteralPath $fake -Recurse -Force }
}

Write-Host "Passed $script:checks Windows Portal prerequisite contracts."
