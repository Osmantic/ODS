# JSON-only, finite controller. No daemon and no implicit runtime adoption.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# WSL interop may preserve a PS7 parent's module path; normalize only this PS5 child.
if ($PSVersionTable.PSEdition -eq 'Desktop') { $env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules') }
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'lib/portal-model-control.ps1')
try {
    $inputJson = [Console]::In.ReadToEnd()
    if ($inputJson.Length -gt 65536) { Throw-ODSPortalControlError 'invalid_request' 'The JSON request is too large.' }
    $request = $inputJson | ConvertFrom-Json -ErrorAction Stop
    $result = Invoke-ODSPortalModelControl $request
    [Console]::Out.WriteLine(($result | ConvertTo-Json -Depth 8 -Compress))
    exit 0
} catch {
    $code = if ($_.Exception.Data['code']) { [string]$_.Exception.Data['code'] } else { 'control_failed' }
    $result = [ordered]@{ ok = $false; code = $code; error = $_.Exception.Message }
    if ($_.Exception.Data['newPlanDigest']) { $result.newPlanDigest = [string]$_.Exception.Data['newPlanDigest'] }
    [Console]::Out.WriteLine(($result | ConvertTo-Json -Depth 4 -Compress))
    exit 1
}
