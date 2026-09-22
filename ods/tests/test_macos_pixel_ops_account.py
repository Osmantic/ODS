import copy
import importlib.util
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
