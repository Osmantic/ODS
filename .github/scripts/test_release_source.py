"""Release archive and trust-boundary tests; no release is created or published."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
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
    job = config['jobs']['draft']
    assert "github.repository == 'Osmantic/ODS'" in job['if']
    assert "startsWith(github.ref, 'refs/tags/v')" in job['if']
    for step in job['steps']:
        if 'uses' in step:
            assert release.SHA.fullmatch(step['uses'].split('@', 1)[1])
    publish = job['steps'][-1]['run']
    assert 'gh release create' in publish and '--verify-tag --draft' in publish
    assert '--clobber' not in publish and 'gh release edit' not in publish


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
