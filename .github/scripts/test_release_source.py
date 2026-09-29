"""Release archive and trust-boundary tests; no release is created or published."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('release_source', Path(__file__).with_name('release_source.py'))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    release.git(root, 'init', '-b', 'main')
    release.git(root, 'config', 'user.name', 'Release test')
    release.git(root, 'config', 'user.email', 'release-test@example.invalid')
    release.git(root, 'config', 'commit.gpgsign', 'false')
    release.git(root, 'config', 'tag.gpgsign', 'false')
    for name in ['install.sh', 'install.ps1', 'ods/install.sh', 'ods/installers/windows.ps1']:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture, never executed\n')
    release.git(root, 'add', '.')
    release.git(root, 'commit', '-m', 'Source fixture')
    commit = release.git(root, 'rev-parse', 'HEAD').decode().strip()
    release.git(root, 'update-ref', 'refs/remotes/origin/main', commit)
    release.git(root, 'tag', '-a', 'v9.0.0-test.1', '-m', 'Test annotation')
    tag_object = release.git(root, 'rev-parse', 'refs/tags/v9.0.0-test.1').decode().strip()
    identity = {'repository': release.REPOSITORY, 'tag': 'v9.0.0-test.1', 'tagObject': tag_object, 'commit': commit}
    return root, identity


def verified_responses(identity):
    return {
        'git/ref/tags/' + identity['tag']: {'object': {'type': 'tag', 'sha': identity['tagObject']}},
        'git/tags/' + identity['tagObject']: {
            'sha': identity['tagObject'], 'tag': identity['tag'],
            'object': {'type': 'commit', 'sha': identity['commit']},
            'verification': {'verified': True, 'reason': 'valid'},
        },
    }


def test_gate_binds_verified_tag_to_remote_object_and_workflow_commit(repo):
    root, identity = repo
    # GitHub is the signature verifier. This tests handling of its response;
    # it does not pretend the local fixture's unsigned tag has a real signature.
    replies = verified_responses(identity)
    assert release.verified_tag(root, identity['tag'], identity['commit'], replies.__getitem__) == identity


@pytest.mark.parametrize('damage', ['unsigned', 'unknown-key', 'moved-ref', 'other-tag', 'other-commit', 'nested-tag'])
def test_gate_rejects_unverified_or_mismatched_source(repo, damage):
    root, identity = repo
    replies = copy.deepcopy(verified_responses(identity))
    annotation = replies['git/tags/' + identity['tagObject']]
    if damage == 'unsigned':
        annotation['verification']['verified'] = False
    elif damage == 'unknown-key':
        annotation['verification']['reason'] = 'unknown_key'
    elif damage == 'moved-ref':
        replies['git/ref/tags/' + identity['tag']]['object']['sha'] = 'a' * 40
    elif damage == 'other-tag':
        annotation['tag'] = 'v8.0.0'
    elif damage == 'other-commit':
        annotation['object']['sha'] = 'a' * 40
    else:
        annotation['object']['type'] = 'tag'
    with pytest.raises(ValueError):
        release.verified_tag(root, identity['tag'], identity['commit'], replies.__getitem__)


def test_lightweight_tag_fails_before_network(repo):
    root, identity = repo
    release.git(root, 'tag', 'v9.0.1')
    with pytest.raises(ValueError, match='annotated'):
        release.verified_tag(root, 'v9.0.1', identity['commit'], lambda _: pytest.fail('Network called'))


def test_unmerged_source_is_not_releasable(repo):
    root, identity = repo
    release.git(root, 'checkout', '--orphan', 'unrelated')
    release.git(root, 'commit', '-m', 'Unrelated history')
    release.git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    release.git(root, 'checkout', identity['commit'])
    with pytest.raises(subprocess.CalledProcessError):
        release.verified_tag(root, identity['tag'], identity['commit'], verified_responses(identity).__getitem__)


def test_exact_archives_ignore_working_changes_and_bind_inventory(repo, tmp_path):
    root, identity = repo
    (root / 'owner-secret.txt').write_text('untracked fixture')
    (root / 'install.sh').write_text('uncommitted change')
    first, second = tmp_path / 'first', tmp_path / 'second'
    for output in (first, second):
        release.git(root, 'config', 'tar.umask', '0077' if output == second else '0002')
        release.package_source(root, identity, output)
        sbom = {'spdxVersion': 'SPDX-2.3', 'documentNamespace': 'https://example.invalid/test-sbom'}
        (output / 'source.spdx.json').write_text(json.dumps(sbom))
        release.finalize(output)
        manifest = json.loads((output / 'release-manifest.json').read_text())
        assert manifest['commit'] == identity['commit']
        for name, metadata in manifest['files'].items():
            assert metadata == {'sha256': release.digest(output / name), 'size': (output / name).stat().st_size}
        with tarfile.open(next(output.glob('*.tar.gz'))) as archive:
            assert 'owner-secret.txt' not in archive.getnames()
            assert archive.extractfile('install.sh').read() == b'fixture, never executed\n'
            assert {m.mode for m in archive.getmembers() if m.isfile()} <= {0o644, 0o755}
        with zipfile.ZipFile(next(output.glob('*.zip'))) as archive:
            assert 'owner-secret.txt' not in archive.namelist()
            assert archive.read('install.sh') == b'fixture, never executed\n'
    for name in [p.name for p in first.iterdir() if p.is_file()]:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    with pytest.raises(FileExistsError):
        release.package_source(root, identity, first)


def test_archive_refuses_symlink_entries(repo, tmp_path):
    root, identity = repo
    blob = subprocess.check_output(['git', '-C', str(root), 'hash-object', '-w', '--stdin'], input=b'../outside')
    release.git(root, 'update-index', '--add', '--cacheinfo', '120000', blob.decode().strip(), 'escape')
    release.git(root, 'commit', '-m', 'Unsafe fixture')
    identity['commit'] = release.git(root, 'rev-parse', 'HEAD').decode().strip()
    with pytest.raises(ValueError, match='unsupported path or link'):
        release.package_source(root, identity, tmp_path / 'bad-archive')


def test_release_job_is_tag_only_pinned_and_draft_only():
    config = yaml.load((ROOT / '.github/workflows/release-provenance.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(config['on']) == {'push', 'workflow_dispatch'}
    assert config['on']['push'] == {'tags': ['v*']}
    prepare, attest, job = (config['jobs'][key] for key in ('prepare', 'attest', 'draft'))
    assert "github.repository == 'Osmantic/ODS'" in prepare['if']
    assert "startsWith(github.ref, 'refs/tags/v')" in prepare['if']
    assert attest['needs'] == 'prepare' and job['needs'] == 'attest'
    # Actions can read github.token even without an explicit env assignment.
    # The SBOM executes on a different runner with no write or OIDC permission.
    assert prepare['permissions'] == {'contents': 'read'}
    assert attest['permissions'] == {'contents': 'read', 'id-token': 'write', 'attestations': 'write'}
    assert job['permissions'] == {'actions': 'read', 'contents': 'write'}
    assert all('uses' not in step for step in job['steps'])
    assert len(job['steps']) == 1
    assert set(job['steps'][0]['env']) == {'GH_TOKEN', 'SOURCE_ARTIFACT'}
    assert job['steps'][0]['env']['SOURCE_ARTIFACT'] == '${{ needs.attest.outputs.artifact }}'
    verify = next(step for step in prepare['steps'] if 'release_source.py prepare' in step.get('run', ''))
    assert verify['env']['GH_TOKEN'] == '${{ github.token }}'
    assert any(step.get('uses', '').startswith('anchore/sbom-action@') for step in prepare['steps'])
    for current in (prepare, attest, job):
        assert 'GH_TOKEN' not in current.get('env', {})
        for step in current['steps']:
            if 'uses' in step:
                assert release.SHA.fullmatch(step['uses'].split('@', 1)[1])
    publish = job['steps'][-1]['run']
    assert 'gh run download "$GITHUB_RUN_ID"' in publish
    assert 'sha256sum --check --strict SHA256SUMS' in publish
    assert 'GITHUB_SHA' in publish
    assert 'gh release create' in publish and '--verify-tag --draft' in publish
    assert '--clobber' not in publish and 'gh release edit' not in publish


def test_release_artifacts_are_attempt_bound_and_never_upload_the_extracted_source():
    config = yaml.load((ROOT / '.github/workflows/release-provenance.yml').read_text(), Loader=yaml.BaseLoader)
    for name in ('prepare', 'attest'):
        job = config['jobs'][name]
        upload = next(step for step in job['steps'] if step.get('uses', '').startswith('actions/upload-artifact@'))
        assert upload['with']['name'] == job['outputs']['artifact']
        assert '${{ github.run_attempt }}' in upload['with']['name']
        assert upload['with']['if-no-files-found'] == 'error'
    paths = config['jobs']['prepare']['steps'][-1]['with']['path'].splitlines()
    assert len(paths) == 5
    assert all(not path.endswith('/source') and not path.endswith('/*') for path in paths)
    download = config['jobs']['attest']['steps'][0]['with']
    assert download['name'] == '${{ needs.prepare.outputs.artifact }}'


@pytest.mark.skipif(sys.platform == 'win32' or not shutil.which('bash'), reason='Executes the Ubuntu release shell step')
@pytest.mark.parametrize('damage', [None, 'tampered', 'wrong-commit', 'missing-bundle'])
def test_draft_shell_checks_downloaded_evidence_before_release_create(repo, tmp_path, damage):
    root, identity = repo
    source = tmp_path / 'artifact'
    release.package_source(root, identity, source)
    (source / 'source.spdx.json').write_text(json.dumps({'spdxVersion': 'SPDX-2.3', 'documentNamespace': 'fixture'}))
    if damage == 'wrong-commit':
        (source / 'source-identity.json').write_text(json.dumps(dict(identity, commit='a' * 40)))
    release.finalize(source)
    (source / 'provenance.sigstore.jsonl').write_text('fixture only, never accepted as real provenance')
    if damage == 'tampered':
        next(source.glob('*.zip')).write_bytes(b'tampered')
    elif damage == 'missing-bundle':
        (source / 'provenance.sigstore.jsonl').unlink()
    # A PATH fixture captures gh operations; no API call or real release occurs.
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    cli = bin_dir / 'gh'
    cli.write_text(f'#!{sys.executable}\n' + '''import json, os, pathlib, shutil, sys
a=sys.argv[1:]
assert os.environ['GH_TOKEN']=='fixture-read-write-token'
if a[:2]==['run','download']:
    assert a[2]=='123' and a[a.index('--name')+1]=='ods-attested-source-1'
    target=pathlib.Path(a[a.index('--dir')+1]); target.mkdir(parents=True)
    for p in pathlib.Path(os.environ['FIXTURE_ARTIFACT']).iterdir():
        if p.is_file() and p.name!='source-identity.json': shutil.copyfile(p,target/p.name)
elif a[:2]==['release','create']:
    assert '--draft' in a and '--verify-tag' in a
    pathlib.Path(os.environ['FIXTURE_CALL']).write_text(json.dumps(a))
else: raise AssertionError(a)
''')
    cli.chmod(0o755)
    call = tmp_path / 'release-call.json'
    config = yaml.load((ROOT / '.github/workflows/release-provenance.yml').read_text(), Loader=yaml.BaseLoader)
    script = config['jobs']['draft']['steps'][0]['run']
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
               GH_TOKEN='fixture-read-write-token', GITHUB_RUN_ID='123',
               GITHUB_REF='refs/tags/' + identity['tag'], GITHUB_SHA=identity['commit'],
               SOURCE_ARTIFACT='ods-attested-source-1', RUNNER_TEMP=str(tmp_path / 'runner'),
               FIXTURE_ARTIFACT=str(source), FIXTURE_CALL=str(call))
    completed = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
    assert (completed.returncode == 0) is (damage is None), completed.stderr
    assert call.exists() is (damage is None)


def test_real_repository_source_can_be_packaged(tmp_path):
    # The small trust-boundary fixtures cannot detect a nonportable filename,
    # case collision or missing entrypoint in the actual release tree.
    if not (ROOT / '.git').exists():
        pytest.skip('Focused file-only fixture; full checkout packaging runs in CI')
    commit = release.git(ROOT, 'rev-parse', 'HEAD').decode().strip()
    identity = {'repository': release.REPOSITORY, 'tag': 'v0.0.0-fixture',
                'tagObject': '0' * 40, 'commit': commit}
    output = tmp_path / 'actual-source'
    release.package_source(ROOT, identity, output)
    with zipfile.ZipFile(next(output.glob('*.zip'))) as archive:
        names = set(archive.namelist())
        assert '.git/config' not in names
        for path in ('install.sh', 'install.ps1', 'ods/install.sh', 'ods/installers/windows.ps1'):
            expected = release.git(ROOT, 'show', commit + ':' + path)
            # Git's committed attributes deliberately export PowerShell as
            # CRLF and shell scripts as LF, independent of the packaging host.
            attribute = release.git(ROOT, 'check-attr', '--source=' + commit, 'eol', '--', path)
            eol = attribute.decode().strip().rsplit(': ', 1)[-1]
            if eol in ('lf', 'crlf'):
                expected = expected.replace(b'\r\n', b'\n')
                if eol == 'crlf':
                    expected = expected.replace(b'\n', b'\r\n')
            assert archive.read(path) == expected
    # This exercises actual packaging, not signature acceptance. Nothing is
    # uploaded, tagged, signed or executed from the generated archives.
