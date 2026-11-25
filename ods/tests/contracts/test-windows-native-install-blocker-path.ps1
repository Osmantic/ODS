# Contract: the Portal setup blocker for an existing native Windows install
# names the directory it matched, so the user knows what to stop (#8494).
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-setup.ps1'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "wsl-portal-setup.ps1 does not parse: $($errors[0].Message)" }
$definition = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-ODSNativeWindowsInstall' }, $true)
if (-not $definition) { throw 'wsl-portal-setup.ps1 no longer defines Test-ODSNativeWindowsInstall' }
. ([scriptblock]::Create($definition.Extent.Text))

function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    Write-Host "PASS $Message"
}

function New-NativeInstall([string]$Root) {
    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $Root '.env') -Value 'WEBUI_SECRET=x'
    Set-Content -LiteralPath (Join-Path $Root 'ods.ps1') -Value '# fixture'
}

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-native-blocker-' + [guid]::NewGuid().ToString('N'))
$saved = @{ USERPROFILE = $env:USERPROFILE; ODS_HOME = $env:ODS_HOME }
try {
    $env:USERPROFILE = Join-Path $fixture 'profile'
    New-Item -ItemType Directory -Path $env:USERPROFILE -Force | Out-Null
    Remove-Item Env:ODS_HOME -ErrorAction SilentlyContinue
    Check ($null -eq (Test-ODSNativeWindowsInstall)) 'no native install returns $null'

    $profileInstall = Join-Path $env:USERPROFILE 'ods'
    New-NativeInstall $profileInstall
    Check ((Test-ODSNativeWindowsInstall) -eq $profileInstall) 'a %USERPROFILE%\ods install returns its path'

    $customInstall = Join-Path $fixture 'custom ods'
    New-NativeInstall $customInstall
    $env:ODS_HOME = $customInstall
    Check ((Test-ODSNativeWindowsInstall) -eq $customInstall) 'an ODS_HOME install is reported first, with its path'
} finally {
    foreach ($name in $saved.Keys) {
        if ($null -ne $saved[$name]) { Set-Item "Env:$name" $saved[$name] } else { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    }
    Remove-Item -LiteralPath $fixture -Recurse -Force -ErrorAction SilentlyContinue
}

$text = [IO.File]::ReadAllText($source)
Check ($text -match [regex]::Escape('native Windows ODS installation was found at $nativeRoot')) 'the blocker message names the matched directory'
Check ($text -match [regex]::Escape("Join-Path `$nativeRoot 'ods.ps1'")) 'the blocker message shows how to stop that install'
exit 0
