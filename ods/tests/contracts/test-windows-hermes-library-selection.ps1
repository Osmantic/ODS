$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $root "installers\windows\lib\service-plan.ps1")

function Assert-Selection {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$scratch = Join-Path ([IO.Path]::GetTempPath()) ("ods-hermes-selection-" + [guid]::NewGuid().ToString("N"))
$installDir = Join-Path $scratch "install"
$hermesDir = Join-Path $installDir "extensions\services\hermes"
$proxyDir = Join-Path $installDir "extensions\services\hermes-proxy"
$hermesActive = Join-Path $hermesDir "compose.yaml"
$proxyActive = Join-Path $proxyDir "compose.yaml"
try {
    New-Item -ItemType Directory -Path $hermesDir, $proxyDir -Force | Out-Null
    $fresh = Resolve-ODSWindowsHermesSelection -InstallDir $installDir -ComputedHermes $false
    Assert-Selection (-not $fresh.Hermes -and -not $fresh.Proxy) "Fresh lean choice changed"
    $full = Resolve-ODSWindowsHermesSelection -InstallDir $installDir -ComputedHermes $true
    Assert-Selection ($full.Hermes -and $full.Proxy) "Explicit full default lost its paired proxy"

    Set-Content -LiteralPath (Join-Path $installDir ".env") -Value "ODS_MODE=local"
    $emptyRetained = Resolve-ODSWindowsHermesSelection -InstallDir $installDir -ComputedHermes $true
    Assert-Selection (-not $emptyRetained.Hermes -and -not $emptyRetained.Proxy) "Existing install without Hermes markers enabled Hermes"
    $explicitFull = Resolve-ODSWindowsHermesSelection -InstallDir $installDir -ComputedHermes $true -All $true
    Assert-Selection ($explicitFull.Hermes -and $explicitFull.Proxy) "Explicit All did not enable Hermes and proxy"
    $legacyFlags = Join-Path $installDir ".compose-flags"
    Set-Content -LiteralPath $legacyFlags -Value "--env-file .env -f docker-compose.base.yml -f extensions/services/hermes/compose.yaml -f extensions/services/hermes-proxy/compose.yaml"
    $legacy = Resolve-ODSWindowsHermesSelection -InstallDir $installDir -ComputedHermes $false
    Assert-Selection ($legacy.Hermes -and $legacy.Proxy) "Legacy selected Hermes flags were lost without Compose markers"
    Set-Content -LiteralPath $legacyFlags -Value "--env-file .env -f docker-compose.base.yml"
    $legacyCore = Resolve-ODSWindowsHermesSelection -InstallDir $installDir -ComputedHermes $true
    Assert-Selection (-not $legacyCore.Hermes -and -not $legacyCore.Proxy) "Legacy Core flags surprise-enabled Hermes"
    Remove-Item -LiteralPath $legacyFlags -Force
    Set-Content -LiteralPath $hermesActive -Value "services: {}"
    Set-Content -LiteralPath "$proxyActive.disabled" -Value "services: {}"
    $dataDir = Join-Path $installDir "data\hermes"
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    $sentinel = Join-Path $dataDir "retained.txt"
    Set-Content -LiteralPath $sentinel -Value "keep"

    $retained = Resolve-ODSWindowsHermesSelection -InstallDir $installDir -ComputedHermes $true
    Assert-Selection ($retained.Hermes -and -not $retained.Proxy) "Hermes-only Library choice was lost"
    $plan = New-ODSWindowsServicePlan -EnableHermes $retained.Hermes -EnableHermesProxy $retained.Proxy
    Assert-Selection ($plan["hermes"].Enabled -and -not $plan["hermes-proxy"].Enabled) "Plan re-coupled proxy"

    # Robocopy can restore the active source file beside the disabled marker.
    # The resolved plan must reconcile that pair without touching saved data.
    Set-Content -LiteralPath $proxyActive -Value "services: {}"
    $null = Set-ODSWindowsExtensionComposeState -ComposePath $proxyActive -Enabled $plan["hermes-proxy"].Enabled
    Assert-Selection ((-not (Test-Path -LiteralPath $proxyActive)) -and
        (Test-Path -LiteralPath "$proxyActive.disabled")) "Source copy re-enabled proxy"
    Assert-Selection ((Get-Content -LiteralPath $sentinel -Raw).Trim() -eq "keep") "Hermes data changed"

    $disabled = Resolve-ODSWindowsHermesSelection -InstallDir $installDir -ComputedHermes $true -CliDisable $true
    Assert-Selection (-not $disabled.Hermes -and -not $disabled.Proxy) "Explicit disable did not affect both"
    $enabled = Resolve-ODSWindowsHermesSelection -InstallDir $installDir -ComputedHermes $false -CliEnable $true
    Assert-Selection ($enabled.Hermes -and $enabled.Proxy) "Explicit enable did not affect both"
    $menu = Resolve-ODSWindowsHermesSelection -InstallDir $installDir -ComputedHermes $false -MenuExplicit $true
    Assert-Selection (-not $menu.Hermes -and -not $menu.Proxy) "Explicit Core menu choice was ignored"

    Move-Item -LiteralPath $hermesActive -Destination "$hermesActive.disabled"
    Move-Item -LiteralPath "$proxyActive.disabled" -Destination $proxyActive
    $before = @(Get-ChildItem -LiteralPath $hermesDir, $proxyDir -File | Select-Object -ExpandProperty FullName | Sort-Object)
    $rejected = $false
    try { $null = Resolve-ODSWindowsHermesSelection -InstallDir $installDir -ComputedHermes $false }
    catch { $rejected = $_.Exception.Message -like "Hermes proxy requires Hermes*" }
    Assert-Selection $rejected "Proxy-only installed choice did not fail before copy"
    $after = @(Get-ChildItem -LiteralPath $hermesDir, $proxyDir -File | Select-Object -ExpandProperty FullName | Sort-Object)
    Assert-Selection ((@($before) -join '|') -eq (@($after) -join '|')) "Rejected selection mutated markers"
    Assert-Selection ((Get-Content -LiteralPath $sentinel -Raw).Trim() -eq "keep") "Rejected selection touched data"

    Write-Output "PASS: native Windows Hermes Library selection and rerun reconciliation"
} finally {
    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
}
