"""Request-local backends and real Root data FDs; no provider or native host.

Darwin ownership/ACL/Directory Service and launchd observations use the explicit
existing Linux driver. Real files, CAS checks and the full sealed deployment
and bundle qualifiers remain active.
"""
# ruff: noqa: E402,F401,F811
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

if sys.platform != 'linux':
    pytest.skip('Linux metadata driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_executor import (executor, qualification_operation, generation, built,
    source, package, native, deployment, material, publication, helper)
from pixel_connections import custody as c, source_backend as b
from pixel_access_bridge import LaunchdAccessBridge
import pixel_macos_custody as mac


def test_backend_capsules_are_local_immutable_and_cannot_supply_a_native_seal(tmp_path):
    first, second = c.Paths(state=tmp_path / 'one'), c.Paths(state=tmp_path / 'two')
    sent = []
    linux = b.for_bridge(None, linux_paths=first, linux_run=lambda args, timeout: sent.append(args) or 0)
    other = b.for_bridge(None, linux_paths=second, linux_run=lambda *_args: 1)
    bridge = object.__new__(LaunchdAccessBridge)
    native = b.for_bridge(bridge, linux_paths=first, linux_run=None)
    assert type(native) is b.NativeSource and type(linux) is b.LinuxSource
    linux.sync()
    assert sent == [['systemctl', 'start', '--no-block', 'pixel-source-broker.service']]
    assert linux.paths is first and other.paths is second
    assert c.BROKER_USER == 'pixel-source-broker' and c.Paths().state == Path('/var/lib/pixel-source-broker')
    with pytest.raises(FrozenInstanceError):
        native.bridge = None
    with pytest.raises(c.CustodyError, match='backend-required'):
        b.NativeSource(bridge, object())
    with pytest.raises(c.CustodyError, match='scope-required'):
        _ = native.selection


@pytest.mark.parametrize('name', ['../' + 'a' * 32 + '.flow.json', 'a' * 32 + '.result.json',
    'A' * 32 + '.flow.json', 'a' * 31 + '.code.json', 'a' * 32 + '.flow.json/extra',
    'a' * 32 + '\\xflow.json', 'a' * 32 + '.code.json\x00'])
def test_bad_handoff_name_refuses_before_any_identity_or_file_probe(name, monkeypatch):
    source = b.for_bridge(object.__new__(LaunchdAccessBridge), linux_paths=c.Paths(), linux_run=None)
    monkeypatch.setattr(b.NativeSource, 'broker_ids', lambda _self: (_ for _ in ()).throw(AssertionError('identity probed')))
    with pytest.raises(c.CustodyError, match='invalid-flow'):
        source.write_handoff(name, {})


@pytest.fixture
def backend(executor, monkeypatch):
    n = executor
    monkeypatch.setattr(b, 'protected_directory', mac.protected_directory)
    monkeypatch.setattr(n.f.bridge, 'connection_deployment', lambda: n.f.value)
    original_stat = os.stat
    def metadata(path, *args, **kwargs):
        info = original_stat(path, *args, **kwargs)
        directory = kwargs.get('dir_fd')
        if directory is not None and Path(os.readlink(f'/proc/self/fd/{directory}')).is_relative_to(n.world):
            fields = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
            fields['st_uid'], fields['st_gid'] = n.owners.get((info.st_dev, info.st_ino), (0, 0))
            return SimpleNamespace(**fields)
        return info
    monkeypatch.setattr(os, 'stat', metadata)
    n.backend = b.for_bridge(n.f.bridge, linux_paths=c.Paths(), linux_run=None)
    return n


def broker_file(n, relative, body, *, gid=None):
    path = c.NATIVE_STATE / relative
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(fd, n.identity['id'], n.identity['id'] if gid is None else gid)
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def test_native_handoff_result_and_token_metadata_use_bound_fds_not_linux_identity(backend, monkeypatch):
    n, flow = backend, '0123456789abcdef' * 2
    monkeypatch.setattr(c, 'broker_ids', lambda: (_ for _ in ()).throw(AssertionError('Linux identity used')))
    n.backend.write_handoff(flow + '.flow.json', {'invented': 'private consent fixture'})
    n.backend.write_handoff(flow + '.code.json', {'invented': 'private code fixture'})
    with pytest.raises(FileExistsError):
        n.backend.write_handoff(flow + '.flow.json', {'invented': 'must not overwrite'})
    token = broker_file(n, 'private/google-token.json', b'INVENTED_TOKEN_NOT_READ')
    before, original_read = token.stat(), os.read
    def guarded_read(fd, limit):
        if os.fstat(fd).st_ino == before.st_ino:
            raise AssertionError('Root read credential bytes')
        return original_read(fd, limit)
    monkeypatch.setattr(os, 'read', guarded_read)
    identity = n.backend.sign_in_identity()
    assert identity[:2] == (before.st_dev, before.st_ino) and n.backend.holds_sign_in()
    def exchange(arguments, timeout):
        assert arguments == ['systemctl', 'start', 'pixel-source-exchange@' + flow + '.service'] and timeout == 120
        broker_file(n, 'private/oauth/' + flow + '.result.json', c._native_encoded({
            'schemaVersion': 1, 'kind': 'pixel-source-oauth-result', 'finishedAt': '2030-01-01T00:00:00Z',
            'flowId': flow, 'outcome': 'connected', 'errorClass': None, 'httpStatus': 200,
            'errorType': None, 'grantedScopes': ['openid', 'openid'], 'account': 'fixture@example.invalid'}))
        return 1  # The bound business result, not process exit status, decides.
    monkeypatch.setattr(b.NativeSource, 'run', property(lambda _self: exchange))
    assert n.backend.exchange(flow) == {'outcome': 'connected', 'errorClass': None,
        'grantedScopes': ['openid'], 'account': 'fixture@example.invalid'}
    assert not list((c.NATIVE_STATE / 'private/oauth').iterdir())
    assert n.backend.sign_in_identity() == identity
    n.backend.forget_sign_in()
    assert not token.exists() and n.backend.sign_in_identity() is None


def test_native_unsafe_result_or_token_is_preserved_and_no_dispatch_occurs(backend):
    n, flow = backend, 'fedcba9876543210' * 2
    result = broker_file(n, 'private/oauth/' + flow + '.result.json', b'{"schemaVersion":1,"schemaVersion":1}')
    with pytest.raises(c.CustodyError, match='result-invalid'):
        n.backend._result(result.name, c.EXCHANGE_KEYS, 'pixel-source-oauth-result')
    assert result.exists()
    result.unlink()
    token = broker_file(n, 'private/google-token.json', b'INVENTED_TOKEN_NOT_READ')
    linked = token.with_name('unexpected-link')
    os.link(token, linked)
    with pytest.raises(c.CustodyError, match='unsafe'):
        n.backend.sign_in_identity()
    assert linked.exists() and token.exists()
    assert not any(args[:2] in (['/bin/launchctl', 'bootstrap'], ['/bin/launchctl', 'kickstart']) for args in n.driver.sent)


def test_native_existing_generation_prepare_is_observation_and_shared_env_policy_is_literal(backend):
    n = backend
    before = (c.NATIVE_META / 'source-jobs-prepare.json').read_bytes()
    n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    assert (c.NATIVE_META / 'source-jobs-prepare.json').read_bytes() == before
    options = {'gateway': {'email': True, 'calendar': True, 'calendarWrite': False,
        'calendarDirect': False, 'emailDrafts': False}, 'calendar_id': 'primary',
        'time_zone': 'America/Sao_Paulo', 'gmail_window_days': 7}
    text = n.backend.connection_env(**options)
    values = dict(c.parse_env(text))
    assert all(values[key] == value for key, value in c._native_environment_paths().items())
    assert values['PIXEL_SOURCE_TIME_ZONE'] == options['time_zone']
    assert values['PIXEL_SOURCE_GMAIL_QUERY'].endswith(' newer_than:7d')
    assert values['PIXEL_LIMB_CALENDAR_ENABLED'] == '1' and values['PIXEL_LIMB_CALENDAR_WRITE_ENABLED'] == '0'
    assert not any(args[:2] in (['/bin/launchctl', 'bootstrap'], ['/bin/launchctl', 'kickstart']) for args in n.driver.sent)


def test_same_environment_after_reboot_stops_fresh_child_and_publishes_new_inactive_generation(backend, monkeypatch):
    n = backend
    old = json.loads((c.NATIVE_META / 'source-jobs-prepare.json').read_bytes())['generation']
    n.executor.start('sync')
    new_boot = '11234567-89AB-4DEF-8123-0123456789AB'
    original = n.f.bridge.command
    def command(argv, timeout=20):
        if argv == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
            return new_boot + '\n'
        return original(argv, timeout=timeout)
    monkeypatch.setattr(n.f.bridge, 'command', command)
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: new_boot)
    text = n.backend._environment_text()
    result = n.backend.install_env(text)
    assert result['generation'] != old
    assert c.NATIVE_ENV.read_text() == text
    assert all(child.poll() is not None for child in n.driver.children)
    assert json.loads((c.NATIVE_STATE / 'custody.json').read_bytes())['schemaVersion'] == 2
    assert json.loads((c.NATIVE_META / 'source-jobs-prepare.json').read_bytes())['witness']['boot'] == new_boot
    assert not any('-k' in args for args in n.driver.sent)


def test_inactive_native_status_holds_token_without_reading_or_trusting_stale_projection(backend, monkeypatch):
    n = backend
    token = broker_file(n, 'private/google-token.json', b'INVENTED_TOKEN_NOT_READ')
    status = broker_file(n, 'projection/status.json', b'{"stale":"would otherwise look connected"}', gid=n.f.bridge.owner.pw_gid)
    before = token.stat().st_ino
    original_read = os.read
    def read(fd, limit):
        if os.fstat(fd).st_ino in (before, status.stat().st_ino):
            raise AssertionError('inactive status read token or stale projection')
        return original_read(fd, limit)
    monkeypatch.setattr(os, 'read', read)
    result = b.native_status_projection(n.f.bridge)
    assert result['statePresent'] and result['tokenIdentity'] is not None
    assert not result['dispatchReady'] and result['rawStatus'] is None
    assert result['reason'] == 'connection-admission-held' and token.stat().st_ino == before


def test_known_environment_gate_rename_is_readonly_recovery_status(backend, monkeypatch):
    n = backend
    token = broker_file(n, 'private/google-token.json', b'INVENTED_TOKEN_NOT_READ')
    original_rename = os.rename
    hit = []
    def rename(first, second, **kwargs):
        result = original_rename(first, second, **kwargs)
        if first == 'custody.json' and second == 'custody.before.json' and not hit:
            hit.append(True)
            raise OSError('invented Gate rename reply loss')
        return result
    monkeypatch.setattr(os, 'rename', rename)
    text = c.native_environment_text(n.selected, {'PIXEL_SOURCE_CALENDAR_ID': 'retained-calendar'}, _publication=True)
    with pytest.raises(OSError, match='Gate rename reply loss'):
        n.backend.install_env(text)
    assert hit and not (c.NATIVE_STATE / 'custody.json').exists()
    before = (c.NATIVE_META / 'source-environment-generation.json').read_bytes()
    token_inode = token.stat().st_ino
    result = b.native_status_projection(n.f.bridge)
    assert result['tokenIdentity'] is not None and not result['dispatchReady']
    assert result['rawStatus'] is None and result['reason'] == 'connection-admission-held'
    assert token.stat().st_ino == token_inode
    assert (c.NATIVE_META / 'source-environment-generation.json').read_bytes() == before


@pytest.mark.parametrize('raw', [b'{"kind":"x","kind":"x"}', b'NaN', b'{"schemaVersion":1}'])
def test_shared_result_decoder_rejects_unknown_or_ambiguous_receipts(raw):
    with pytest.raises(c.CustodyError, match='result-invalid'):
        c.decode_oauth_result(raw, c.EXCHANGE_KEYS, 'pixel-source-oauth-result')
