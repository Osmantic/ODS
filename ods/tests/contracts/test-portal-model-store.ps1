$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/portal-model-store.ps1')
$fixture=[IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('ods-model-store-'+[guid]::NewGuid().ToString('N'))))
function Check($value,$label) { if (-not $value) { throw $label }; Write-Host "PASS $label" }
$script:downloadMode='forbid'
function Invoke-DownloadWithRetry {
    param($Url,$Destination,$Label,$MaxRetries)
    if ($script:downloadMode -eq 'forbid') { throw 'Unexpected download: existing models must be reused' }
    [IO.File]::WriteAllBytes($Destination,([Text.Encoding]::ASCII.GetBytes('GGUF' + ('x'*32))))
    return $script:downloadMode -ne 'fail'
}
try {
    $model=@{GgufFile='test.gguf'}
    $preview=Initialize-ODSPortalModelStore -WindowsPath $fixture -Model $model -DryRun
    Check (-not (Test-Path -LiteralPath $fixture)) 'dry run creates no directory or model'
    Check (-not $preview.Prepared) 'dry run never claims a model is prepared'
    New-Item -ItemType Directory -Path $fixture | Out-Null
    $file=Join-Path $fixture 'test.gguf'
    [IO.File]::WriteAllBytes($file,([Text.Encoding]::ASCII.GetBytes('GGUF' + ('x'*32))))
    $model.GgufSha256=(Get-FileHash -LiteralPath $file).Hash
    $prepared=Initialize-ODSPortalModelStore -WindowsPath $fixture -Model $model
    Check $prepared.Prepared 'existing model checksum and header verified without download'
    $model.GgufSha256='0'*64
    $rejected=$false
    try { Initialize-ODSPortalModelStore -WindowsPath $fixture -Model $model } catch { $rejected=$true }
    Check $rejected 'checksum mismatch fails'
    Check (Test-Path -LiteralPath $file) 'checksum failure preserves the existing artifact'
    $model=@{GgufFile='../../escape.gguf'}
    $rejected=$false
    try { Initialize-ODSPortalModelStore -WindowsPath $fixture -Model $model } catch { $rejected=$true }
    Check $rejected 'catalog traversal rejected'
    $legacy=Join-Path $fixture 'download.gguf.ods-download'
    [IO.File]::WriteAllText($legacy,'earlier interrupted attempt')
    $model=@{GgufFile='download.gguf';GgufUrl='https://example.test/download.gguf';GgufSha256=(Get-FileHash -LiteralPath $file).Hash}
    $script:downloadMode='fail'
    $rejected=$false
    try { $null=Initialize-ODSPortalModelStore -WindowsPath $fixture -Model $model } catch { $rejected=$true }
    Check ($rejected -and -not (Test-Path (Join-Path $fixture 'download.gguf'))) 'failed download is never published'
    Check (@(Get-ChildItem -LiteralPath $fixture -Filter '*.ods-download').Count -eq 1 -and (Test-Path $legacy)) 'failed attempt cleans only its own partial and preserves earlier artifacts'
    $script:downloadMode='success'
    $prepared=Initialize-ODSPortalModelStore -WindowsPath $fixture -Model $model
    Check ($prepared.Prepared -and (Test-Path (Join-Path $fixture 'download.gguf'))) 'retry succeeds despite an earlier interrupted download'
    $model.GgufFile='invalid.gguf'
    $model.GgufSha256='0'*64
    $rejected=$false
    try { $null=Initialize-ODSPortalModelStore -WindowsPath $fixture -Model $model } catch { $rejected=$true }
    Check ($rejected -and -not (Test-Path (Join-Path $fixture 'invalid.gguf'))) 'download checksum mismatch is never published'
    Check (@(Get-ChildItem -LiteralPath $fixture -Filter '*.ods-download').Count -eq 1) 'invalid download leaves no retry-blocking partial from this attempt'
} finally {
    $tempRoot=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $fixture.StartsWith($tempRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture escaped temporary directory' }
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
exit 0
