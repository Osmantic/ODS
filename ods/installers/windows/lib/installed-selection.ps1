# Read the last selected Windows Compose stack before robocopy replaces the
# installed extension tree. Source manifests and data directories are not
# evidence that a user selected a service.
function Get-ODSWindowsInstalledFeatureSelection {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$InstallDir)

    $envPath = Join-Path $InstallDir ".env"
    $basePath = Join-Path $InstallDir "docker-compose.base.yml"
    $flagsPath = Join-Path $InstallDir ".compose-flags"
    $hasEnv = Test-Path -LiteralPath $envPath -PathType Leaf
    $hasBase = Test-Path -LiteralPath $basePath -PathType Leaf
    $hasFlags = Test-Path -LiteralPath $flagsPath -PathType Leaf
    if (-not $hasEnv -and -not $hasFlags) {
        return [PSCustomObject]@{ Kind = "fresh"; Features = $null; Reason = "" }
    }
    if (-not $hasEnv -or -not $hasBase) {
        return [PSCustomObject]@{ Kind = "unknown"; Features = $null; Reason = "installed root is incomplete" }
    }

    if (-not $hasFlags) {
        return [PSCustomObject]@{ Kind = "unknown"; Features = $null; Reason = "installed .compose-flags is missing" }
    }
    $raw = Get-Content -LiteralPath $flagsPath -Raw -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return [PSCustomObject]@{ Kind = "unknown"; Features = $null; Reason = "installed .compose-flags is empty" }
    }

    $tokens = @($raw.Trim() -split '\s+' | Where-Object { $_ })
    $baseSelected = $false
    $selected = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    for ($i = 0; $i -lt $tokens.Count; $i++) {
        if ($tokens[$i] -ne "-f") { continue }
        if ($i + 1 -ge $tokens.Count) {
            return [PSCustomObject]@{ Kind = "unknown"; Features = $null; Reason = "incomplete -f pair" }
        }
        $fragment = $tokens[++$i] -replace '\\', '/'
        if ($fragment -eq "docker-compose.base.yml") { $baseSelected = $true }
        if ($fragment -match '^extensions/services/([a-z0-9-]+)/compose\.yaml$') {
            [void]$selected.Add($Matches[1])
        }
    }
    if (-not $baseSelected) {
        return [PSCustomObject]@{ Kind = "unknown"; Features = $null; Reason = "base Compose fragment is absent" }
    }
    foreach ($pair in @(@('whisper','tts'), @('qdrant','embeddings'),
            @('hermes','hermes-proxy'))) {
        if ($selected.Contains($pair[0]) -ne $selected.Contains($pair[1])) {
            return [PSCustomObject]@{
                Kind = "unknown"; Features = $null
                Reason = "installed $($pair[0])/$($pair[1]) group has a partial selection"
            }
        }
    }
    # LiteLLM can be required by cloud Core or the native switchboard without
    # enabling the optional Recommended bundle.
    if ($selected.Contains("token-spy") -and -not $selected.Contains("litellm")) {
        return [PSCustomObject]@{
            Kind = "unknown"; Features = $null
            Reason = "installed recommended group has a partial selection"
        }
    }
    if ($selected.Contains("litellm") -and -not $selected.Contains("token-spy")) {
        $savedEnv = @(Get-Content -LiteralPath $envPath)
        $installedMode = $savedEnv | Where-Object { $_ -match '^ODS_MODE=' } | Select-Object -First 1
        $switchboardMode = $savedEnv | Where-Object { $_ -match '^ODS_MODEL_SWITCHBOARD=' } | Select-Object -First 1
        $previousWebuiRoute = $savedEnv | Where-Object { $_ -match '^OPEN_WEBUI_LLM_BASE_URL=' } | Select-Object -First 1
        $transitionFromGateway = ([string]$switchboardMode -match '^ODS_MODEL_SWITCHBOARD=(legacy|observe)\s*$' -and
            [string]$previousWebuiRoute -match '^OPEN_WEBUI_LLM_BASE_URL=http://litellm:4000(/v1)?\s*$')
        if ([string]$installedMode -notmatch '^ODS_MODE=cloud\s*$' -and
            [string]$switchboardMode -notmatch '^ODS_MODEL_SWITCHBOARD=enabled\s*$' -and
            -not $transitionFromGateway) {
            return [PSCustomObject]@{
                Kind = "unknown"; Features = $null
                Reason = "installed LiteLLM selection has no matching cloud or switchboard mode"
            }
        }
    }

    $features = @{
        Voice = ($selected.Contains("whisper") -or $selected.Contains("tts"))
        Workflows = $selected.Contains("n8n")
        Rag = ($selected.Contains("qdrant") -or $selected.Contains("embeddings"))
        Recommended = $selected.Contains("token-spy")
        Hermes = ($selected.Contains("hermes") -or $selected.Contains("hermes-proxy"))
        OpenClaw = $selected.Contains("openclaw")
        Comfyui = $selected.Contains("comfyui")
        DeepResearch = $selected.Contains("perplexica")
        PrivacyShield = $selected.Contains("privacy-shield")
        Langfuse = $selected.Contains("langfuse")
    }
    return [PSCustomObject]@{ Kind = "preserved"; Features = $features; Reason = "" }
}
