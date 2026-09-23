#!/usr/bin/env python3
"""Build a checksum-bound beta source archive and platform launchers from one commit.

This prepares local release artifacts; it never creates a tag, publishes a release,
or runs an installer. Publish the whole kit only after validating its exact commit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse
import zipfile


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf',
                                    '-C', str(root), *args])


def build(root, commit, release_url, output):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Use a full lowercase 40-character commit SHA, not a branch or tag.')
    parsed = urlparse(release_url)
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com'
            or not re.fullmatch(r'/Osmantic/ODS/releases/download/[A-Za-z0-9._-]+', parsed.path)
            or parsed.query or parsed.fragment):
        raise ValueError('Use the official ODS HTTPS release download URL with a fixed tag.')
    resolved = git(root, 'rev-parse', '--verify', commit + '^{commit}').decode().strip()
    if resolved != commit:
        raise ValueError('Commit identity mismatch.')
    tree = git(root, 'rev-parse', commit + '^{tree}').decode().strip()
    output.mkdir(parents=True, exist_ok=True)
    archive_name = 'ods-public-beta-' + commit + '.zip'
    targets = [output / name for name in (archive_name, 'install-beta.sh', 'install-beta.ps1',
                                          'release.json', 'SHA256SUMS')]
    if any(path.exists() for path in targets):
        raise ValueError('Output artifacts already exist; choose a fresh output directory.')
    archive = output / archive_name
    git(root, 'archive', '--format=zip', '--prefix=ods-beta/', '--output=' + str(archive.resolve()), commit)
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    # git archive honors committed eol/export attributes. Hash the bytes users
    # extract, which can differ from git show (notably *.ps1 eol=crlf).
    with zipfile.ZipFile(archive) as archived_source:
        installer_hashes = {
            name: hashlib.sha256(archived_source.read('ods-beta/' + name)).hexdigest()
            for name in ('install.sh', 'install.ps1')
        }
    source_url = release_url + '/' + archive_name
    receipt = {
        'schema': 1, 'channel': 'public-beta', 'version': 'public-beta+' + commit[:12],
        'sourceUrl': source_url, 'requestedRef': commit, 'resolvedCommit': commit,
        'tree': tree, 'archiveSha256': archive_hash, 'installerSha256': installer_hashes,
    }
    receipt_json = json.dumps(receipt, indent=2)
    unix = '''#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" != "" && "${1:-}" != --verify-only ]]; then
  echo 'Usage: bash install-beta.sh [--verify-only]' >&2; exit 2
fi
for program in curl; do
  command -v "$program" >/dev/null || { echo "Required command missing: $program" >&2; exit 1; }
done
if ! command -v unzip >/dev/null && ! command -v python3 >/dev/null; then
  echo 'Either unzip or Python 3 is required to extract the verified archive.' >&2; exit 1
fi
sha256() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null; then shasum -a 256 "$1" | awk '{print $1}'
  else echo 'A SHA-256 verifier is required.' >&2; return 1; fi
}
# Keep the verified source and receipt for diagnosis; never reuse an existing path.
stage=$(umask 077; mktemp -d "${TMPDIR:-/tmp}/ods-beta.XXXXXXXX")
echo "Preparing public-beta @COMMIT@ in $stage"
(
  umask 077
  curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --max-time 600 --output "$stage/source.zip" '@SOURCE_URL@'
)
[[ "$(sha256 "$stage/source.zip")" == '@ARCHIVE_HASH@' ]] || {
  echo 'Source archive checksum mismatch; installer was not executed.' >&2; exit 1;
}
if command -v unzip >/dev/null; then unzip -q "$stage/source.zip" -d "$stage"
else
  # zipfile extraction omits Unix modes. Restore archived rwx permissions on
  # regular files/directories, subject to the caller's umask, after verification.
  # The private stage directory protects the source while keeping its modes
  # suitable for the installer's later source copies to service directories.
  python3 - "$stage/source.zip" "$stage" <<'ODS_BETA_EXTRACT'
from pathlib import Path
import os
import stat
import sys
import zipfile

caller_umask = os.umask(0)
os.umask(caller_umask)
with zipfile.ZipFile(sys.argv[1]) as archive:
    for entry in archive.infolist():
        extracted = Path(archive.extract(entry, sys.argv[2]))
        mode = entry.external_attr >> 16
        if stat.S_ISREG(mode) or stat.S_ISDIR(mode):
            extracted.chmod((mode & 0o777) & ~caller_umask)
ODS_BETA_EXTRACT
fi
source_dir="$stage/ods-beta"
[[ ! -L "$source_dir/install.sh" && -f "$source_dir/install.sh" ]] || exit 1
[[ "$(sha256 "$source_dir/install.sh")" == '@INSTALLER_HASH@' ]] || {
  echo 'Installer checksum mismatch; installer was not executed.' >&2; exit 1;
}
(
  umask 077
  cat > "$source_dir/ods/beta-install-receipt.json" <<'ODS_BETA_RECEIPT'
@RECEIPT@
ODS_BETA_RECEIPT
)
printf 'Verified public-beta commit: %s\nReceipt: %s\n' '@COMMIT@' "$source_dir/ods/beta-install-receipt.json"
[[ "${1:-}" == --verify-only ]] && exit 0
cd "$source_dir"
exec bash ./install.sh
'''
    for key, value in {
        '@COMMIT@': commit, '@SOURCE_URL@': source_url, '@ARCHIVE_HASH@': archive_hash,
        '@INSTALLER_HASH@': installer_hashes['install.sh'], '@RECEIPT@': receipt_json,
    }.items():
        unix = unix.replace(key, value)
    windows = '''param([switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
$stage = Join-Path $env:TEMP ('ods-beta-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage -ErrorAction Stop | Out-Null
$archive = Join-Path $stage 'source.zip'
Write-Host 'Preparing public-beta @COMMIT@'
Invoke-WebRequest -UseBasicParsing -Uri '@SOURCE_URL@' -OutFile $archive
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne '@ARCHIVE_HASH@') {
    throw 'Source archive checksum mismatch; installer was not executed.'
}
Expand-Archive -LiteralPath $archive -DestinationPath $stage
$sourceDir = Join-Path $stage 'ods-beta'
$installer = Join-Path $sourceDir 'install.ps1'
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne '@INSTALLER_HASH@') {
    throw 'Installer checksum mismatch; installer was not executed.'
}
$receipt = Join-Path $sourceDir 'ods/beta-install-receipt.json'
$receiptJson = @'
@RECEIPT@
'@
[IO.File]::WriteAllText($receipt, $receiptJson, [Text.UTF8Encoding]::new($false))
Write-Host "Verified public-beta commit: @COMMIT@`nReceipt: $receipt"
if ($VerifyOnly) { return }
Push-Location -LiteralPath $sourceDir
try { & $installer } finally { Pop-Location }
'''
    for key, value in {
        '@COMMIT@': commit, '@SOURCE_URL@': source_url, '@ARCHIVE_HASH@': archive_hash,
        '@INSTALLER_HASH@': installer_hashes['install.ps1'], '@RECEIPT@': receipt_json,
    }.items():
        windows = windows.replace(key, value)
    (output / 'install-beta.sh').write_text(unix, encoding='utf-8', newline='\n')
    (output / 'install-beta.ps1').write_text(windows, encoding='utf-8', newline='\n')
    receipt['launcherSha256'] = {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest()
        for name in ('install-beta.sh', 'install-beta.ps1')
    }
    (output / 'release.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    checksums = ''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
                        for path in targets if path.name != 'SHA256SUMS')
    (output / 'SHA256SUMS').write_text(checksums, encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--release-url', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    receipt = build(root, args.commit, args.release_url, args.output)
    print('Prepared public-beta kit for ' + receipt['resolvedCommit'] + '. Nothing published or installed.')


if __name__ == '__main__':
    main()
