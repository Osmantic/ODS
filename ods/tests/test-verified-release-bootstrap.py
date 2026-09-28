"""Run release bootstraps with controlled HTTP/gh transports and real extraction.

The fake verifier tests sequencing and constraints, not Sigstore cryptography.
Actual bundle validation is a release-candidate gate.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

ODS = Path(__file__).resolve().parents[1]
TAG_OBJECT = 'a' * 40
COMMIT = 'b' * 40

GH_STUB = r'''
import json, os, pathlib, shutil, sys
root = pathlib.Path(os.environ['ODS_RELEASE_TEST'])
args = sys.argv[1:]
if args[0] == 'curl':
    uri = next(arg for arg in args if arg.startswith('https://'))
    args = ['http', uri] + (['-o', args[args.index('-o')+1]] if '-o' in args else [])
with (root/'calls.jsonl').open('a', encoding='utf-8') as log:
    log.write(json.dumps(args)+'\n')
scenario = os.environ.get('ODS_RELEASE_SCENARIO', 'valid')
def option(name): return args[args.index(name)+1]
if args[0] == 'http' and args[1].startswith('https://api.github.com/repos/Osmantic/ODS/'):
    if scenario == 'api-failed': sys.exit(22)
    if args[1].endswith('/releases/latest'):
        data = {'immutable': scenario != 'mutable', 'draft': scenario == 'draft', 'prerelease': scenario == 'prerelease', 'tag_name': '../untrusted' if scenario == 'invalid-tag' else 'v9.0.0'}
        selected = data['tag_name'] if data['immutable'] else ''
    elif '/git/ref/tags/' in args[1]:
        data = {'object': {'type': 'commit' if scenario == 'lightweight' else 'tag', 'sha': '../bad' if scenario == 'invalid-object' else 'a'*40}}
        selected = data['object']['sha'] if data['object']['type'] == 'tag' else ''
    else:
        data = {'object': {'type': 'commit', 'sha': 'b'*40}, 'verification': {'verified': scenario != 'unsigned', 'reason': 'valid'}}
        selected = data['object']['sha'] if data['verification']['verified'] else ''
    print(json.dumps(data))
elif args[0] == 'http' and args[1].startswith('https://github.com/Osmantic/ODS/releases/download/v9.0.0/'):
    if scenario == 'download-failed': sys.exit(2)
    target = pathlib.Path(option('-o'))
    if args[1].endswith('.zip'): shutil.copyfile(root/'fixture.zip', target)
    else: target.write_text('fixture bundle')
elif args[:2] == ['attestation', 'verify']:
    assert option('--repo') == 'Osmantic/ODS'
    assert option('--hostname') == 'github.com'
    assert option('--signer-workflow') == 'Osmantic/ODS/.github/workflows/release-provenance.yml'
    assert option('--source-ref') == 'refs/tags/v9.0.0'
    assert option('--source-digest') == 'b'*40
    assert '--deny-self-hosted-runners' in args
    assert pathlib.Path(option('--bundle')).is_file()
    # Verification must occur before extraction, even for a successful run.
    assert not (pathlib.Path(args[2]).parent/'source').exists()
    if scenario == 'bad-attestation': sys.exit(1)
    print('fixture verification accepted')
else:
    raise AssertionError(args)
'''


@pytest.fixture
def harness(tmp_path):
    root = tmp_path / 'Release Test \u00e9\u6a21'
    root.mkdir()
    binaries = root / 'bin'
    binaries.mkdir()
    stub = binaries / 'gh-stub.py'
    stub.write_text(GH_STUB, encoding='utf-8')
    if sys.platform == 'win32':
        (binaries / 'gh.cmd').write_text('@"%ODS_TEST_PYTHON%" "%ODS_TEST_HTTP_STUB%" %*\n', encoding='utf-8')
    else:
        shim = binaries / 'gh'
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n')
        shim.chmod(0o700)
        curl = binaries / 'curl'
        curl.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" curl "$@"\n')
        curl.chmod(0o700)
    with zipfile.ZipFile(root / 'fixture.zip', 'w') as archive:
        archive.writestr('install.sh', '#!/bin/bash\nprintf executed > "$ODS_RELEASE_TEST/receipt"\n')
        archive.writestr('install.ps1', "[IO.File]::WriteAllText((Join-Path $env:ODS_RELEASE_TEST 'receipt'), 'executed')\n")
    env = dict(os.environ, ODS_RELEASE_TEST=str(root), TEMP=str(root), TMPDIR=str(root),
               ODS_TEST_PYTHON=sys.executable, ODS_TEST_HTTP_STUB=str(stub),
               ODS_TEST_BOOTSTRAP=str(ODS / 'installers/verified-release.ps1'),
               PATH=str(binaries) + os.pathsep + os.environ['PATH'])
    if sys.platform == 'win32':
        shell = shutil.which('powershell.exe')
        if not shell:
            pytest.skip('Windows PowerShell 5.1 is required')
        wrapper = root / 'run-test.ps1'
        wrapper.write_text('''
$ErrorActionPreference = 'Stop'
function Invoke-RestMethod {
    param($Uri, $TimeoutSec)
    $value = & $env:ODS_TEST_PYTHON $env:ODS_TEST_HTTP_STUB http $Uri
    if ($LASTEXITCODE -ne 0) { throw 'Fixture HTTP failed' }
    return (($value | Out-String) | ConvertFrom-Json)
}
function Invoke-WebRequest {
    param([switch]$UseBasicParsing, $Uri, $OutFile, $TimeoutSec)
    & $env:ODS_TEST_PYTHON $env:ODS_TEST_HTTP_STUB http $Uri -o $OutFile
    if ($LASTEXITCODE -ne 0) { throw 'Fixture download failed' }
}
. $env:ODS_TEST_BOOTSTRAP
''', encoding='utf-8')
        command = [shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(wrapper)]
    else:
        command = ['bash', str(ODS / 'installers/verified-release.sh')]
    return root, env, command


def test_verified_release_runs_only_after_identity_and_artifact_checks(harness):
    root, env, command = harness
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / 'receipt').read_text() == 'executed'
    calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
    assert [call[0] for call in calls] == ['http'] * 5 + ['attestation']
    assert list(root.glob('ods-release*/source/install.*')), 'Verified source must survive installer restart/resume'


@pytest.mark.parametrize('scenario', ['mutable', 'draft', 'prerelease', 'invalid-tag', 'invalid-object',
                                      'lightweight', 'unsigned', 'api-failed', 'download-failed', 'bad-attestation'])
def test_release_rejection_never_extracts_or_invokes_installation(harness, scenario):
    root, env, command = harness
    env['ODS_RELEASE_SCENARIO'] = scenario
    old_install = root / 'existing-ods'
    old_install.mkdir()
    (old_install / '.env').write_text('owner fixture')
    env['ODS_INSTALL_DIR'] = str(old_install)
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert not (root / 'receipt').exists()
    assert not list(root.glob('ods-release*/source'))
    assert (old_install / '.env').read_text() == 'owner fixture'


def test_readme_executes_reviewed_bootstrap_bodies():
    readme = (ODS.parent / 'README.md').read_text(encoding='utf-8')
    for suffix, fence in [('sh', 'bash'), ('ps1', 'powershell')]:
        body = (ODS / f'installers/verified-release.{suffix}').read_text(encoding='utf-8')
        body = '\n'.join(line for line in body.splitlines() if not line.startswith('#')).strip()
        assert f'```{fence}\n{body}\n```' in readme
