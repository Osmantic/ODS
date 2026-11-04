"""Actual schema3 Source withdrawal and token-metadata preservation on Linux.

Uses real protected Source inventories and files. Apple/launchd metadata is the
shared OS driver. Core retirement is a serialized stopped owned-child witness,
not a physical native Core/launchd retirement qualification.
"""
# ruff: noqa: E402,F401,F811
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs metadata harness, not physical Darwin', allow_module_level=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_native_source_executor import executor, qualification_operation, proc
from test_native_source_generation import generation, SPECS
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_runtime_publication import built, source, package
from test_native_source_publication import material, publication, helper
from test_native_source_installer_withdrawal import files, digest
from pixel_connections import custody as c
import pixel_macos_custody as mac

spec = importlib.util.spec_from_file_location('source_retirement_serializer', ROOT / 'installers/macos/lib/pixel-native-uninstall.py')
retirement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retirement)


@pytest.fixture
def retirement_ready(executor, monkeypatch):
    n = executor
    record_parent = n.world / 'public-records'
    record_parent.mkdir(mode=0o755)
    monkeypatch.setattr(c, 'NATIVE_CONNECTION_RECORD', record_parent / 'portal-connections.json')
    previous = mac.protected_directory
    @contextmanager
    def directory(path, **kwargs):
        if Path(path) == record_parent:
            assert not kwargs
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                yield fd
            finally:
                os.close(fd)
        else:
            with previous(path, **kwargs) as fd:
                yield fd
    monkeypatch.setattr(mac, 'protected_directory', directory)
    token = c.NATIVE_STATE / 'private/google-token.json'
    token.write_bytes(b'{"token":"synthetic-fixture-only"}')
    token.chmod(0o600)
    info = token.stat()
    n.owners[(info.st_dev, info.st_ino)] = (n.identity['id'], n.identity['id'])
    n.token = token
    read = os.read
    def no_token_read(fd, count):
        if Path(os.readlink('/proc/self/fd/' + str(fd))) == token:
            raise AssertionError('product must not read the synthetic token body')
        return read(fd, count)
    monkeypatch.setattr(os, 'read', no_token_read)
    inventory = c.native_retirement_inventory(n.selected)
    doc = c._native_selected(n.selected, _publication=True)
    runtime = {'deploymentSha256': n.selected.deployment.checksum, 'custodySha256': doc['runtimeCustodySha256'],
        'bundleSha256': doc['bundleSha256'], 'sourceRef': inventory['binding']['sourceRef']}
    child = subprocess.Popen([sys.executable, '-B', '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
    try:
        _parent, second, usec = proc(child.pid)
    finally:
        child.stdin.close()
        child.wait(timeout=5)
    core = {'schema': 1, 'owner': doc['ownerUid'], 'boot': inventory['boot'].lower(),
        'hashes': {str(n.f.plist): digest(n.f.plist.read_bytes())},
        'trees': {'system/com.ods.fixture': [[child.pid, second, usec]]}, 'sandboxes': []}
    source = {'schemaVersion': 1, 'kind': 'ods-native-source-retirement-intent', 'nonce': 'a' * 32,
        'owner': {'uid': doc['ownerUid'], 'gid': doc['ownerGid'], 'name': n.f.bridge.owner.pw_name,
            'installDir': str(n.f.bridge.install)}, 'runtime': runtime, 'inventory': inventory}
    witness = retirement.source_retirement_witness(core, source)
    raw = c._native_encoded(witness)
    n.witness = n.metadata / 'retirement.json'
    n.witness.write_bytes(raw); n.witness.chmod(0o600)
    n.transaction = retirement.source_retirement_transaction(raw)
    n.intent = helper.retirement_withdrawal_intent(n.selected, transaction=n.transaction)
    return n


def test_retirement_uses_distinct_witness_and_retains_token_without_reading_or_rearming(retirement_ready):
    n = retirement_ready
    environment = (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino)
    token = n.token.stat()
    result = helper.withdraw_retirement_generation(n.selected, transaction=n.transaction, intent=n.intent)
    assert result['kind'] == 'ods-native-source-retirement-withdrawal'
    assert result['archive']['name'] == '.source-retirement-withdrawal-' + result['withdrawal']
    assert result['token'][:2] == [token.st_dev, token.st_ino]
    assert n.token.stat().st_ino == token.st_ino and n.token.stat().st_size == token.st_size
    assert (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino) == environment
    assert json.loads((c.NATIVE_STATE / 'custody.json').read_bytes())['schemaVersion'] == 1
    assert not list(n.units.iterdir()) and not (n.metadata / 'native-source-jobs').exists()
    assert not (n.metadata / 'runtime-upgrade.json').exists()
    assert helper.verify_retirement_withdrawal(n.selected, transaction=n.transaction, intent=n.intent, receipt=result) == result
    before = files(n)
    assert helper.withdraw_retirement_generation(n.selected, transaction=n.transaction, intent=n.intent) == result
    with pytest.raises(ValueError, match='purpose-invalid'):
        helper.withdraw_installer_generation(n.selected, transaction=n.transaction, intent=n.intent)
    assert files(n) == before


@pytest.mark.parametrize('change', ['witness', 'owner', 'intent'])
def test_retirement_changed_proof_never_becomes_upgrade_or_mutates_state(retirement_ready, change):
    n = retirement_ready
    intent = json.loads(json.dumps(n.intent))
    if change == 'witness':
        n.witness.write_bytes(n.witness.read_bytes() + b' ')
    elif change == 'owner':
        n.records['Users']['GeneratedUID'] = ['00000000-0000-0000-0000-000000000000']
    else:
        intent['kind'] = 'ods-native-source-installer-withdrawal-intent'
    before = files(n)
    with pytest.raises((ValueError, c.CustodyError)):
        helper.withdraw_retirement_generation(n.selected, transaction=n.transaction, intent=intent)
    assert files(n) == before


@pytest.mark.parametrize('where', ['gate', 'v1', 'complete'])
def test_retirement_lost_reply_keeps_original_purpose_and_archive(retirement_ready, monkeypatch, where):
    n = retirement_ready
    rename, save = os.rename, c._native_save_at
    hit = []
    def renamed(first, second, **kwargs):
        result = rename(first, second, **kwargs)
        if not hit and (where == 'gate' and second == 'custody.before.json' or where == 'v1' and first == '.v1-next'):
            hit.append(True)
            raise OSError('invented retirement reply loss')
        return result
    def saved(selection, directory, name, value, **kwargs):
        result = save(selection, directory, name, value, **kwargs)
        if not hit and where == 'complete' and name == 'source-environment-generation.json' and value['phase'] == 'withdrawn':
            hit.append(True)
            raise OSError('invented retirement reply loss')
        return result
    monkeypatch.setattr(os, 'rename', renamed)
    monkeypatch.setattr(c, '_native_save_at', saved)
    with pytest.raises(OSError, match='retirement reply loss'):
        helper.withdraw_retirement_generation(n.selected, transaction=n.transaction, intent=n.intent)
    frozen = json.loads((n.metadata / 'source-environment-generation.json').read_bytes())
    monkeypatch.setattr(os, 'rename', rename)
    monkeypatch.setattr(c, '_native_save_at', save)
    result = helper.withdraw_retirement_generation(n.selected, transaction=n.transaction, intent=n.intent)
    assert hit and result['withdrawal'] == frozen['generation']
    before = files(n)
    assert helper.verify_retirement_withdrawal(n.selected, transaction=n.transaction, intent=n.intent, receipt=result) == result
    assert helper.withdraw_retirement_generation(n.selected, transaction=n.transaction, intent=n.intent) == result
    assert files(n) == before
