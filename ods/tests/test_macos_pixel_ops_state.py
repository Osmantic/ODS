import importlib.util
from contextlib import nullcontext
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('native_ops_state',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-ops-state.py')
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)


@pytest.fixture
def accounts(monkeypatch):
    gateway = SimpleNamespace(pw_name='fixture_owner', pw_gid=20)
    broker = SimpleNamespace(pw_name='_fixture_ops', pw_gid=61000, pw_shell='/usr/bin/false')
    group = SimpleNamespace(gr_mem=[])
    monkeypatch.setattr(ops.pwd, 'getpwuid', lambda uid: gateway if uid == 501 else broker)
    monkeypatch.setattr(ops.grp, 'getgrgid', lambda gid: group)
    monkeypatch.setattr(ops.os, 'getgrouplist', lambda name, gid: [20])
    return gateway, broker, group


def test_separate_nonlogin_identity(accounts):
    assert ops.identities(501, 61000, 61000) == accounts[:2]


@pytest.mark.parametrize('values', [(0, 61000, 61000), (501, 0, 61000),
                                  (501, 61000, 0), (501, 501, 61000),
                                  (True, 61000, 61000), (501, -2, 61000)])
def test_invalid_ids(accounts, values):
    with pytest.raises(ValueError): ops.identities(*values)


@pytest.mark.parametrize('fault', ['login', 'primary-group', 'member', 'gateway-group'])
def test_reject_shared_or_interactive_broker(accounts, monkeypatch, fault):
    gateway, broker, group = accounts
    if fault == 'login': broker.pw_shell = '/bin/zsh'
    if fault == 'primary-group': broker.pw_gid = 20
    if fault == 'member': group.gr_mem = ['other']
    if fault == 'gateway-group':
        monkeypatch.setattr(ops.os, 'getgrouplist', lambda name, gid: [20, 61000])
    with pytest.raises(ValueError): ops.identities(501, 61000, 61000)


def test_never_provisions_as_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(ops.sys, 'platform', 'darwin')
    monkeypatch.setattr(ops.os, 'geteuid', lambda: 501)
    with pytest.raises(ValueError, match='macos-root-required'):
        ops.provision(state=tmp_path / 'spool', gateway_uid=501, broker_uid=61000, broker_gid=61000)
    assert not (tmp_path / 'spool').exists()


@pytest.fixture
def provisioning(accounts, monkeypatch, tmp_path):
    # Hosted macOS temporary directories can inherit a group the runner cannot use.
    os.chown(tmp_path, -1, os.getgid())
    monkeypatch.setattr(ops.sys, 'platform', 'darwin')
    monkeypatch.setattr(ops.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(ops.custody, 'protected_directory', lambda path: nullcontext())
    ownership, acls = [], []
    monkeypatch.setattr(ops.os, 'chown', lambda path, uid, gid: ownership.append((Path(path), uid, gid)))
    monkeypatch.setattr(ops, 'acl', lambda path, entry: acls.append((Path(path), entry)))
    return dict(state=tmp_path / 'state', gateway_uid=501, broker_uid=61000, broker_gid=61000), ownership, acls


def test_layout_matches_upstream_projection_allowlist(provisioning):
    arguments, ownership, acls = provisioning
    state = ops.provision(**arguments)
    for name in ops.PRIVATE: assert (state / name).stat().st_mode & 0o7777 == 0o700
    for name in ops.SUBMISSIONS: assert (state / name).stat().st_mode & 0o7777 == 0o2770
    for name in ops.PROJECTIONS + ops.STORAGE: assert (state / name).stat().st_mode & 0o7777 == 0o2750
    assert state.stat().st_mode & 0o7777 == 0o750
    assert len(acls) == 6
    assert {path.name for path, _ in acls[:-2]} == {'results', 'events'}
    assert all('write' not in entry and 'delete' not in entry for _, entry in acls)
    assert ownership[-1][1:] == (61000, 61000)
    assert all(uid == (501 if path.name in ops.SUBMISSIONS else 61000) for path, uid, gid in ownership)


@pytest.mark.parametrize('kind', ['directory', 'file', 'symlink'])
def test_existing_state_is_not_modified(provisioning, kind):
    arguments, ownership, acls = provisioning
    state = arguments['state']
    if kind == 'directory': state.mkdir()
    elif kind == 'file': state.write_text('existing')
    else: state.symlink_to(state.parent / 'missing')
    with pytest.raises(ValueError, match='new-operations-state-required'):
        ops.provision(**arguments)
    assert not ownership and not acls
    assert os.path.lexists(state)


def test_acl_failure_never_publishes_partial_state(provisioning, monkeypatch):
    arguments, _, _ = provisioning
    def fail(path, entry): raise OSError('injected ACL failure')
    monkeypatch.setattr(ops, 'acl', fail)
    with pytest.raises(OSError): ops.provision(**arguments)
    assert not arguments['state'].exists()
    assert not list(arguments['state'].parent.glob('.pixel-ops-*'))


def test_manager_runtime_uses_owner_primary_group_not_broker_group(provisioning):
    arguments, ownership, acls = provisioning
    arguments['runtime'] = arguments.pop('state')
    result = ops.provision_manager_runtime(**arguments)
    assert result.stat().st_mode & 0o777 == 0o700
    assert ownership[-1][1:] == (501, 20)
    assert len(acls) == 2
    assert 'list' not in acls[0][1] and 'write' not in acls[0][1]
    assert all('user:_fixture_ops' in entry for _, entry in acls)
    assert 'file_inherit,only_inherit' in acls[1][1]


def test_manager_runtime_refuses_existing_directory(provisioning):
    arguments, ownership, acls = provisioning
    arguments['runtime'] = arguments.pop('state')
    arguments['runtime'].mkdir()
    with pytest.raises(ValueError): ops.provision_manager_runtime(**arguments)
    assert not ownership and not acls


@pytest.mark.parametrize('fault', [None, 'concurrent-addition', 'remove-failure', 'verification-failure'])
def test_retained_home_revokes_and_verifies_acl_before_final_empty_check(monkeypatch, tmp_path, fault):
    """Actual provision body with mocked credentials/ACL IO, not a live ACL proof."""
    state = tmp_path / 'retained-home'
    state.mkdir()
    events = []
    retained = dict(uid=61000, gid=61000, mode=0o750, acl=True)
    fd = 12345
    def chown(opened, uid, gid):
        assert opened == fd
        events.append(('owner', uid, gid))
        retained.update(uid=uid, gid=gid)
    def chmod(opened, mode):
        assert opened == fd
        events.append(('mode', mode))
        retained['mode'] = mode
    def entries(opened):
        assert opened == fd
        events.append('empty-check')
        assert retained == dict(uid=0, gid=0, mode=0o700, acl=False)
        assert events[-2] == 'verify-pinned'
        return [path.name for path in state.iterdir()]
    def remove_acl(argv, **kwargs):
        assert argv == ['/bin/chmod', '-N', str(state)] and kwargs['check'] is True
        assert retained == dict(uid=0, gid=0, mode=0o700, acl=True)
        events.append('remove-acl')
        if fault == 'concurrent-addition':
            # A named-ACL writer can still add this after chown/chmod but
            # before -N takes effect. Never adopt or erase its bytes.
            (state / 'unexpected').write_text('preserve')
        if fault == 'remove-failure': raise OSError('ACL removal failed')
        retained['acl'] = False
    def verify(opened, *, directory):
        assert opened == fd and directory is True
        events.append('verify-pinned')
        assert retained == dict(uid=0, gid=0, mode=0o700, acl=False)
        if fault == 'verification-failure': raise OSError('ACL absence unconfirmed')
    def rename(source, target):
        assert 'empty-check' in events and retained['acl'] is False
        events.append('publish-child')
        os.rename(source, target)
    monkeypatch.setattr(ops, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(ops, 'os', SimpleNamespace(geteuid=lambda: 0,
        path=os.path, O_RDONLY=0, O_DIRECTORY=1, O_NOFOLLOW=2,
        open=lambda *a: fd, close=lambda opened: events.append('close'),
        fchown=chown, fchmod=chmod, listdir=entries, fsync=lambda opened: events.append('fsync'),
        chown=lambda *a: None, rename=rename))
    monkeypatch.setattr(ops, 'identities', lambda *a: (SimpleNamespace(pw_name='fixture_owner'), None))
    monkeypatch.setattr(ops, 'reusable_empty_home', lambda *a, **k: True)
    monkeypatch.setattr(ops, 'acl', lambda path, entry: None)
    monkeypatch.setattr(ops.custody, 'protected_directory', lambda path: nullcontext())
    monkeypatch.setattr(ops.custody, '_verify_fd', verify)
    monkeypatch.setattr(ops.subprocess, 'run', remove_acl)
    def run():
        return ops.provision(state=state, gateway_uid=501, broker_uid=61000,
                             broker_gid=61000, reuse_empty_home=True)
    if fault:
        with pytest.raises((ValueError, OSError)): run()
        assert 'publish-child' not in events
        assert (retained['uid'], retained['gid'], retained['mode']) == (0, 0, 0o700)
        assert {path.name for path in state.iterdir()} == ({'unexpected'} if fault == 'concurrent-addition' else set())
        if fault == 'concurrent-addition': assert (state / 'unexpected').read_text() == 'preserve'
        if fault == 'remove-failure': assert 'verify-pinned' not in events
        if fault in ('remove-failure', 'verification-failure'): assert 'empty-check' not in events
    else:
        assert run() == state
        assert events[:5] == [('owner', 0, 0), ('mode', 0o700), 'remove-acl', 'verify-pinned', 'empty-check']
        assert retained == dict(uid=61000, gid=61000, mode=0o750, acl=False)
        assert {path.name for path in state.iterdir()} == {
            name.split('/')[0] for name in ops.PRIVATE + ops.PROJECTIONS + ops.STORAGE + ops.SUBMISSIONS}
    assert events[-1] == 'close'
    assert not list(tmp_path.glob('.pixel-ops-*'))
