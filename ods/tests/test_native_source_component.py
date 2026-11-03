"""Source component bytes/Git/profiles only. No SDK, account, job or Mac claim."""
import copy
import ast
import hashlib
import importlib.util
import json
import io
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import tarfile

import pytest

if sys.platform == 'win32':
    pytest.skip('POSIX owner packaging fixture', allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_source_component', ROOT / 'installers/macos/lib/pixel-native-source-broker.py')
component = importlib.util.module_from_spec(spec)
spec.loader.exec_module(component)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL).decode().strip()


@pytest.fixture
def package(tmp_path, monkeypatch):
    source = tmp_path / 'component-source-input'
    tree = ast.parse((ROOT / 'installers/macos/lib/pixel-native-bootstrap.py').read_text())
    pins = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ('ODS_BUNDLED_REF', 'ODS_BUNDLED_SHA256')}
    ref = pins['ODS_BUNDLED_REF']
    authority = component.SOURCE_AUTHORITIES[ref]
    path = ROOT / 'vendor/pixel.bundle'
    before = path.read_bytes()
    assert len(before) < 64 * 1024 * 1024
    assert hashlib.sha256(before).hexdigest() == pins['ODS_BUNDLED_SHA256'] == authority['bundleSha256']
    for key, value in {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'file', 'GIT_NO_REPLACE_OBJECTS': '1'}.items():
        monkeypatch.setenv(key, value)
    subprocess.run(['git', 'clone', '--bare', '--no-local', '--no-hardlinks', '--', str(path), str(source)],
        check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    assert git(source, 'rev-parse', ref + '^{tree}') == authority['tree']
    files = component.source_payloads(source, ref, '/usr/bin/python3')
    assert hashlib.sha256(path.read_bytes()).hexdigest() == pins['ODS_BUNDLED_SHA256']
    for relative, checksum in authority['inputs'].items():
        blob = subprocess.check_output(['git', '-C', str(source), 'cat-file', 'blob', ref + ':' + relative])
        assert hashlib.sha256(blob).hexdigest() == checksum
    assert hashlib.sha256(files['source-broker.env']).hexdigest() == authority['templateSha256']
    return source, ref, files


def test_actual_tracked_source_contract_and_private_query_not_adopted(package):
    source, ref, files = package
    (source / '.env.example').write_text("PIXEL_SOURCE_GMAIL_QUERY='private term must not be adopted'\n")
    (source / 'deploy/source-broker').mkdir(parents=True)
    (source / 'deploy/source-broker/broker.py').write_text('owner changed uncommitted body')
    assert component.source_payloads(source, ref, '/usr/bin/python3') == files
    assert files['broker.py'] == (ROOT / 'vendor/pixel/deploy/source-broker/broker.py').read_bytes()
    assert b'private term' not in files['source-broker.env']
    template = files['source-broker.env'].decode()
    assert "PIXEL_SOURCE_GMAIL_QUERY='in:inbox'" in template
    assert "PIXEL_SOURCE_TOKEN_PATH='/private/var/lib/pixel-source-broker/private/google-token.json'" in template
    assert list(files) == list(component.FILES)
    component.validate_payloads(files, ref=ref, python='/usr/bin/python3')


def test_template_matches_actual_selected_configure_defaults_with_only_fixed_paths(tmp_path, package):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Existing native Node required; this test installs nothing')
    environment = dict(os.environ)
    gitfile = ROOT.parent / '.git'
    if sys.platform.startswith('linux') and gitfile.is_file():
        path = PureWindowsPath(gitfile.read_text().removeprefix('gitdir: ').strip())
        if path.is_absolute() and path.drive.endswith(':'):
            # WSL reads this Windows-created private worktree; use its actual
            # Git metadata without rewriting the worktree's .git pointer.
            environment['GIT_DIR'] = '/mnt/' + path.drive[0].lower() + '/' + '/'.join(path.parts[1:])
            environment['GIT_WORK_TREE'] = str(ROOT.parent)
    archive = subprocess.check_output(['git', '-C', str(ROOT.parent), 'archive', 'HEAD:ods/vendor/pixel'], env=environment)
    checkout = tmp_path / 'tracked-source'
    checkout.mkdir(mode=0o700)
    # Only the repository's tracked Source payload, never an owner checkout's
    # ignored files. Configure uses builtins and its own tracked local modules.
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        entries = source.getmembers()
        assert len(entries) < 2000 and sum(item.size for item in entries) < 64 * 1024 * 1024
        for item in entries:
            assert not Path(item.name).is_absolute() and '..' not in Path(item.name).parts
            target = checkout / item.name
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                assert item.isfile()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.extractfile(item).read())
                target.chmod(item.mode & 0o777)
    home = tmp_path / 'invented-home'
    home.mkdir(mode=0o700)
    launcher = home / 'invented-openclaw-never-executed'
    launcher.write_text('#!/bin/sh\nexit 97\n')
    launcher.chmod(0o600)
    answers = tmp_path / 'answers.json'
    answers.write_text(json.dumps({'agentId': 'pixel', 'deploymentName': 'ods-default',
        'openclawHome': str(home / '.openclaw'), 'installDir': str(checkout), 'openclawBin': str(launcher)}))
    answers.chmod(0o600)
    result = subprocess.run([node, str(checkout / 'scripts/configure.mjs'), '--answers', str(answers)],
        cwd=checkout, env={'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
            'PATH': str(Path(node).parent) + ':/usr/bin:/bin'}, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    from pixel_connections.custody import parse_env
    actual = dict(parse_env((checkout / '.generated/source-broker.env').read_text()))
    # Actual configure's tracked inputs must also equal the independently
    # pinned bundle blobs; this is not a component's self-reported hash table.
    for relative in component.SOURCE_INPUTS:
        assert hashlib.sha256((checkout / relative).read_bytes()).hexdigest() == component.SOURCE_AUTHORITIES[package[1]]['inputs'][relative]
    generated = package[2]
    expected = dict(parse_env(generated['source-broker.env'].decode()))
    native = {
        'PIXEL_SOURCE_TOKEN_PATH': '/private/var/lib/pixel-source-broker/private/google-token.json',
        'PIXEL_SOURCE_PROJECTION_DIR': '/private/var/lib/pixel-source-broker/projection',
        'PIXEL_ACTION_PROPOSAL_DIR': '/private/var/lib/pixel-source-broker/proposals',
        'PIXEL_ACTION_RESULT_DIR': '/private/var/lib/pixel-source-broker/results',
        'PIXEL_SOURCE_OAUTH_DIR': '/private/var/lib/pixel-source-broker/private/oauth'}
    assert len(actual) == len(expected) == 24
    assert {**actual, **native} == expected
    assert not os.path.lexists(home / '.openclaw/openclaw.json')


@pytest.mark.parametrize('what', ['private-query', 'broker', 'configure-input', 'env-input'])
def test_coherently_resealed_component_cannot_redefine_independent_source_authority(package, what):
    _source, ref, files = package
    forged = copy.deepcopy(files)
    manifest = json.loads(forged['component.json'])
    if what == 'private-query':
        forged['source-broker.env'] = forged['source-broker.env'].replace(
            b"PIXEL_SOURCE_GMAIL_QUERY='in:inbox'", b"PIXEL_SOURCE_GMAIL_QUERY='subject:invented-confidential-b3-probe'")
        manifest['files']['source-broker.env'] = hashlib.sha256(forged['source-broker.env']).hexdigest()
    elif what == 'broker':
        forged['broker.py'] += b'\n# invented altered code, never executed\n'
        checksum = hashlib.sha256(forged['broker.py']).hexdigest()
        manifest['inputs']['deploy/source-broker/broker.py'] = checksum
        manifest['files']['broker.py'] = checksum
    else:
        manifest['inputs']['scripts/configure.mjs' if what == 'configure-input' else '.env.example'] = '0' * 64
    forged['component.json'] = component.encoded(manifest)
    with pytest.raises(ValueError, match='component-changed'):
        component.validate_payloads(forged, ref=ref, python='/usr/bin/python3')


def test_unknown_source_ref_refuses_before_owner_git_or_root_component_validation(package, monkeypatch):
    called = []
    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError('Unknown ref must not trigger Git')
    monkeypatch.setattr(component.subprocess, 'run', forbidden)
    with pytest.raises(ValueError, match='ref-unqualified'):
        component.source_payloads('/invented/missing/checkout', '0' * 40, '/usr/bin/python3')
    with pytest.raises(ValueError, match='ref-unqualified'):
        component.validate_payloads(package[2], ref='0' * 40, python='/usr/bin/python3')
    assert not called


def test_exact_43_current_pin_keeps_only_the_reviewed_42_predecessor(package):
    assert package[1] == '224ed399e38273db0f9d4834ceab55d493a99cf3'
    assert set(component.SOURCE_AUTHORITIES) == {
        'acc5550888fe034a9a42e90307c9f7a664726315', '224ed399e38273db0f9d4834ceab55d493a99cf3'}


def test_unknown_44_coherently_resealed_component_cannot_expand_the_closed_table(package, monkeypatch):
    files = copy.deepcopy(package[2])
    forged = json.loads(files['component.json'])
    forged['sourceRef'] = '4'*40
    files['component.json'] = component.encoded(forged)
    with pytest.raises(ValueError, match='ref-unqualified'):
        component.validate_payloads(files, ref='4'*40, python='/usr/bin/python3')
    monkeypatch.setattr(component.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Unqualified 44 must refuse before Git'))
    with pytest.raises(ValueError, match='ref-unqualified'):
        component.source_payloads('/invented/unknown44', '4'*40, '/usr/bin/python3')


@pytest.mark.parametrize('change', ['missing-profile', 'extra', 'broker', 'profile', 'ref', 'python', 'input-sha', 'duplicate'])
def test_component_closed_shape_and_provenance_refuses(package, change):
    _source, ref, original = package
    files = copy.deepcopy(original)
    if change == 'missing-profile':
        del files['source-action.sb']
    elif change == 'extra':
        files['source-any.sb'] = b'extra'
    elif change == 'broker':
        files['broker.py'] += b'changed'
    elif change == 'profile':
        files['source-action.sb'] += b'(allow default)\n'
    else:
        value = json.loads(files['component.json'])
        if change == 'ref':
            value['sourceRef'] = '1' * 40
        elif change == 'python':
            value['hostPython'] = '/owner/other/python'
        elif change == 'input-sha':
            value['inputs']['deploy/source-broker/broker.py'] = '0' * 64
        elif change == 'duplicate':
            files['component.json'] = b'{"schemaVersion":1,"schemaVersion":1}'
        if change != 'duplicate':
            files['component.json'] = component.encoded(value)
    with pytest.raises(ValueError):
        component.validate_payloads(files, ref=ref, python='/usr/bin/python3')


def test_profiles_have_exact_roles_paths_and_no_jobs_or_grant_widening():
    profiles = component.profiles('/usr/bin/python3')
    assert set(profiles) == set(component.PROFILES)
    for name, body in profiles.items():
        assert body.startswith(b'(version 1)\n(deny default)')
        assert b'(allow default)' not in body and b'/Users/' not in body and b'launchctl' not in body
        assert b'process-fork' not in body
        assert b'(allow process-exec (literal "/usr/bin/python3"))' in body
        assert b'file-write* (subpath "/private/var/lib/pixel-source-broker/private")' not in body or name == 'source-exchange.sb'
    assert b'proposals' not in profiles['source-sync.sb']
    assert b'(subpath "/private/var/lib/pixel-source-broker/results")' in profiles['source-direct.sb']
    assert b'(subpath "/private/var/lib/pixel-source-broker/private/oauth")' in profiles['source-exchange.sb']


def test_component_read_is_bounded_and_fd_based(package, tmp_path):
    _source, ref, files = package
    root = tmp_path / 'bundle'
    target = root / component.COMPONENT
    target.mkdir(parents=True, mode=0o755)
    for name, body in files.items():
        (target / name).write_bytes(body)
        (target / name).chmod(0o755 if name == 'broker.py' else 0o644)
    assert component.bundle_payloads(root, ref=ref, python='/usr/bin/python3') == files
    broker = target / 'broker.py'
    broker.unlink()
    broker.symlink_to(ROOT / 'vendor/pixel/deploy/source-broker/broker.py')
    with pytest.raises(OSError):
        component.bundle_payloads(root, ref=ref, python='/usr/bin/python3')
    assert hashlib.sha256((ROOT / 'vendor/pixel/deploy/source-broker/broker.py').read_bytes()).hexdigest() == json.loads(files['component.json'])['files']['broker.py']
