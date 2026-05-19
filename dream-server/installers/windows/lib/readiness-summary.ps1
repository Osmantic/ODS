# ============================================================================
# Dream Server Windows -- Install readiness summary
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Print a concise post-install summary showing which services are
#          ready now and which need attention.
# Requires: ui.ps1 (Write-AI, Write-AIWarn, Write-Host colors) sourced first.
# ============================================================================

function Test-DreamReadinessHttp {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [int]$TimeoutSec = 3
    )

    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.Timeout = $TimeoutSec * 1000
        $req.Method = "GET"
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $resp.Close()
        return @{ Code = $code; Ready = ($code -ge 200 -and $code -lt 400) }
    }
    catch [System.Net.WebException] {
        $webResp = $_.Exception.Response
        if ($webResp) {
            $code = [int]$webResp.StatusCode
            return @{ Code = $code; Ready = ($code -eq 401 -or $code -eq 403) }
        }
        return @{ Code = 0; Ready = $false }
    }
    catch {
        return @{ Code = 0; Ready = $false }
    }
}

function Get-DreamReadinessContainerState {
    param([string]$Container)

    if ([string]::IsNullOrWhiteSpace($Container)) { return "host" }
    try {
        $state = & docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $Container 2>$null
        if ($LASTEXITCODE -eq 0 -and $state) { return $state.ToString().Trim() }
    }
    catch { }
    return "missing"
}

function Write-DreamInstallReadinessSummary {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Checks,
        [string]$StatusCommand = ".\dream.ps1 status",
        [string]$LogPath = "",
        [string]$DashboardUrl = "http://localhost:3001"
    )

    $ready = New-Object System.Collections.Generic.List[string]
    $attention = New-Object System.Collections.Generic.List[string]

    foreach ($check in $Checks) {
        if (-not $check.Name -or -not $check.Url) { continue }

        $http = Test-DreamReadinessHttp -Url $check.Url
        $containerState = Get-DreamReadinessContainerState -Container $check.Container
        $openUrl = if ($check.OpenUrl) { $check.OpenUrl } else { $check.Url }

        if ($http.Ready) {
            [void]$ready.Add(("{0,-28} {1} (HTTP {2})" -f $check.Name, $openUrl, $http.Code))
        }
        else {
            $state = "starting"
            $detail = "HTTP $($http.Code)"
            if ($containerState -and $containerState -notin @("running", "starting", "host")) {
                $state = "needs attention"
                $detail = "container $containerState, HTTP $($http.Code)"
            }
            if ($check.Hint) {
                $detail = "$detail; $($check.Hint)"
            }
            [void]$attention.Add(("{0,-28} {1} - {2}" -f $check.Name, $state, $detail))
        }
    }

    if (($ready.Count + $attention.Count) -eq 0) { return }

    Write-Host ""
    Write-Host "INSTALL READINESS" -ForegroundColor Green
    Write-Host "Ready now: $($ready.Count)/$($ready.Count + $attention.Count)"
    if ($ready.Count -gt 0) {
        Write-Host "Ready:"
        foreach ($line in $ready) { Write-Host "  [OK] $line" -ForegroundColor Green }
    }
    if ($attention.Count -gt 0) {
        Write-Host "Needs attention:"
        foreach ($line in $attention) { Write-Host "  [!!] $line" -ForegroundColor Yellow }
    }
    Write-Host "Next:"
    Write-Host "  - Open dashboard: $DashboardUrl"
    Write-Host "  - Check status: $StatusCommand"
    if ($LogPath) { Write-Host "  - Logs: $LogPath" }
    Write-Host ""
}
