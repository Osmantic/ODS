import copy
from contextlib import contextmanager
import importlib.util
import json
import plistlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('ops_account',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-ops-account.py')
account = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(account)


@pytest.fixture
def fixture(monkeypatch):
    intent = {'schema': 1, 'name': account.NAME, 'id': 61000,
              'userGuid': '11111111-1111-4111-8111-111111111111',
              'groupGuid': '22222222-2222-4222-8222-222222222222'}
    records, writes = {}, []
    monkeypatch.setattr(account, 'read_record', lambda kind: copy.deepcopy(records.get(kind)))
    def create(kind, key, value):
        writes.append((kind, key, value))
        records.setdefault(kind, {})[key] = [value]
    monkeypatch.setattr(account, 'create_attribute', create)
    monkeypatch.setattr(account.pwd, 'getpwall', lambda: [])
    monkeypatch.setattr(account.grp, 'getgrall', lambda: [])
    return intent, records, writes


def test_create_and_replay(fixture):
    intent, records, writes = fixture
    assert account.reconcile(intent)['uid'] == 61000
    assert writes[-1][:2] == ('Users', 'UniqueID')
    assert records['Users']['UserShell'] == ['/usr/bin/false']
    assert records['Users']['Password'] == ['*']
    writes.clear()
    account.reconcile(intent)
    assert not writes


@pytest.mark.parametrize('completed', range(1, 10))
def test_resume_every_attribute_boundary(fixture, completed):
    intent, records, writes = fixture
    account.reconcile(intent)
    original = list(writes)
    records.clear()
    for kind, key, value in original[:completed]: records.setdefault(kind, {})[key] = [value]
    writes.clear()
    account.reconcile(intent)
    assert writes == original[completed:]


@pytest.mark.parametrize('fault', ['uuid', 'uid', 'shell', 'password', 'auth', 'members', 'missing-uuid'])
def test_refuse_changed_records_without_writes(fixture, fault):
    intent, records, writes = fixture
    account.reconcile(intent)
    if fault == 'uuid': records['Users']['GeneratedUID'] = [intent['groupGuid']]
    if fault == 'uid': records['Users']['UniqueID'] = ['501']
    if fault == 'shell': records['Users']['UserShell'] = ['/bin/zsh']
    if fault == 'password': records['Users']['Password'] = ['not-disabled']
    if fault == 'auth': records['Users']['AuthenticationAuthority'] = [';ShadowHash;']
    if fault == 'members': records['Groups']['GroupMembership'] = ['someone']
    if fault == 'missing-uuid': del records['Users']['GeneratedUID']
    writes.clear()
    with pytest.raises(ValueError): account.reconcile(intent)
    assert not writes


@pytest.mark.parametrize('kind', ['user', 'group', 'membership'])
def test_id_collisions_and_other_group_membership(fixture, monkeypatch, kind):
    intent, records, writes = fixture
    if kind == 'user':
        monkeypatch.setattr(account.pwd, 'getpwall', lambda: [SimpleNamespace(pw_uid=61000, pw_name='other')])
    else:
        monkeypatch.setattr(account.grp, 'getgrall', lambda: [SimpleNamespace(
            gr_gid=61000 if kind == 'group' else 80, gr_name='other',
            gr_mem=[] if kind == 'group' else [account.NAME])])
    with pytest.raises(ValueError): account.reconcile(intent)
    assert not writes


def test_directory_errors_are_not_absence(monkeypatch):
    monkeypatch.setattr(account, 'dscl', lambda *args: SimpleNamespace(returncode=1, stdout=b'', stderr=b'connection failed'))
    with pytest.raises(ValueError, match='directory-read-failed'): account.read_record('Users')


def test_missing_record(monkeypatch):
    monkeypatch.setattr(account, 'dscl', lambda *args: SimpleNamespace(returncode=185, stdout=b'', stderr=b'eDSRecordNotFound'))
    assert account.read_record('Users') is None


def test_native_hidden_attribute_is_normalized(monkeypatch):
    body = plistlib.dumps({'dsAttrTypeNative:IsHidden': ['1'],
                          'dsAttrTypeStandard:UniqueID': ['61000']})
    monkeypatch.setattr(account, 'dscl', lambda *args: SimpleNamespace(returncode=0, stdout=body, stderr=b''))
    assert account.read_record('Users') == {'IsHidden': ['1'], 'UniqueID': ['61000']}


def test_ambiguous_hidden_attribute_refused(monkeypatch):
    body = plistlib.dumps({'dsAttrTypeNative:IsHidden': ['1'], 'dsAttrTypeStandard:IsHidden': ['0']})
    monkeypatch.setattr(account, 'dscl', lambda *args: SimpleNamespace(returncode=0, stdout=body, stderr=b''))
    with pytest.raises(ValueError): account.read_record('Users')


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin directory-fd semantics')
@pytest.mark.parametrize('fault', [None, 'extra-state', 'receipt-mode', 'foreign-user',
    'active-process', 'loaded-service', 'unexpected-membership'])
def test_retained_identity_only_proof_is_read_only_and_fail_closed(
        tmp_path, monkeypatch, fixture, fault):
    intent, records, writes = fixture
    identity = tmp_path / 'identity'
    identity.mkdir(mode=0o700)
    identity.chmod(0o700)
    for name in ('ops-identity.json', 'ops-identity.lock'):
        path = identity / name
        path.write_text(json.dumps(intent) if name.endswith('.json') else '')
        path.chmod(0o600)
    if fault == 'extra-state': (identity / 'active.json').write_text('keep')
    if fault == 'receipt-mode': (identity / 'ops-identity.json').chmod(0o644)
    monkeypatch.setattr(account, 'ROOT', identity)
    monkeypatch.setattr(account.os, 'geteuid', lambda: 0)

    @contextmanager
    def directory(path):
        assert path == identity
        fd = os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: yield fd
        finally: os.close(fd)

    monkeypatch.setattr(account.custody, 'protected_directory', directory)
    monkeypatch.setattr(account.custody, '_verify_fd', lambda fd, **kwargs: os.fstat(fd))
    monkeypatch.setattr(account.custody, 'protected_bytes',
        lambda path, **kwargs: Path(path).read_bytes())
    monkeypatch.setattr(account, 'read_record', lambda kind: {
        key: [value] for key, value in account.expected_attributes(intent, kind).items()})
    monkeypatch.setattr(account.pwd, 'getpwnam', lambda name: SimpleNamespace(
        pw_uid=intent['id'] + (1 if fault == 'foreign-user' else 0), pw_gid=intent['id']))
    monkeypatch.setattr(account.grp, 'getgrnam', lambda name: SimpleNamespace(gr_gid=intent['id']))
    monkeypatch.setattr(account.grp, 'getgrall', lambda: [SimpleNamespace(
        gr_gid=80, gr_name='other', gr_mem=[account.NAME] if fault == 'unexpected-membership' else [])])
    commands = []
    def run(argv, **kwargs):
        commands.append(argv)
        if argv[:2] == ['/bin/ps', '-axo']:
            return SimpleNamespace(stdout=str(intent['id']) if fault == 'active-process' else '',
                returncode=0)
        assert argv[:2] == ['/bin/launchctl', 'print']
        return SimpleNamespace(returncode=0 if fault == 'loaded-service' else 113)
    monkeypatch.setattr(account.subprocess, 'run', run)
    before = {path.name: (path.read_bytes(), path.stat().st_mode)
        for path in identity.iterdir()}
    if fault:
        with pytest.raises(ValueError): account.verify_identity_only()
    else:
        assert account.verify_identity_only() == {
            'name': account.NAME, 'uid': intent['id'], 'gid': intent['id']}
        assert len(commands) == 1 + len(account.SYSTEM_JOBS)
    assert not writes
    assert before == {path.name: (path.read_bytes(), path.stat().st_mode)
        for path in identity.iterdir()}


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin directory-fd semantics')
@pytest.mark.parametrize('fault', [None, 'content', 'mode', 'symlink'])
def test_empty_home_proof_requires_exact_empty_identity_home(tmp_path, monkeypatch, fault):
    home = tmp_path / 'ops-home'
    home.mkdir(mode=0o750)
    home.chmod(0o700 if fault == 'mode' else 0o750)
    home_info = home.stat()
    if fault == 'content': (home / 'old-state').write_text('keep')
    if fault == 'symlink':
        home.rename(tmp_path / 'real-home')
        home.symlink_to(tmp_path / 'real-home', target_is_directory=True)
    monkeypatch.setattr(account, 'HOME', str(home))
    monkeypatch.setattr(account, 'verify_identity_only', lambda: {
        'name': account.NAME, 'uid': home_info.st_uid, 'gid': home_info.st_gid})
    if fault:
        with pytest.raises((ValueError, OSError)):
            account.verify_empty_home_only()
    else:
        assert account.verify_empty_home_only()['uid'] == home_info.st_uid


@pytest.mark.skipif(sys.platform != 'darwin' or os.geteuid() != 0 or
    os.environ.get('ODS_TEST_OPS_ACCOUNT_LIVE') != '1', reason='explicit dedicated account provisioning')
def test_real_account_provision_and_idempotent_replay():
    first = account.provision()
    before = {kind: account.read_record(kind) for kind in ('Users', 'Groups')}
    assert account.provision() == first
    assert before == {kind: account.read_record(kind) for kind in ('Users', 'Groups')}
    assert first['uid'] != int(os.environ['SUDO_UID'])
    assert account.pwd.getpwnam(account.NAME).pw_uid == first['uid']
    gateway = account.pwd.getpwuid(int(os.environ['SUDO_UID']))
    assert first['gid'] not in os.getgrouplist(gateway.pw_name, gateway.pw_gid)
