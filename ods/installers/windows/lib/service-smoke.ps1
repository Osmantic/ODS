function Test-ODSWindowsComposeServiceRecords {
    param(
        [Parameter(Mandatory = $true)][string[]]$EnabledServices,
        [Parameter(Mandatory = $true)][object[]]$ServiceRecords
    )
    $byService = @{}
    foreach ($record in $ServiceRecords) {
        $name = [string]$record.Service
        if (-not [string]::IsNullOrWhiteSpace($name)) { $byService[$name] = $record }
    }
    $failures = New-Object System.Collections.Generic.List[string]
    foreach ($service in $EnabledServices) {
        if (-not $byService.ContainsKey($service)) {
            [void]$failures.Add("$service (missing)")
            continue
        }
        $record = $byService[$service]
        $state = ([string]$record.State).ToLowerInvariant()
        $health = ([string]$record.Health).ToLowerInvariant()
        if ($state -notin @("running", "up")) {
            [void]$failures.Add("$service (state=$state)")
        } elseif ($health -and $health -notin @("healthy", "running", "none", "n/a")) {
            [void]$failures.Add("$service (health=$health)")
        }
    }
    [pscustomobject]@{ Passed = ($failures.Count -eq 0); Failures = @($failures) }
}

function Get-ODSWindowsComposeServiceSmokeRecords {
    param(
        [Parameter(Mandatory = $true)][string[]]$DockerClientArgs,
        [Parameter(Mandatory = $true)][string[]]$ComposeFlags
    )
    $services = @(& docker @DockerClientArgs compose @ComposeFlags config --services 2>$null |
        ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    if ($LASTEXITCODE -ne 0) { throw "docker compose config --services failed" }
    $records = @(& docker @DockerClientArgs compose @ComposeFlags ps --format json 2>$null)
    if ($LASTEXITCODE -ne 0) { throw "docker compose ps --format json failed" }
    $parsed = @()
    foreach ($line in $records) {
        if ([string]::IsNullOrWhiteSpace([string]$line)) { continue }
        try { $parsed += ($line | ConvertFrom-Json) } catch { }
    }
    return @{ Services = $services; Records = $parsed }
}
