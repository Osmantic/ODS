# ============================================================================
# ODS Windows -- transactional model activation helpers
# ============================================================================

function Resolve-WindowsODSAgentBaseUri {
    param([hashtable]$EnvMap)

    $port = 7710
    $rawPort = [string]$EnvMap["ODS_AGENT_PORT"]
    $parsedPort = 0
    if (-not [string]::IsNullOrWhiteSpace($rawPort)) {
        if (-not [int]::TryParse($rawPort.Trim(), [ref]$parsedPort) -or
            $parsedPort -lt 1 -or $parsedPort -gt 65535) {
            throw "ODS_AGENT_PORT must be an integer between 1 and 65535"
        }
        $port = $parsedPort
    }

    $hostName = [string]$EnvMap["ODS_AGENT_BIND"]
    if ([string]::IsNullOrWhiteSpace($hostName) -or
        $hostName.Trim() -in @("0.0.0.0", "::", "*", "+")) {
        $hostName = "127.0.0.1"
    } else {
        $hostName = $hostName.Trim()
    }

    if ([Uri]::CheckHostName($hostName) -eq [UriHostNameType]::Unknown) {
        throw "ODS_AGENT_BIND is not a valid host name or IP address"
    }
    if ($hostName.Contains(":")) { $hostName = "[$hostName]" }
    return "http://${hostName}:$port"
}

function Resolve-WindowsODSModelCatalogId {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [Parameter(Mandatory = $true)][string]$GgufFile
    )

    if ([IO.Path]::GetFileName($GgufFile) -ne $GgufFile -or
        $GgufFile.Contains("/") -or $GgufFile.Contains("\")) {
        throw "Tier model contains an invalid GGUF filename"
    }

    $modelPath = Join-Path (Join-Path $InstallDir "data\models") $GgufFile
    if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf) -or
        (Get-Item -LiteralPath $modelPath).Length -le 0) {
        throw "Model file is not downloaded: $modelPath"
    }

    $catalogPath = Join-Path (Join-Path $InstallDir "config") "model-library.json"
    if (-not (Test-Path -LiteralPath $catalogPath -PathType Leaf)) {
        throw "Model catalog is missing: $catalogPath"
    }
    try {
        $catalog = Get-Content -LiteralPath $catalogPath -Raw -Encoding UTF8 |
            ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Model catalog is not valid JSON: $($_.Exception.Message)"
    }

    $catalogMatches = @($catalog.models | Where-Object {
        [string]$_.gguf_file -eq $GgufFile -and
        -not [string]::IsNullOrWhiteSpace([string]$_.id)
    })
    if ($catalogMatches.Count -ne 1) {
        throw "Tier model must match exactly one model catalog entry: $GgufFile"
    }
    $modelId = ([string]$catalogMatches[0].id).Trim()
    if ($modelId.Contains("`r") -or $modelId.Contains("`n")) {
        throw "Model catalog entry contains an invalid model ID"
    }
    return $modelId
}

function Get-WindowsODSHttpErrorDetail {
    param([System.Management.Automation.ErrorRecord]$ErrorRecord)

    $fallback = $ErrorRecord.Exception.Message
    try {
        $response = $ErrorRecord.Exception.Response
        if ($null -eq $response) { return $fallback }
        $stream = $response.GetResponseStream()
        if ($null -eq $stream) { return $fallback }
        $reader = New-Object IO.StreamReader($stream)
        try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
        if ([string]::IsNullOrWhiteSpace($body)) { return $fallback }
        try {
            $parsed = $body | ConvertFrom-Json -ErrorAction Stop
            if (-not [string]::IsNullOrWhiteSpace([string]$parsed.error)) {
                return [string]$parsed.error
            }
        } catch { }
        return $body.Substring(0, [Math]::Min(500, $body.Length))
    } catch {
        return $fallback
    }
}

function Invoke-WindowsODSModelActivationTransaction {
    param(
        [Parameter(Mandatory = $true)][hashtable]$EnvMap,
        [Parameter(Mandatory = $true)][string]$ModelId,
        [Parameter(Mandatory = $true)][string]$Tier,
        [Parameter(Mandatory = $true)][int]$ContextLength,
        [int]$HealthTimeoutSeconds = 3,
        [int]$ActivationTimeoutSeconds = 2700
    )

    if ($ContextLength -le 0) { throw "Context length must be positive" }
    if ([string]::IsNullOrWhiteSpace($ModelId) -or
        $ModelId.Contains("`r") -or $ModelId.Contains("`n")) {
        throw "Model ID is invalid"
    }
    if ([string]::IsNullOrWhiteSpace($Tier) -or
        $Tier.Contains("`r") -or $Tier.Contains("`n")) {
        throw "Model tier is invalid"
    }
    if ($HealthTimeoutSeconds -le 0 -or $ActivationTimeoutSeconds -le 0) {
        throw "Model activation timeouts must be positive"
    }
    $agentKey = [string]$EnvMap["ODS_AGENT_KEY"]
    if ([string]::IsNullOrWhiteSpace($agentKey)) {
        $agentKey = [string]$EnvMap["DASHBOARD_API_KEY"]
    }
    if ([string]::IsNullOrWhiteSpace($agentKey)) {
        throw "ODS host-agent API key is missing from .env"
    }
    if ($agentKey.Contains("`r") -or $agentKey.Contains("`n")) {
        throw "ODS host-agent API key contains invalid newline characters"
    }

    $baseUri = Resolve-WindowsODSAgentBaseUri -EnvMap $EnvMap
    try {
        $health = Invoke-WebRequest -Method Get -Uri "$baseUri/health" `
            -UseBasicParsing -TimeoutSec $HealthTimeoutSeconds -ErrorAction Stop
        if ([int]$health.StatusCode -lt 200 -or [int]$health.StatusCode -ge 300) {
            throw "health endpoint returned HTTP $($health.StatusCode)"
        }
    } catch {
        throw "ODS host agent is not reachable. Run '.\ods.ps1 agent restart' and retry. $($_.Exception.Message)"
    }

    $payload = [ordered]@{
        model_id      = $ModelId
        tier          = $Tier
        context_length = $ContextLength
    } | ConvertTo-Json -Compress
    $headers = @{ Authorization = "Bearer $agentKey" }
    try {
        $receipt = Invoke-RestMethod -Method Post -Uri "$baseUri/v1/model/activate" `
            -Headers $headers -ContentType "application/json" -Body $payload `
            -UseBasicParsing -TimeoutSec $ActivationTimeoutSeconds -ErrorAction Stop
    } catch {
        $detail = Get-WindowsODSHttpErrorDetail -ErrorRecord $_
        throw "Model activation failed: $detail"
    }

    $receiptContext = 0
    $validReceiptContext = [int]::TryParse(
        [string]$receipt.context_length,
        [ref]$receiptContext
    ) -and $receiptContext -gt 0
    if ([string]$receipt.status -ne "activated" -or
        [string]$receipt.model_id -ne $ModelId -or
        [string]$receipt.tier -ne $Tier -or
        -not $validReceiptContext) {
        throw "Host agent returned an invalid model activation receipt"
    }
    return $receipt
}
