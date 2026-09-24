# Select placement from the existing Windows hardware detector. This describes
# ownership, not successful deployment: readiness must still be proved by the
# selected runtime and the Pixel gateway before installation can succeed.
function Get-ODSWindowsPortalRuntimePlan {
    param([Parameter(Mandatory=$true)][hashtable]$GpuInfo, [switch]$Cloud)
    if ($Cloud) {
        return @{ Agent='pixel'; AgentHost='wsl'; InferenceHost='provider'; Backend='cloud'; ManagedLocally=$false }
    }
    switch ([string]$GpuInfo.Backend) {
        'nvidia' {
            return @{ Agent='pixel'; AgentHost='wsl'; InferenceHost='docker'; Backend='cuda'; ManagedLocally=$true }
        }
        'amd' {
            return @{ Agent='pixel'; AgentHost='wsl'; InferenceHost='windows'; Backend='vulkan'; ManagedLocally=$true }
        }
        'none' {
            return @{ Agent='pixel'; AgentHost='wsl'; InferenceHost='docker'; Backend='cpu'; ManagedLocally=$true }
        }
        default { throw "Unsupported Windows GPU detection result: '$($GpuInfo.Backend)'" }
    }
}
