# Pure argument translation for the Windows entrypoint. No installation side effects.
function Get-ODSWindowsPortalArguments {
    param([string[]]$Arguments)
    if ($Arguments -contains '--no-pixel') {
        throw 'The Windows WSL installer installs Pixel/Portal. For an intentional native installation without Portal, use install.ps1 -NativeWindows.'
    }
    $result = @($Arguments | Where-Object { $null -ne $_ })
    if ($result -notcontains '--pixel') { $result += '--pixel' }
    # --all must not implicitly replace the primary agent. An explicit Hermes
    # choice remains an optional extension alongside Pixel.
    if ($result -notcontains '--hermes' -and $result -notcontains '--no-hermes') {
        $result += '--no-hermes'
    }
    return $result
}

function Get-ODSWindowsPortalInstallPlan {
    param([System.Collections.IDictionary]$Parameters)
    # PSBoundParametersDictionary exposes Contains differently from Hashtable.
    $normalized = @{}
    foreach ($key in $Parameters.Keys) { $normalized[$key] = $Parameters[$key] }
    $Parameters = $normalized
    if ($Parameters.Contains('InstallDir') -and $Parameters['InstallDir']) {
        throw 'Portal installs inside Ubuntu/WSL. InstallDir is a native Windows option; use the WSL installer with INSTALL_DIR set to a Linux path, or explicitly select -NativeWindows.'
    }
    $arguments = @()
    # Process --all before individual overrides, matching the native installer.
    $flags = [ordered]@{
        All='--all'; DryRun='--dry-run'; Force='--force'; NonInteractive='--non-interactive'
        Voice='--voice'; Workflows='--workflows'; Rag='--rag'; Recommended='--recommended'
        NoRecommended='--no-recommended'; Hermes='--hermes'; OpenClaw='--openclaw'
        Cloud='--cloud'; Comfyui='--comfyui'; NoComfyui='--no-comfyui'
        Langfuse='--langfuse'; NoLangfuse='--no-langfuse'; NoBootstrap='--no-bootstrap'; Lan='--lan'
    }
    foreach ($name in $flags.Keys) {
        if ($Parameters.Contains($name) -and $Parameters[$name]) { $arguments += $flags[$name] }
    }
    if ($Parameters.Contains('Tier') -and $Parameters['Tier']) {
        $arguments += @('--tier', [string]$Parameters['Tier'])
    }
    if ($Parameters.Contains('SummaryJsonPath') -and $Parameters['SummaryJsonPath']) {
        $path = [string]$Parameters['SummaryJsonPath']
        if ($path -match '^([A-Za-z]):\\(.*)$') {
            $path = '/mnt/' + $Matches[1].ToLowerInvariant() + '/' + ($Matches[2] -replace '\\','/')
        } elseif (-not $path.StartsWith('/')) {
            throw 'SummaryJsonPath must be an absolute Windows drive path or Linux path for a WSL installation.'
        }
        $arguments += @('--summary-json', $path)
    }
    # Pixel is required, so an unqualified host fails instead of choosing Hermes.
    $arguments += '--pixel'
    if (-not $Parameters['Hermes'] -or $Parameters['NoHermes']) { $arguments += '--no-hermes' }
    return @{ PassthroughArgs = $arguments }
}
