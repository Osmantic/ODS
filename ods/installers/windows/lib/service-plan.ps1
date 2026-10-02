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

function New-ODSWindowsServicePlan {
    param(
        [bool]$EnableRecommended,
        [bool]$EnableVoice,
        [bool]$EnableWorkflows,
        [bool]$EnableRag,
        [bool]$EnableHermes,
        [bool]$EnableOpenClaw,
        [bool]$EnableComfyui,
        [bool]$EnableDeepResearch,
        [bool]$EnablePrivacyShield,
        [bool]$EnableBraveSearch = $false,
        [bool]$EnableODSProxy = $false,
        [bool]$EnableRemoteAccess = $false
    )

    $plan = @{}

    $enableSearxng = Test-ODSWindowsSearxngNeeded `
        -EnableRecommended $EnableRecommended `
        -EnableDeepResearch $EnableDeepResearch `
        -EnableHermes $EnableHermes `
        -EnableOpenClaw $EnableOpenClaw
    $plan["litellm"] = New-ODSWindowsServicePlanEntry "litellm" $EnableRecommended "recommended" "recommended services not enabled"
    $plan["searxng"] = New-ODSWindowsServicePlanEntry "searxng" $enableSearxng "search" "web search backend not required"
    $plan["token-spy"] = New-ODSWindowsServicePlanEntry "token-spy" $EnableRecommended "recommended" "recommended services not enabled"

    $plan["whisper"] = New-ODSWindowsServicePlanEntry "whisper" $EnableVoice "voice" "voice not enabled"
    $plan["tts"] = New-ODSWindowsServicePlanEntry "tts" $EnableVoice "voice" "voice not enabled"

    $plan["n8n"] = New-ODSWindowsServicePlanEntry "n8n" $EnableWorkflows "workflows" "workflows not enabled"
    $plan["qdrant"] = New-ODSWindowsServicePlanEntry "qdrant" $EnableRag "rag" "RAG not enabled"
    $plan["embeddings"] = New-ODSWindowsServicePlanEntry "embeddings" $EnableRag "rag" "RAG not enabled"

    $plan["hermes"] = New-ODSWindowsServicePlanEntry "hermes" $EnableHermes "agents" "Hermes agent not enabled"
    $plan["hermes-proxy"] = New-ODSWindowsServicePlanEntry "hermes-proxy" $EnableHermes "agents" "Hermes agent not enabled"
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

function Get-ODSWindowsRemoteProviderSelections {
    <# Preserve Library choices and active routes when the two internal
       remote-provider services move out of base Compose. #>
    param([Parameter(Mandatory = $true)][string]$InstallDir)

    $routePath = Join-Path $InstallDir "data\remote-provider\routing-state.json"
    $transport = ""
    foreach ($directory in @((Join-Path $InstallDir 'data'), (Join-Path $InstallDir 'data\remote-provider'))) {
        if (Test-Path -LiteralPath $directory) {
            $item = Get-Item -LiteralPath $directory -Force
            if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'Unsafe remote-provider state directory'
            }
        }
    }
    if (Test-Path -LiteralPath $routePath) {
        $routeItem = Get-Item -LiteralPath $routePath -Force
        if ($routeItem.PSIsContainer -or $routeItem.Length -gt 1048576 -or
            ($routeItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Unsafe remote-provider route state"
        }
        try {
            $route = Get-Content -LiteralPath $routePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "Remote-provider route state is unreadable or invalid"
        }
        if ($route.schema -cne "ods.remote-routing-state.v1" -or $route.enabled -isnot [bool]) {
            throw "Remote-provider route state has an invalid contract"
        }
        if ($route.enabled) {
            $transport = [string]$route.provider.transport
            if ($transport -cnotin @("direct", "ssh")) {
                throw "Enabled remote-provider route has an unknown transport"
            }
        }
    }

    $selection = @{}
    foreach ($serviceId in @("remote-provider-egress", "remote-provider-ssh-tunnel")) {
        $serviceDir = Join-Path (Join-Path $InstallDir "extensions\services") $serviceId
        if (Test-Path -LiteralPath $serviceDir) {
            $directoryItem = Get-Item -LiteralPath $serviceDir -Force
            if (-not $directoryItem.PSIsContainer -or
                ($directoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Unsafe remote-provider service directory: $serviceId"
            }
        }
        $active = Join-Path $serviceDir "compose.yaml"
        $disabled = "$active.disabled"
        $hasActive = Test-Path -LiteralPath $active
        $hasDisabled = Test-Path -LiteralPath $disabled
        foreach ($marker in @($active, $disabled)) {
            if (Test-Path -LiteralPath $marker) {
                $item = Get-Item -LiteralPath $marker -Force
                if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw "Unsafe remote-provider Compose marker: $serviceId"
                }
            }
        }
        $required = $transport -ceq "ssh" -or ($transport -ceq "direct" -and $serviceId -ceq "remote-provider-egress")
        if ($hasActive -and $hasDisabled) {
            throw "Ambiguous remote-provider Compose markers: $serviceId"
        }
        # Source copy preserves both marker names. A genuine disabled choice
        # must not be mistaken for a newly copied recipe during an upgrade.
        $enabled = if ($hasActive) { $true } elseif ($hasDisabled) { $false } else { $required }
        if ($required -and -not $enabled) {
            throw "Active remote-provider route requires enabled $serviceId"
        }
        $selection[$serviceId] = New-ODSWindowsServicePlanEntry $serviceId $enabled "remote-provider" "remote-provider service not selected"
    }
    return $selection
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
