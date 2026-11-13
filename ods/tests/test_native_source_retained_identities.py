"""Real owned retained files; Directory Service/ownership and Source API are isolated boundaries."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
from pixel_connections import custody as source_custody

SPEC = importlib.util.spec_from_file_location('source_retained_ops',
    ROOT / 'installers/macos/lib/pixel-native-ops-account.py')
account = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(account)


@pytest.fixture
def retained(tmp_path, monkeypatch):
    state = tmp_path / 'retained'
    state.mkdir(mode=0o700)
    install = tmp_path / 'ods'
    install.mkdir(mode=0o700)
    source = dict(schema=1, name='_ods_pixel_sources', id=62000,
        userGuid='11111111-1111-4111-8111-111111111111',
        groupGuid='22222222-2222-4222-8222-222222222222')
    ops = dict(source, name=account.NAME, id=61000,
        userGuid='33333333-3333-4333-8333-333333333333',
        groupGuid='44444444-4444-4444-8444-444444444444')
    for name, value in [('ops-identity.json', ops), ('ops-identity.lock', None), ('source-identity.json', source)]:
        file = state / name
        file.write_text(json.dumps(value) if value is not None else '')
        file.chmod(0o600)
    owner = SimpleNamespace(pw_name='fixture-owner', pw_uid=install.stat().st_uid,
        pw_gid=install.stat().st_gid, pw_dir=str(tmp_path))
    assert owner.pw_uid != 0, 'this mechanical fixture is an unprivileged owned namespace'
    monkeypatch.setattr(account, 'ROOT', state)
    monkeypatch.setattr(source_custody, 'NATIVE_META', state)
    monkeypatch.setattr(account.sys, 'platform', 'darwin')
    monkeypatch.setattr(account.os, 'geteuid', lambda: 0)
    @contextmanager
    def directory(path):
        assert Path(path) == state
        fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: yield fd
        finally: os.close(fd)
    monkeypatch.setattr(account.custody, 'protected_directory', directory)
    monkeypatch.setattr(account.custody, '_verify_fd', lambda fd, **_kw: os.fstat(fd))
    monkeypatch.setattr(account.custody, 'protected_bytes', lambda path, **_kw: Path(path).read_bytes())
    monkeypatch.setattr(account, 'read_record', lambda kind: {
        key: [value] for key, value in account.expected_attributes(ops, kind).items()})
    def user(name):
        if name == account.NAME: return SimpleNamespace(pw_uid=ops['id'], pw_gid=ops['id'])
        if name == owner.pw_name: return owner
        raise KeyError(name)
    monkeypatch.setattr(account.pwd, 'getpwnam', user)
    monkeypatch.setattr(account.pwd, 'getpwall', lambda: [])
    monkeypatch.setattr(account.grp, 'getgrnam', lambda _name: SimpleNamespace(gr_gid=ops['id']))
    monkeypatch.setattr(account.grp, 'getgrall', lambda: [])
    commands, source_calls = [], []
    def run(argv, **_kw):
        commands.append(argv)
        return SimpleNamespace(returncode=0, stdout='') if argv[0] == '/bin/ps' else SimpleNamespace(returncode=113)
    monkeypatch.setattr(account.subprocess, 'run', run)
    def validated_source(*, owner_uid, owner_gid):
        source_calls.append((owner_uid, owner_gid))
        assert (owner_uid, owner_gid) == (owner.pw_uid, owner.pw_gid)
        path = state / 'source-identity.json'
        info, raw = path.stat(), path.read_bytes()
        if info.st_nlink != 1 or info.st_mode & 0o777 != 0o600:
            raise source_custody.CustodyError('changed fixture identity')
        return dict(schemaVersion=1, kind='ods-native-source-retained-identity',
            record=json.loads(raw), image=dict(inode=[info.st_dev, info.st_ino], sha256=hashlib.sha256(raw).hexdigest()),
            witness=dict(boot='55555555-5555-4555-8555-555555555555', loadedSourceJobs=0, brokerProcesses=0, brokerUid=62000))
    monkeypatch.setattr(source_custody, 'native_retained_identity_only', validated_source)
    return SimpleNamespace(state=state, install=install, source=source, ops=ops,
        owner=owner, commands=commands, source_calls=source_calls, run=run)


def images(state):
    return {path.name: (path.stat().st_dev, path.stat().st_ino, path.read_bytes()) for path in state.iterdir()}


def test_exact_retired_source_and_ops_identities_reinstall_only_after_both_proofs(retained):
    n = retained
    before = images(n.state)
    with pytest.raises(ValueError, match='not-only-protected-state'):
        account.verify_identity_only()  # unchanged default still refuses a third file
    result = account.verify_identity_only(source_owner=n.owner.pw_name, source_install_dir=n.install)
    assert result == dict(name=account.NAME, uid=n.ops['id'], gid=n.ops['id'])
    assert n.source_calls == [(n.owner.pw_uid, n.owner.pw_gid)] * 2
    assert images(n.state) == before


@pytest.mark.parametrize('fault', ['wrong-owner', 'wrong-install', 'extra-node', 'source-uuid-rejected', 'source-drift'])
def test_retained_identity_union_cannot_adopt_wrong_owner_partial_or_changed_state(retained, monkeypatch, fault):
    n = retained
    owner_name, install_dir = n.owner.pw_name, n.install
    if fault == 'wrong-owner': owner_name = 'unknown-owner'
    elif fault == 'wrong-install': install_dir = Path(n.owner.pw_dir)
    elif fault == 'extra-node': (n.state / 'source-environment.json').write_text('preserve private residue')
    elif fault == 'source-uuid-rejected':
        monkeypatch.setattr(source_custody, 'native_retained_identity_only',
            lambda **_kw: (_ for _ in ()).throw(source_custody.CustodyError('UUID mismatch must not be repaired')))
    elif fault == 'source-drift':
        def run(argv, **kwargs):
            if argv == ['/bin/launchctl', 'print', 'system/' + account.SYSTEM_JOBS[-1]]:
                (n.state / 'source-identity.json').write_text(json.dumps(dict(n.source, userGuid='66666666-6666-4666-8666-666666666666')))
            return n.run(argv, **kwargs)
        monkeypatch.setattr(account.subprocess, 'run', run)
    before = images(n.state)
    with pytest.raises(ValueError):
        account.verify_identity_only(source_owner=owner_name, source_install_dir=install_dir)
    if fault != 'source-drift': assert images(n.state) == before
    else:
        assert (n.state / 'source-identity.json').stat().st_ino == before['source-identity.json'][1]
        assert images(n.state)['ops-identity.json'] == before['ops-identity.json']


def test_source_install_ancestor_is_bound_without_creating_a_fresh_target(retained):
    n = retained
    future = n.install / 'new-ods'
    account.verify_identity_only(source_owner=n.owner.pw_name, source_install_dir=future)
    assert not future.exists()


@pytest.mark.parametrize('argv', [
    ['--source-owner', 'owner'], ['--source-install-dir', '/Users/owner/ods'],
    ['--source-owner', 'owner', '--source-install-dir', '/Users/owner/ods'],
])
def test_source_identity_options_cannot_provision_or_supply_partial_authority(argv, monkeypatch):
    monkeypatch.setattr(account, 'provision', lambda: pytest.fail('invalid Source mode cannot provision'))
    with pytest.raises(SystemExit): account.main(argv)
