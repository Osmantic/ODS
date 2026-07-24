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

    $plan["litellm"] = New-ODSWindowsServicePlanEntry "litellm" $EnableRecommended "recommended" "recommended services not enabled"
    $plan["searxng"] = New-ODSWindowsServicePlanEntry "searxng" $EnableRecommended "recommended" "recommended services not enabled"
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

    $plan["comfyui"] = New-ODSWindowsServicePlanEntry "comfyui" $EnableComfyui "image" "image generation not enabled"
    $plan["perplexica"] = New-ODSWindowsServicePlanEntry "perplexica" $EnableDeepResearch "research" "deep research not enabled"
    $plan["privacy-shield"] = New-ODSWindowsServicePlanEntry "privacy-shield" $EnablePrivacyShield "privacy" "privacy shield not enabled"

    $plan["brave-search"] = New-ODSWindowsServicePlanEntry "brave-search" $EnableBraveSearch "search" "Brave Search API not configured"
    $plan["ods-proxy"] = New-ODSWindowsServicePlanEntry "ods-proxy" $EnableODSProxy "networking" "LAN web proxy not enabled"
    $plan["tailscale"] = New-ODSWindowsServicePlanEntry "tailscale" $EnableRemoteAccess "networking" "remote access not enabled"

    return $plan
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
