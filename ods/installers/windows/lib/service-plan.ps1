# ============================================================================
# ODS Windows Installer -- Service Plan
# ============================================================================
# Purpose:
#   Keep extension compose discovery aligned with the feature choices selected
#   in Phase 03. The manifest scanner is intentionally generic, but install
#   decisions are explicit so Core Only cannot accidentally start optional
#   services just because their compose.yaml exists on disk.
# ============================================================================

function New-ODSWindowsServicePlanEntry {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceId,
        [Parameter(Mandatory = $true)][bool]$Enabled,
        [Parameter(Mandatory = $true)][string]$Group,
        [Parameter(Mandatory = $true)][string]$DisabledReason
    )

    [PSCustomObject]@{
        ServiceId      = $ServiceId
        Enabled        = $Enabled
        Group          = $Group
        DisabledReason = $DisabledReason
    }
}

function Get-ODSWindowsInstalledServiceSelection {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [Parameter(Mandatory = $true)][string]$ServiceId
    )

    # Read before the installer copies fresh source over the installed tree.
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir ".env") -PathType Leaf)) {
        return $null
    }
    $extensions = Join-Path $InstallDir "extensions"
    $services = Join-Path $extensions "services"
    $serviceDir = Join-Path $services $ServiceId
    $active = Join-Path $serviceDir "compose.yaml"
    $disabled = "$active.disabled"
    foreach ($path in @($extensions, $services, $serviceDir, $active, $disabled)) {
        $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Unsafe installed $ServiceId selection path: $path"
        }
    }
    $hasActive = Test-Path -LiteralPath $active -PathType Leaf
    $hasDisabled = Test-Path -LiteralPath $disabled -PathType Leaf
    if ($hasActive -and $hasDisabled) {
        throw "Ambiguous installed $ServiceId selection: both Compose markers exist"
    }
    if ($hasActive) { return $true }
    if ($hasDisabled) { return $false }

    # Older native installs recorded their selected Compose stack in this
    # flags file before Library actions began renaming per-service fragments.
    $flagsPath = Join-Path $InstallDir ".compose-flags"
    $flagsItem = Get-Item -LiteralPath $flagsPath -Force -ErrorAction SilentlyContinue
    if ($flagsItem) {
        if ($flagsItem.PSIsContainer -or ($flagsItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Unsafe installed Compose flags path: $flagsPath"
        }
        $tokens = @((Get-Content -LiteralPath $flagsPath -Raw -ErrorAction Stop).Trim() -split '\s+' |
            Where-Object { $_ })
        $baseSelected = $false
        $serviceSelected = $false
        for ($i = 0; $i -lt $tokens.Count; $i++) {
            if ($tokens[$i] -ne "-f") { continue }
            if (++$i -ge $tokens.Count) { throw "Incomplete installed Compose flags: $flagsPath" }
            $fragment = $tokens[$i] -replace '\\', '/'
            if ($fragment -eq "docker-compose.base.yml") { $baseSelected = $true }
            if ($fragment -eq "extensions/services/$ServiceId/compose.yaml") { $serviceSelected = $true }
        }
        if (-not $baseSelected) { throw "Installed Compose flags lack base stack: $flagsPath" }
        return $serviceSelected
    }
    return $null
}

function Resolve-ODSWindowsVoiceSelection {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [bool]$ComputedVoice,
        [bool]$CliEnable,
        [bool]$CliDisable,
        [bool]$All,
        [bool]$MenuExplicit
    )

    # Inspect before source copy even for explicit choices. An unsafe marker
    # must never be silently overwritten by the later Compose reconciliation.
    $installedWhisper = Get-ODSWindowsInstalledServiceSelection -InstallDir $InstallDir -ServiceId "whisper"
    $installedTts = Get-ODSWindowsInstalledServiceSelection -InstallDir $InstallDir -ServiceId "tts"
    $whisper = $ComputedVoice
    $tts = $ComputedVoice
    if ($CliDisable) {
        $whisper = $false
        $tts = $false
    } elseif ($CliEnable -or $All) {
        $whisper = $true
        $tts = $true
    } elseif (-not $MenuExplicit) {
        if ($null -ne $installedWhisper) { $whisper = [bool]$installedWhisper }
        if ($null -ne $installedTts) { $tts = [bool]$installedTts }
    }
    return [PSCustomObject]@{ Whisper = $whisper; Tts = $tts }
}

function Resolve-ODSWindowsHermesSelection {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [bool]$ComputedHermes,
        [Nullable[bool]]$ComputedProxy = $null,
        [bool]$CliEnable,
        [bool]$CliDisable,
        [bool]$All,
        [bool]$MenuExplicit
    )

    # Validate retained markers before any explicit override can copy source
    # over an unsafe or ambiguous installed selection.
    $installedHermes = Get-ODSWindowsInstalledServiceSelection -InstallDir $InstallDir -ServiceId "hermes"
    $installedProxy = Get-ODSWindowsInstalledServiceSelection -InstallDir $InstallDir -ServiceId "hermes-proxy"
    $hermes = $ComputedHermes
    $proxy = if ($null -ne $ComputedProxy) { [bool]$ComputedProxy } else { $ComputedHermes }
    if ($CliDisable) {
        $hermes = $false
        $proxy = $false
    } elseif ($CliEnable) {
        $hermes = $true
        $proxy = $true
    } elseif (-not $All -and -not $MenuExplicit) {
        if (Test-Path -LiteralPath (Join-Path $InstallDir ".env") -PathType Leaf) {
            # An existing install with no Hermes fragments has not selected it.
            # Do not re-enable it from a computed default on a quiet rerun.
            $hermes = if ($null -ne $installedHermes) { [bool]$installedHermes } else { $false }
            $proxy = if ($null -ne $installedProxy) { [bool]$installedProxy } else {
                # Older native installs selected the agent and proxy together.
                $hermes
            }
        }
    }
    if ($proxy -and -not $hermes) {
        throw "Hermes proxy requires Hermes; disable its proxy or enable Hermes first."
    }
    return [PSCustomObject]@{ Hermes = $hermes; Proxy = $proxy }
}

function New-ODSWindowsServicePlan {
    param(
        [bool]$EnableRecommended,
        [bool]$EnableVoice,
        [Nullable[bool]]$EnableWhisper = $null,
        [Nullable[bool]]$EnableTts = $null,
        [bool]$EnableWorkflows,
        [bool]$EnableRag,
        [bool]$EnableHermes,
        [Nullable[bool]]$EnableHermesProxy = $null,
        [bool]$EnableOpenClaw,
        [bool]$EnableComfyui,
        [bool]$EnableDeepResearch,
        [bool]$EnablePrivacyShield,
        [bool]$EnableBraveSearch = $false,
        [bool]$EnableODSProxy = $false,
        [bool]$EnableRemoteAccess = $false
    )

    $plan = @{}
    $proxyEnabled = if ($null -eq $EnableHermesProxy) { $EnableHermes } else { [bool]$EnableHermesProxy }
    $whisperEnabled = if ($null -eq $EnableWhisper) { $EnableVoice } else { [bool]$EnableWhisper }
    $ttsEnabled = if ($null -eq $EnableTts) { $EnableVoice } else { [bool]$EnableTts }

    $enableSearxng = Test-ODSWindowsSearxngNeeded `
        -EnableRecommended $EnableRecommended `
        -EnableDeepResearch $EnableDeepResearch `
        -EnableHermes $EnableHermes `
        -EnableOpenClaw $EnableOpenClaw
    $plan["litellm"] = New-ODSWindowsServicePlanEntry "litellm" $EnableRecommended "recommended" "recommended services not enabled"
    $plan["searxng"] = New-ODSWindowsServicePlanEntry "searxng" $enableSearxng "search" "web search backend not required"
    $plan["token-spy"] = New-ODSWindowsServicePlanEntry "token-spy" $EnableRecommended "recommended" "recommended services not enabled"

    $plan["whisper"] = New-ODSWindowsServicePlanEntry "whisper" $whisperEnabled "voice" "Whisper not enabled"
    $plan["tts"] = New-ODSWindowsServicePlanEntry "tts" $ttsEnabled "voice" "Kokoro not enabled"

    $plan["n8n"] = New-ODSWindowsServicePlanEntry "n8n" $EnableWorkflows "workflows" "workflows not enabled"
    $plan["qdrant"] = New-ODSWindowsServicePlanEntry "qdrant" $EnableRag "rag" "RAG not enabled"
    $plan["embeddings"] = New-ODSWindowsServicePlanEntry "embeddings" $EnableRag "rag" "RAG not enabled"

    $plan["hermes"] = New-ODSWindowsServicePlanEntry "hermes" $EnableHermes "agents" "Hermes agent not enabled"
    $plan["hermes-proxy"] = New-ODSWindowsServicePlanEntry "hermes-proxy" $proxyEnabled "agents" "Hermes proxy not enabled"
    $plan["openclaw"] = New-ODSWindowsServicePlanEntry "openclaw" $EnableOpenClaw "legacy-agents" "OpenClaw is deprecated and was not explicitly enabled"
    $plan["ape"] = New-ODSWindowsServicePlanEntry "ape" ($EnableHermes -or $EnableOpenClaw) "agents" "agent governance not needed without an enabled agent"
    # Pixel's current trusted host runtime is installed by the Linux installer
    # on native Linux or Ubuntu 24.04 under WSL2.  The native Windows installer
    # must not inherit the manifest's generic `core` fallback and start only the
    # edge proxy: without the private host ingress behind it that creates a
    # broken, misleading Pixel surface (and fails Compose secret interpolation).
    $plan["pixel-edge"] = New-ODSWindowsServicePlanEntry "pixel-edge" $false "agents" "Pixel requires the ODS Linux installer in Ubuntu 24.04 WSL2"
    # The relay is also Pixel-only. Its manifest is tagged core for the Linux
    # installer, but native Windows has no Pixel host or relay bearer key.
    # Do not let the generic core fallback enable it during Compose discovery.
    $plan["pixel-model-relay"] = New-ODSWindowsServicePlanEntry "pixel-model-relay" $false "agents" "Pixel requires the ODS Linux installer in Ubuntu 24.04 WSL2"

    $plan["comfyui"] = New-ODSWindowsServicePlanEntry "comfyui" $EnableComfyui "image" "image generation not enabled"
    $plan["perplexica"] = New-ODSWindowsServicePlanEntry "perplexica" $EnableDeepResearch "research" "deep research not enabled"
    $plan["privacy-shield"] = New-ODSWindowsServicePlanEntry "privacy-shield" $EnablePrivacyShield "privacy" "privacy shield not enabled"

    $plan["brave-search"] = New-ODSWindowsServicePlanEntry "brave-search" $EnableBraveSearch "search" "Brave Search API not configured"
    $plan["ods-proxy"] = New-ODSWindowsServicePlanEntry "ods-proxy" $EnableODSProxy "networking" "LAN web proxy not enabled"
    $plan["tailscale"] = New-ODSWindowsServicePlanEntry "tailscale" $EnableRemoteAccess "networking" "remote access not enabled"

    return $plan
}

function Test-ODSWindowsSearxngNeeded {
    <#
    .SYNOPSIS
        SearXNG is required for Open WebUI web search, Perplexica, and agent web tools.
        It is not only a "recommended" extra.
    #>
    param(
        [bool]$EnableRecommended = $false,
        [bool]$EnableDeepResearch = $false,
        [bool]$EnableHermes = $false,
        [bool]$EnableOpenClaw = $false
    )

    return [bool]($EnableRecommended -or $EnableDeepResearch -or $EnableHermes -or $EnableOpenClaw)
}

function Get-ODSWindowsServicePlanDecision {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceId,
        [string]$Category = "",
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [bool]$EnableRecommended = $false
    )

    if ($Plan.ContainsKey($ServiceId)) {
        return $Plan[$ServiceId]
    }

    switch ($Category) {
        "core" {
            return New-ODSWindowsServicePlanEntry $ServiceId $true "core" "core services are always enabled"
        }
        "recommended" {
            return New-ODSWindowsServicePlanEntry $ServiceId $EnableRecommended "recommended" "recommended services not enabled"
        }
        "optional" {
            return New-ODSWindowsServicePlanEntry $ServiceId $false "optional" "optional extension not selected by installer service plan"
        }
        default {
            return New-ODSWindowsServicePlanEntry $ServiceId $false "unknown" "extension category is not selected by installer service plan"
        }
    }
}

function Test-ODSWindowsServiceEnabled {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceId,
        [Parameter(Mandatory = $true)][hashtable]$Plan
    )

    return ($Plan.ContainsKey($ServiceId) -and $Plan[$ServiceId].Enabled)
}

function Set-ODSWindowsExtensionComposeState {
    param(
        [Parameter(Mandatory = $true)][string]$ComposePath,
        [Parameter(Mandatory = $true)][bool]$Enabled
    )

    $disabledPath = "$ComposePath.disabled"

    if ($Enabled) {
        if (Test-Path -LiteralPath $disabledPath) {
            if (Test-Path -LiteralPath $ComposePath) {
                Remove-Item -LiteralPath $disabledPath -Force
            } else {
                Move-Item -LiteralPath $disabledPath -Destination $ComposePath -Force
            }
        }
        return (Test-Path -LiteralPath $ComposePath)
    }

    if (Test-Path -LiteralPath $ComposePath) {
        if (Test-Path -LiteralPath $disabledPath) {
            Remove-Item -LiteralPath $disabledPath -Force
        }
        Move-Item -LiteralPath $ComposePath -Destination $disabledPath -Force
    }
    return $false
}
