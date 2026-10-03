# This body is also the Windows qualification preview. It requires GitHub CLI,
# but no Python, WSL or Docker to authenticate the source before setup begins.
& {
    $ErrorActionPreference = 'Stop'
    if (-not (Get-Command gh -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'Install GitHub CLI from https://cli.github.com first, then open a new normal PowerShell window.'
    }
    $odsRelease = Invoke-RestMethod -Uri 'https://api.github.com/repos/Osmantic/ODS/releases/latest' -TimeoutSec 60
    $odsTag = [string]$odsRelease.tag_name
    if ($odsRelease.immutable -ne $true -or $odsRelease.draft -ne $false -or $odsRelease.prerelease -ne $false -or $odsTag -cnotmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') {
        throw 'No immutable signed stable release is available. No installation was changed; main is a separate development channel.'
    }
    $odsRef = Invoke-RestMethod -Uri "https://api.github.com/repos/Osmantic/ODS/git/ref/tags/$odsTag" -TimeoutSec 60
    $odsTagObject = [string]$odsRef.object.sha
    if ($odsRef.object.type -ne 'tag' -or $odsTagObject -cnotmatch '^[0-9a-f]{40}$') { throw 'Stable tag must be annotated and signed.' }
    $odsAnnotation = Invoke-RestMethod -Uri "https://api.github.com/repos/Osmantic/ODS/git/tags/$odsTagObject" -TimeoutSec 60
    $odsCommit = [string]$odsAnnotation.object.sha
    if ($odsAnnotation.verification.verified -ne $true -or $odsAnnotation.verification.reason -ne 'valid' -or $odsAnnotation.object.type -ne 'commit' -or $odsCommit -cnotmatch '^[0-9a-f]{40}$') {
        throw 'Release tag signature was not verified.'
    }
    $odsStage = Join-Path $env:TEMP ('ods-release-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $odsStage | Out-Null
    $odsArchive = "ODS-$odsTag-source.zip"
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/Osmantic/ODS/releases/download/$odsTag/$odsArchive" -OutFile (Join-Path $odsStage $odsArchive) -TimeoutSec 600
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/Osmantic/ODS/releases/download/$odsTag/provenance.sigstore.jsonl" -OutFile (Join-Path $odsStage 'provenance.sigstore.jsonl') -TimeoutSec 600
    & gh attestation verify (Join-Path $odsStage $odsArchive) --bundle (Join-Path $odsStage 'provenance.sigstore.jsonl') --repo Osmantic/ODS --hostname github.com --signer-workflow Osmantic/ODS/.github/workflows/release-provenance.yml --source-ref "refs/tags/$odsTag" --source-digest $odsCommit --deny-self-hosted-runners
    if ($LASTEXITCODE -ne 0) { throw 'GitHub release verification failed. No installer was executed.' }
    $odsSource = Join-Path $odsStage 'source'
    Expand-Archive -LiteralPath (Join-Path $odsStage $odsArchive) -DestinationPath $odsSource
    Write-Host "Verified $odsTag ($odsCommit). Source retained at $odsSource"
    $odsShell = if ($PSVersionTable.PSEdition -eq 'Desktop') { 'powershell.exe' } else { 'pwsh.exe' }
    & (Join-Path $PSHOME $odsShell) -NoProfile -ExecutionPolicy Bypass -File (Join-Path $odsSource 'install.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'The verified installer reported a failure; inspect its output before retrying.' }
}
