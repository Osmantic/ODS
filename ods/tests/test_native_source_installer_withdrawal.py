"""Actual protected-copy Source closure and Root files; Darwin metadata simulated.

Journal candidate bytes are declarations only in withdrawal tests; no candidate
Core/service code is installed, no provider is used and no physical Mac is claimed.
"""
# ruff: noqa: E402,F401,F811
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs metadata harness, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_executor import executor, qualification_operation
from test_native_source_generation import generation, SPECS
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_runtime_publication import built, source, package
from test_native_source_publication import material, publication, helper
from pixel_connections import custody as c


def digest(body):
    return hashlib.sha256(body).hexdigest()


@pytest.fixture
def prepared(executor, monkeypatch):
    return make_prepared(executor, monkeypatch)


def make_prepared(n, monkeypatch, *, runtime_bundle=None, candidate_digest=None, source_after=None, extra_after=()):
    monkeypatch.setattr(n.f.bridge, 'state', n.metadata, raising=False)
    monkeypatch.setattr(n.f.bridge.owner, 'pw_dir', str(n.f.bridge.home), raising=False)
    doc = c._native_selected(n.selected, _publication=True)
    candidate = candidate_digest or ('a' * 64 if doc['bundleSha256'] != 'a' * 64 else 'b' * 64)
    paths = c.native_installer_source_family()
    rows = []
    for path in sorted(paths):
        body = source_after[path] if source_after is not None else ('declared candidate Source file: ' + path).encode()
        mode = 0o600 if path.endswith('/source-runtime-installation.json') else 0o644
        before = Path(path).read_bytes() if Path(path).is_file() else None
        rows.append({'path': path, 'mode': mode, 'gid': 0, 'before': None if before is None else base64.b64encode(before).decode(),
            'beforeSha256': None if before is None else digest(before), 'after': base64.b64encode(body).decode(), 'afterSha256': digest(body)})
    family = digest(json.dumps([[row['path'], row['mode'], row['gid'], row['afterSha256']] for row in rows],
        separators=(',', ':'), ensure_ascii=True).encode())
    for path, body, mode in extra_after:
        before = Path(path).read_bytes()
        rows.append({'path': str(path), 'mode': mode, 'gid': 0, 'before': base64.b64encode(before).decode(),
            'beforeSha256': digest(before), 'after': base64.b64encode(body).decode(), 'afterSha256': digest(body)})
    owner = n.f.bridge.owner
    context = {'schemaVersion': 2, 'owner': {'uid': owner.pw_uid, 'gid': owner.pw_gid,
        'name': owner.pw_name, 'home': str(n.f.bridge.home)},
        'runtime_bundle': {'digest': candidate, **(runtime_bundle or {})},
        'upgrade_qualification': {'currentDigest': doc['bundleSha256'], 'candidateDigest': candidate},
        'source_lifecycle': {'schemaVersion': 1, 'kind': 'ods-native-source-installer-intent',
            'mode': 'withdrawal', 'nonce': '1' * 32, 'currentDigest': doc['bundleSha256'], 'candidateDigest': candidate,
            'ownerUid': owner.pw_uid, 'ownerGid': owner.pw_gid, 'familySha256': family}}
    body = c._native_encoded(context)
    n.transaction = {'kind': 'native-installer-upgrade', 'transaction': digest(body),
        'currentDigest': doc['bundleSha256'], 'candidateDigest': candidate}
    n.context = n.metadata / ('runtime-upgrade-context-' + candidate + '.json')
    n.context.write_bytes(body)
    n.context.chmod(0o600)
    n.intent = helper.installer_withdrawal_intent(n.selected, transaction=n.transaction)
    value = {'schemaVersion': 4, 'currentDigest': doc['bundleSha256'], 'candidateDigest': candidate,
        'phase': 'prepared', 'files': rows,
        'sourceAddition': {'schemaVersion': 1, 'kind': 'ods-native-source-installer-addition',
            'contextSha256': n.transaction['transaction'], 'familySha256': family, 'proof': n.intent,
            'absentPaths': sorted(row['path'] for row in rows if row['before'] is None)}}
    n.upgrade = n.metadata / 'runtime-upgrade.json'
    n.upgrade.write_bytes(c._native_encoded(value))
    n.upgrade.chmod(0o600)
    return n


def files(n):
    return {str(path.relative_to(n.world)): path.read_bytes()
        for path in n.world.rglob('*') if path.is_file() and not path.is_symlink()}


def test_actual_journal_withdrawal_ends_at_complete_v1_without_env_or_actor_replay(prepared):
    n = prepared
    env = (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino)
    old = {path.name: (path.read_bytes(), path.stat().st_ino) for path in n.units.iterdir()}
    result = helper.withdraw_installer_generation(n.selected, transaction=n.transaction, intent=n.intent)
    c.native_installer_withdrawal_receipt(result, intent=n.intent, transaction=n.transaction,
        owner_uid=n.f.bridge.owner.pw_uid, owner_gid=n.f.bridge.owner.pw_gid)
    assert (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino) == env
    assert json.loads((c.NATIVE_STATE / 'custody.json').read_bytes())['schemaVersion'] == 1
    assert not list(n.units.iterdir()) and not (n.metadata / 'native-source-jobs').exists()
    archive = n.metadata / result['archive']['name']
    for name, image in old.items():
        assert ((archive / name).read_bytes(), (archive / name).stat().st_ino) == image
    assert digest((archive / 'withdrawal-receipt.json').read_bytes()) == result['archive']['receiptSha256']
    assert result['runtime'] == n.intent['runtime'] and result['environment'] == n.intent['environment']
    before = files(n)
    assert helper.withdraw_installer_generation(n.selected, transaction=n.transaction, intent=n.intent) == result
    assert files(n) == before
    with pytest.raises(ValueError, match='installer-withdrawal-pending'):
        helper.publish_environment_generation(n.selected, c.NATIVE_ENV.read_text())
    assert files(n) == before


@pytest.mark.parametrize('change', ['context', 'journal', 'intent', 'owner'])
def test_withdrawal_never_accepts_caller_only_or_changed_actual_authority(prepared, change):
    n = prepared
    intent = json.loads(json.dumps(n.intent))
    if change == 'context':
        n.context.write_bytes(n.context.read_bytes() + b' ')
    elif change == 'journal':
        value = json.loads(n.upgrade.read_bytes())
        value['sourceAddition']['familySha256'] = 'f' * 64
        n.upgrade.write_bytes(c._native_encoded(value))
    elif change == 'intent':
        intent['gate']['sha256'] = 'f' * 64
    else:
        intent['owner']['uid'] += 1
    before = files(n)
    with pytest.raises((ValueError, c.CustodyError)):
        helper.withdraw_installer_generation(n.selected, transaction=n.transaction, intent=intent)
    assert files(n) == before


@pytest.mark.parametrize('where', ['gate', 'v1', 'receipt-inode', 'complete-reply'])
def test_withdrawal_lost_replies_resume_original_archive_and_receipt(prepared, monkeypatch, where):
    n = prepared
    original_save, original_rename = c._native_save_at, os.rename
    hit = []
    def saved(selection, directory, name, value, **kwargs):
        result = original_save(selection, directory, name, value, **kwargs)
        data = value.get('installer')
        chosen = (name == 'source-environment-generation.json' and data is not None and (
            where == 'receipt-inode' and data['receipt']['inode'] is not None and not data['receipt']['ready']
            or where == 'complete-reply' and value['phase'] == 'withdrawn'))
        if chosen and not hit:
            hit.append(True)
            raise OSError('invented withdrawal reply loss')
        return result
    def rename(first, second, **kwargs):
        result = original_rename(first, second, **kwargs)
        if not hit and (where == 'gate' and second == 'custody.before.json' or where == 'v1' and first == '.v1-next'):
            hit.append(True)
            raise OSError('invented withdrawal reply loss')
        return result
    monkeypatch.setattr(c, '_native_save_at', saved)
    monkeypatch.setattr(os, 'rename', rename)
    with pytest.raises(OSError, match='withdrawal reply loss'):
        helper.withdraw_installer_generation(n.selected, transaction=n.transaction, intent=n.intent)
    frozen = json.loads((n.metadata / 'source-environment-generation.json').read_bytes())
    monkeypatch.setattr(c, '_native_save_at', original_save)
    monkeypatch.setattr(os, 'rename', original_rename)
    result = helper.withdraw_installer_generation(n.selected, transaction=n.transaction, intent=n.intent)
    assert hit and result['withdrawal'] == frozen['generation']
    before = files(n)
    assert helper.withdraw_installer_generation(n.selected, transaction=n.transaction, intent=n.intent) == result
    assert files(n) == before
