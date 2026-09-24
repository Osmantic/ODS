$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/portal-model-store.ps1')
$fixture=[IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('ods-model-store-'+[guid]::NewGuid().ToString('N'))))
function Check($value,$label) { if (-not $value) { throw $label }; Write-Host "PASS $label" }
function Invoke-DownloadWithRetry { param($Url,$Destination,$Label,$MaxRetries); throw 'Unexpected download: existing models must be reused' }
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
} finally {
    $tempRoot=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $fixture.StartsWith($tempRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture escaped temporary directory' }
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
exit 0
