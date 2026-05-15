# ============================================================================
# Dream Server Windows Installer -- Service Plan
# ============================================================================
# Purpose:
#   Keep extension compose discovery aligned with the feature choices selected
#   in Phase 03. The manifest scanner is intentionally generic, but install
#   decisions are explicit so Core Only cannot accidentally start optional
#   services just because their compose.yaml exists on disk.
# ============================================================================

function New-DreamWindowsServicePlanEntry {
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

function New-DreamWindowsServicePlan {
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
        [bool]$EnableDreamProxy = $false,
        [bool]$EnableRemoteAccess = $false
    )

    $plan = @{}

    $plan["litellm"] = New-DreamWindowsServicePlanEntry "litellm" $EnableRecommended "recommended" "recommended services not enabled"
    $plan["searxng"] = New-DreamWindowsServicePlanEntry "searxng" $EnableRecommended "recommended" "recommended services not enabled"
    $plan["token-spy"] = New-DreamWindowsServicePlanEntry "token-spy" $EnableRecommended "recommended" "recommended services not enabled"

    $plan["whisper"] = New-DreamWindowsServicePlanEntry "whisper" $EnableVoice "voice" "voice not enabled"
    $plan["tts"] = New-DreamWindowsServicePlanEntry "tts" $EnableVoice "voice" "voice not enabled"

    $plan["n8n"] = New-DreamWindowsServicePlanEntry "n8n" $EnableWorkflows "workflows" "workflows not enabled"
    $plan["qdrant"] = New-DreamWindowsServicePlanEntry "qdrant" $EnableRag "rag" "RAG not enabled"
    $plan["embeddings"] = New-DreamWindowsServicePlanEntry "embeddings" $EnableRag "rag" "RAG not enabled"

    $plan["hermes"] = New-DreamWindowsServicePlanEntry "hermes" $EnableHermes "agents" "Hermes agent not enabled"
    $plan["hermes-proxy"] = New-DreamWindowsServicePlanEntry "hermes-proxy" $EnableHermes "agents" "Hermes agent not enabled"
    $plan["openclaw"] = New-DreamWindowsServicePlanEntry "openclaw" $EnableOpenClaw "legacy-agents" "OpenClaw is deprecated and was not explicitly enabled"
    $plan["ape"] = New-DreamWindowsServicePlanEntry "ape" ($EnableHermes -or $EnableOpenClaw) "agents" "agent governance not needed without an enabled agent"

    $plan["comfyui"] = New-DreamWindowsServicePlanEntry "comfyui" $EnableComfyui "image" "image generation not enabled"
    $plan["perplexica"] = New-DreamWindowsServicePlanEntry "perplexica" $EnableDeepResearch "research" "deep research not enabled"
    $plan["privacy-shield"] = New-DreamWindowsServicePlanEntry "privacy-shield" $EnablePrivacyShield "privacy" "privacy shield not enabled"

    $plan["brave-search"] = New-DreamWindowsServicePlanEntry "brave-search" $EnableBraveSearch "search" "Brave Search API not configured"
    $plan["dream-proxy"] = New-DreamWindowsServicePlanEntry "dream-proxy" $EnableDreamProxy "networking" "LAN web proxy not enabled"
    $plan["tailscale"] = New-DreamWindowsServicePlanEntry "tailscale" $EnableRemoteAccess "networking" "remote access not enabled"

    return $plan
}

function Get-DreamWindowsServicePlanDecision {
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
            return New-DreamWindowsServicePlanEntry $ServiceId $true "core" "core services are always enabled"
        }
        "recommended" {
            return New-DreamWindowsServicePlanEntry $ServiceId $EnableRecommended "recommended" "recommended services not enabled"
        }
        "optional" {
            return New-DreamWindowsServicePlanEntry $ServiceId $false "optional" "optional extension not selected by installer service plan"
        }
        default {
            return New-DreamWindowsServicePlanEntry $ServiceId $false "unknown" "extension category is not selected by installer service plan"
        }
    }
}

function Test-DreamWindowsServiceEnabled {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceId,
        [Parameter(Mandatory = $true)][hashtable]$Plan
    )

    return ($Plan.ContainsKey($ServiceId) -and $Plan[$ServiceId].Enabled)
}
