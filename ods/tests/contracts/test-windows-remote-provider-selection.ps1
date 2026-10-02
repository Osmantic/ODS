$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\installers\windows\lib\service-plan.ps1')

$tempBase = [IO.Path]::GetTempPath()
$testRoot = Join-Path $tempBase ('ods-remote-provider-selection-' + [guid]::NewGuid().ToString('N'))
$resolvedBase = [IO.Path]::GetFullPath($tempBase).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
$resolvedRoot = [IO.Path]::GetFullPath($testRoot)
if (-not $resolvedRoot.StartsWith($resolvedBase, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Unsafe test root'
}

function Assert-Selection([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

try {
    foreach ($id in @('remote-provider-egress', 'remote-provider-ssh-tunnel')) {
        New-Item -ItemType Directory -Path (Join-Path $testRoot "extensions\services\$id") -Force | Out-Null
    }
    $dataDir = Join-Path $testRoot 'data\remote-provider'
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    $routePath = Join-Path $dataDir 'routing-state.json'
    $egress = Join-Path $testRoot 'extensions\services\remote-provider-egress\compose.yaml'

    $plan = Get-ODSWindowsRemoteProviderSelections -InstallDir $testRoot
    Assert-Selection (-not $plan['remote-provider-egress'].Enabled -and -not $plan['remote-provider-ssh-tunnel'].Enabled) 'Fresh Core must exclude both services'

    [IO.File]::WriteAllText($routePath, '{"schema":"ods.remote-routing-state.v1","enabled":true,"provider":{"transport":"direct"}}')
    $plan = Get-ODSWindowsRemoteProviderSelections -InstallDir $testRoot
    Assert-Selection ($plan['remote-provider-egress'].Enabled -and -not $plan['remote-provider-ssh-tunnel'].Enabled) 'Direct route needs egress only'

    [IO.File]::WriteAllText($routePath, '{"schema":"ods.remote-routing-state.v1","enabled":true,"provider":{"transport":"ssh"}}')
    $plan = Get-ODSWindowsRemoteProviderSelections -InstallDir $testRoot
    Assert-Selection ($plan['remote-provider-egress'].Enabled -and $plan['remote-provider-ssh-tunnel'].Enabled) 'SSH route needs both services'

    Remove-Item -LiteralPath $routePath
    [IO.File]::WriteAllText($egress, 'old selected recipe')
    [IO.File]::WriteAllText("$egress.disabled", 'new reviewed recipe')
    $plan = Get-ODSWindowsRemoteProviderSelections -InstallDir $testRoot
    Assert-Selection ($plan['remote-provider-egress'].Enabled -and -not $plan['remote-provider-ssh-tunnel'].Enabled) 'Library selection must survive source copy'
    $result = Set-ODSWindowsExtensionComposeState -ComposePath $egress -Enabled $true -PreferDisabledRecipe $true
    Assert-Selection ($result -and -not (Test-Path -LiteralPath "$egress.disabled")) 'Enabled marker must be unique'
    Assert-Selection (([IO.File]::ReadAllText($egress)) -eq 'new reviewed recipe') 'Rerun must use the new recipe'

    [IO.File]::WriteAllText($routePath, '{broken')
    $failed = $false
    try { $null = Get-ODSWindowsRemoteProviderSelections -InstallDir $testRoot } catch { $failed = $true }
    Assert-Selection $failed 'Malformed route state must fail closed'
    Remove-Item -LiteralPath $routePath
    [IO.File]::WriteAllText($routePath, '{"schema":"ods.remote-routing-state.v1","enabled":true,"provider":{"transport":"direct"}}')
    Move-Item -LiteralPath $egress -Destination "$egress.disabled" -Force
    $failed = $false
    try { $null = Get-ODSWindowsRemoteProviderSelections -InstallDir $testRoot } catch { $failed = $true }
    Assert-Selection $failed 'Disabled egress cannot silently strand an active route'
    Write-Output '[PASS] native Windows remote-provider selection and retained route'
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
