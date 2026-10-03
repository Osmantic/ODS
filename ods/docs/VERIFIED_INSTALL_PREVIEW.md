# Verified installer preview — qualification only

This is a proposed installation channel, **not the current public quickstart**.
The commands require a signed, immutable stable release with source archives
and a provenance bundle. The current public `v3.0.0` has no such assets and is
correctly refused. Do not advertise these commands as usable until the release
qualification below passes. Existing installation instructions remain in the
[main README](../../README.md).

Tests and qualification are the implementation team's responsibility. The
maintainer reviews and merges the PRs; they are not being asked to reproduce
the development test suite. Actual GitHub signing/attestation verification needs
eligible published artifacts and cannot be proved by local mocked responses.

## Qualification before activation

1. Merge the reviewed producer and security implementation with their CI green.
2. Generate a new candidate through the signed-source workflow, following
   [Signed Source Releases](SIGNED_SOURCE_RELEASES.md). Publishing or creating
   tags still requires explicit release authorization; merging does not publish.
3. The implementation team verifies the real tag, archives, bundle and installer
   path on isolated test installations. Record the release tag, commit, artifact
   digests and results; do not use a fixture success as production evidence.
4. Only then promote the commands below to the public README in a separate
   reviewed change. No failed verification may fall back to unsigned source.

This staging allows the runtime security fixes to merge without replacing a
working installation command with one that cannot yet obtain eligible source.
The default-channel portion of audit finding SEC-005 remains open until step 4.

## Candidate commands

Install the official [GitHub CLI](https://cli.github.com/) first. Downloads are
public; bundle verification does not require a GitHub login. POSIX also needs
Python 3, curl and unzip. The verified source is retained for installer resume.

### Linux or macOS

```bash
(
set -euo pipefail
command -v gh >/dev/null || { echo 'Install GitHub CLI from https://cli.github.com first.' >&2; exit 1; }
command -v unzip >/dev/null || { echo 'Install unzip first.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Install Python 3 first (JSON metadata parsing).' >&2; exit 1; }
command -v curl >/dev/null || { echo 'Install curl first.' >&2; exit 1; }
ods_get() { curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location --connect-timeout 15 --max-time 600 "$@"; }
ods_tag="$(ods_get https://api.github.com/repos/Osmantic/ODS/releases/latest | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tag_name", "") if d.get("immutable") is True and d.get("draft") is False and d.get("prerelease") is False else "")')"
[[ "$ods_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
    echo 'No immutable signed stable release is available. No installation was changed; main is a separate development channel.' >&2
    exit 1
}
ods_tag_object="$(ods_get "https://api.github.com/repos/Osmantic/ODS/git/ref/tags/$ods_tag" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("object", {}); print(d.get("sha", "") if d.get("type") == "tag" else "")')"
[[ "$ods_tag_object" =~ ^[0-9a-f]{40}$ ]] || { echo 'Stable tag must be annotated and signed.' >&2; exit 1; }
ods_commit="$(ods_get "https://api.github.com/repos/Osmantic/ODS/git/tags/$ods_tag_object" | python3 -c 'import json,sys; d=json.load(sys.stdin); v=d.get("verification", {}); o=d.get("object", {}); print(o.get("sha", "") if v.get("verified") is True and v.get("reason") == "valid" and o.get("type") == "commit" else "")')"
[[ "$ods_commit" =~ ^[0-9a-f]{40}$ ]] || { echo 'Release tag signature was not verified.' >&2; exit 1; }
ods_stage="$(mktemp -d "${TMPDIR:-/tmp}/ods-release.XXXXXXXX")"
ods_archive="ODS-$ods_tag-source.zip"
ods_get "https://github.com/Osmantic/ODS/releases/download/$ods_tag/$ods_archive" -o "$ods_stage/$ods_archive"
ods_get "https://github.com/Osmantic/ODS/releases/download/$ods_tag/provenance.sigstore.jsonl" -o "$ods_stage/provenance.sigstore.jsonl"
gh attestation verify "$ods_stage/$ods_archive" \
    --bundle "$ods_stage/provenance.sigstore.jsonl" --repo Osmantic/ODS --hostname github.com \
    --signer-workflow Osmantic/ODS/.github/workflows/release-provenance.yml \
    --source-ref "refs/tags/$ods_tag" --source-digest "$ods_commit" --deny-self-hosted-runners
unzip -q "$ods_stage/$ods_archive" -d "$ods_stage/source"
echo "Verified $ods_tag ($ods_commit). Source retained at $ods_stage/source"
bash "$ods_stage/source/install.sh" "$@"
)
```

### Windows PowerShell

```powershell
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
```

