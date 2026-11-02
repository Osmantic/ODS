"""Offline native integrations deployment contracts; no launchd or kernel isolation claims."""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('Native owner account APIs; run in WSL/Linux or macOS')

from contextlib import contextmanager, nullcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import socket
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_integrations_service',
    ROOT / 'installers/macos/lib/pixel-native-integrations-service.py')
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


@pytest.fixture
def world(monkeypatch):
    entry = SimpleNamespace(pw_name='fixture', pw_uid=501, pw_gid=20, pw_dir='/Users/fixture')
    monkeypatch.setattr(native.pwd, 'getpwnam', lambda _name: entry)
    return entry


def test_native_service_uses_persistent_runtime_private_real_home_and_no_credentials(world):
    result = native.render(python='/Library/Developer/Python',
        program_root='/usr/local/libexec/ods-pixel-services/integrations', owner='fixture')
    document = plistlib.loads(result['plist'])
    args = document['ProgramArguments']
    assert args[:2] == ['/usr/bin/env', '-i']
    assert 'HOME=/Users/fixture' in args
    assert args[-3:] == ['serve', '/private/var/lib/ods-pixel-integrations/control.sock',
                        '/Users/fixture/.config/ods/pixel-integrations']
    assert 'GroupName' not in document and document['UserName'] == 'fixture'
    assert document['KeepAlive'] is True and document['ExitTimeOut'] == 300
    assert document['WorkingDirectory'] == '/' and document['Umask'] == 0o077
    assert b'owner.key' not in result['plist'] and b'token' not in result['plist']
    assert 'MemoryLimit' not in document  # No fictitious cgroup-equivalent claim.


@pytest.mark.parametrize('fault', ['root', 'relative', 'overlap', 'label', 'long-home'])
def test_native_plan_refuses_bad_or_unrepresentable_paths_before_writing(world, fault):
    args = {'python': '/Library/Developer/Python', 'program_root': '/usr/local/libexec/ods-pixel-services/integrations', 'owner': 'fixture'}
    if fault == 'root':
        args['owner'] = 'root'
    elif fault == 'relative':
        args['runtime'] = 'runtime'
    elif fault == 'overlap':
        args['logs'] = '/private/var/lib/ods-pixel-integrations'
    elif fault == 'label':
        args['label'] = 'com.other.service'
    else:
        world.pw_dir = '/Users/' + 'u' * 90
    with pytest.raises(ValueError):
        native.render(**args)


@pytest.fixture
def publication(world, monkeypatch):
    monkeypatch.setattr(native.sys, 'platform', 'darwin')
    monkeypatch.setattr(native.os, 'geteuid', lambda: 0)
    verified = []
    fake = SimpleNamespace(custody=SimpleNamespace(
        protected_bytes=lambda path, **kwargs: verified.append(path),
        protected_directory=lambda *args, **kwargs: nullcontext()))
    monkeypatch.setattr(native, 'operations_helpers', lambda: fake)
    sources = {name: (ROOT / 'extensions/services/pixel-agent/host' / name).read_bytes() for name in native.SOURCES}
    args = {'sources': sources, 'expected_sha256': {name: hashlib.sha256(body).hexdigest() for name, body in sources.items()},
            'python': '/Library/Developer/Python', 'program_root': '/usr/local/libexec/ods-pixel-services/integrations',
            'owner': 'fixture', 'definition': '/Library/LaunchDaemons/com.ods.pixel-native-integrations.plist'}
    return args, verified


def test_publication_plan_is_exact_complete_nine_files_and_does_not_provision(publication, monkeypatch):
    args, verified = publication
    monkeypatch.setattr(native, 'provision', lambda *args, **kwargs: pytest.fail('read-only plan provisioned'))
    files = native.publication_files(**args)
    assert len(files) == 9
    assert {p.name for p, _, _, _ in files} == native.SOURCES | {'com.ods.pixel-native-integrations.plist'}
    assert all(mode == 0o644 and gid == 0 for _, _, mode, gid in files)
    assert verified == ['/Library/Developer/Python']


@pytest.mark.parametrize('fault', ['missing', 'extra', 'changed', 'syntax', 'definition'])
def test_incomplete_or_changed_snapshot_cannot_publish(publication, fault):
    args, _ = publication
    if fault == 'missing':
        args['sources'].pop('integrations_ntfy.py')
    elif fault == 'extra':
        args['sources']['extra.py'] = b'pass'
    elif fault == 'changed':
        args['sources']['unix_peer.py'] = b'pass'
    elif fault == 'syntax':
        args['sources']['unix_peer.py'] = b'not valid python'
        args['expected_sha256']['unix_peer.py'] = hashlib.sha256(args['sources']['unix_peer.py']).hexdigest()
    else:
        args['definition'] = '/Library/LaunchDaemons/com.other.plist'
    with pytest.raises((ValueError, SyntaxError)):
        native.publication_files(**args)


def test_readiness_runs_only_bounded_owner_get_and_agent_inventory(world, monkeypatch):
    calls = []
    monkeypatch.setattr(native, 'operations_helpers', lambda: SimpleNamespace(custody=SimpleNamespace(protected_bytes=lambda *args, **kwargs: b'')))
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'schemaVersion': 1, 'status': 'ready', 'owner': True, 'agent': True}))
    monkeypatch.setattr(native.subprocess, 'run', run)
    assert native.readiness(owner='fixture', python='/Library/Developer/Python')
    args, options = calls[0]
    assert args[-3:] == ['health', '/private/var/lib/ods-pixel-integrations/control.sock', '/Users/fixture/.config/ods/pixel-integrations']
    assert options['user'] == 501 and options['group'] == 20 and options['extra_groups'] == []
    assert options['timeout'] == 15 and options['env'] == {'PATH': '/usr/bin:/bin', 'HOME': '/Users/fixture'}
    assert all(name not in ' '.join(args) for name in ('notify', 'discover', 'decide', 'send-digest'))


@pytest.mark.parametrize('output', [b'{}', b'{"status":"ready"}', b'not json', b'{"schemaVersion":1,"status":"ready","owner":true,"agent":false}'])
def test_readiness_fails_closed_for_partial_or_corrupt_results(world, monkeypatch, output):
    monkeypatch.setattr(native, 'operations_helpers', lambda: SimpleNamespace(custody=SimpleNamespace(protected_bytes=lambda *args, **kwargs: b'')))
    monkeypatch.setattr(native.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output))
    with pytest.raises(ValueError, match='readiness-failed'):
        native.readiness(owner='fixture', python='/Library/Developer/Python')


@pytest.fixture
def retirement(world, monkeypatch, tmp_path):
    """Disposable selection fixture; root custody/ACL provider is explicitly mocked."""
    world.pw_uid, world.pw_gid = os.getuid(), os.getgid()
    if world.pw_uid == 0:
        pytest.skip('Disposable owner metadata fixture requires a nonroot test user')
    runtime, auth, logs = [tmp_path / name for name in ('runtime', 'auth', 'logs')]
    runtime.mkdir(mode=0o700)
    auth.mkdir(mode=0o755)
    logs.mkdir(mode=0o755)
    folder = auth / str(world.pw_uid)
    folder.mkdir(mode=0o700)
    (folder / 'owner.key').write_bytes(b'k' * 32)
    for name in ('stdout.log', 'stderr.log'):
        (logs / name).write_text('invented diagnostic\n')
        (logs / name).chmod(0o600)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(runtime / 'control.sock'))
    (runtime / 'control.sock').chmod(0o600)
    sock.close()  # A stopped service's stale own socket is expected.
    # These objects represent provisioned owner state, independent of the
    # temporary parent's inherited group on Darwin.
    for path in (runtime, runtime / 'control.sock', logs / 'stdout.log', logs / 'stderr.log'):
        os.chown(path, world.pw_uid, world.pw_gid)
    @contextmanager
    def directory(path, **kwargs):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            yield fd
        finally:
            os.close(fd)
    custody = SimpleNamespace(protected_directory=directory, _require_no_acl=lambda _fd: None)
    monkeypatch.setattr(native, 'operations_helpers', lambda: SimpleNamespace(custody=custody))
    checked = []
    monkeypatch.setattr(native, '_published_common', lambda: SimpleNamespace(
        native_owner_key=lambda uid: checked.append(uid), validate_native_state=lambda *args, **kwargs: None))
    monkeypatch.setattr(native, 'RUNTIME', runtime)
    monkeypatch.setattr(native, 'AUTH_ROOT', auth)
    monkeypatch.setattr(native, 'LOGS', logs)
    monkeypatch.setattr(native.sys, 'platform', 'darwin')
    monkeypatch.setattr(native.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(native, 'provision', lambda *args, **kwargs: pytest.fail('retirement provisioned state'))
    return runtime, auth, logs, folder, checked


def test_retirement_validates_only_selected_private_paths_without_changes(retirement):
    runtime, auth, logs, folder, checked = retirement
    paths = [runtime, auth, logs, folder, folder / 'owner.key', logs / 'stdout.log', logs / 'stderr.log']
    before = [(path.lstat().st_ino, path.lstat().st_mtime_ns, path.lstat().st_mode) for path in paths]
    assert native.validate_retirement('fixture') is True
    assert checked == [os.getuid()]
    assert before == [(path.lstat().st_ino, path.lstat().st_mtime_ns, path.lstat().st_mode) for path in paths]
    assert (folder / 'owner.key').read_bytes() == b'k' * 32
    assert (logs / 'stdout.log').read_text() == 'invented diagnostic\n'


@pytest.mark.parametrize('fault', ['runtime-extra', 'runtime-mode', 'socket-file', 'socket-mode',
    'auth-owner', 'auth-extra', 'log-mode', 'log-link', 'log-extra'])
def test_retirement_refuses_extra_or_unsafe_objects_without_cleaning_them(retirement, fault):
    runtime, auth, logs, folder, _ = retirement
    if fault == 'runtime-extra':
        (runtime / 'unexpected').write_text('preserve')
    elif fault == 'runtime-mode':
        runtime.chmod(0o755)
    elif fault == 'socket-file':
        (runtime / 'control.sock').unlink()
        (runtime / 'control.sock').write_text('preserve')
    elif fault == 'socket-mode':
        (runtime / 'control.sock').chmod(0o666)
    elif fault == 'auth-owner':
        (auth / 'unexpected-owner').mkdir()
    elif fault == 'auth-extra':
        (folder / 'unexpected').write_text('preserve')
    elif fault == 'log-mode':
        (logs / 'stdout.log').chmod(0o644)
    elif fault == 'log-link':
        os.link(logs / 'stdout.log', logs.parent / 'alias')
    else:
        (logs / 'unexpected').write_text('preserve')
    before = {str(path): path.lstat().st_ino for root in (runtime, auth, logs) for path in [root, *root.rglob('*')]}
    with pytest.raises(ValueError):
        native.validate_retirement('fixture')
    assert before == {str(path): path.lstat().st_ino for root in (runtime, auth, logs) for path in [root, *root.rglob('*')]}


def test_managed_upgrade_plan_then_provision_prepares_missing_launchd_logs(publication, world, monkeypatch, tmp_path):
    """Upgrade journals publication_files directly and never calls publish().

    Privileged custody/ACL/chown are explicitly mocked; only private fixture
    directories are touched, and no launchd job or actual owner state is used.
    """
    args, _ = publication
    with tempfile.TemporaryDirectory(prefix='ods-ni-') as home:
        world.pw_uid, world.pw_gid, world.pw_dir = os.getuid(), os.getgid(), home
        if world.pw_uid == 0:
            pytest.skip('Disposable owner fixture requires a nonroot test user')
        runtime, auth, logs = [tmp_path / name for name in ('runtime', 'auth', 'logs')]
        @contextmanager
        def directory(path, create=False):
            if create:
                Path(path).mkdir(mode=0o755, parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                yield fd
            finally:
                os.close(fd)
        custody = SimpleNamespace(protected_directory=directory, protected_bytes=lambda *_args, **_kwargs: b'',
                                  _require_no_acl=lambda _fd: None)
        monkeypatch.setattr(native, 'operations_helpers', lambda: SimpleNamespace(custody=custody))
        for name, path in [('RUNTIME', runtime), ('AUTH_ROOT', auth), ('LOGS', logs)]:
            monkeypatch.setattr(native, name, path)
        prepared = []
        def logs_prepare(path, *, uid, gid):
            prepared.append((path, uid, gid))
            path.mkdir(mode=0o755)
            for name in ('stdout.log', 'stderr.log'):
                (path / name).write_text('')
                (path / name).chmod(0o600)
                os.chown(path / name, uid, gid)
        monkeypatch.setattr(native, 'manager_helpers', lambda: SimpleNamespace(prepare_logs=logs_prepare))
        # Runtime chown remains real: it changes only this user's disposable
        # directory to this user's existing primary group.
        monkeypatch.setattr(native, '_acl', lambda *args: None)
        monkeypatch.setattr(native, '_published_common', lambda: SimpleNamespace(native_owner_key=lambda uid: b'k' * 32,
            verify_native_no_acl=lambda _fd: None, validate_native_state=lambda *args, **kwargs: None))
        prepared_state = []
        def prepare_state(command, **options):
            prepared_state.append((command, options))
            Path(home, '.config/ods/pixel-integrations').mkdir(mode=0o700, parents=True)
            return SimpleNamespace(returncode=0, stdout=b'{"schemaVersion":1,"status":"prepared"}\n')
        monkeypatch.setattr(native.subprocess, 'run', prepare_state)
        monkeypatch.setattr(native, 'publish', lambda **kwargs: pytest.fail('managed upgrade must not call initial publisher'))
        plan = native.publication_files(**args)
        assert len(plan) == 9 and not logs.exists()
        native.provision('fixture', python='/Library/Developer/Python')
        assert prepared == [(logs, world.pw_uid, world.pw_gid)]
        assert (logs / 'stdout.log').is_file() and (logs / 'stderr.log').is_file()
        assert (Path(home) / '.config/ods/pixel-integrations').is_dir()
        command, options = prepared_state[0]
        assert command[:3] == ['/Library/Developer/Python', '-I', '-B']
        assert command[3:] == ['/usr/local/libexec/ods-pixel-services/integrations/integrations_common.py',
                               '--prepare-native-state', str(Path(home) / '.config/ods/pixel-integrations')]
        assert options['user'] == world.pw_uid and options['group'] == world.pw_gid and options['extra_groups'] == []
