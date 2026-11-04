"""Actual two-bundle installer metadata rollover; no service/provider runs."""
# ruff: noqa: E402,F401,F811
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import plistlib
import sys
from types import SimpleNamespace

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux metadata driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_installer_first_kit import (first_target, material, native, deployment, built,
    source, package, runtime_custody, bundle, services, helper, qualification_operation, digest)
from test_native_source_installer_code_only import finish_source_caller, finish_and_first_prepare
from test_native_source_installer_withdrawal import files
from test_native_source_installer_withdrawal import make_prepared
from test_native_source_installer_rebind import connected_upgraded
from test_native_source_backend import backend
from test_native_source_executor import executor, generation, publication
from test_macos_pixel_access_install import installer
from pixel_connections import custody as c
from pixel_access_bridge import AccessError
import pixel_macos_custody as mac


@pytest.fixture
def second_prepared(first_target, monkeypatch):
    n = first_target
    # All old immutable components retain their actual inventory/digest proof.
    # Only the Apple UID/GID observation of these relocated QA files differs.
    actual_loader = c._native_installer_old_publisher
    def load(authority, **kwargs):
        module = actual_loader(authority, **kwargs)
        descriptor = json.loads(next(item['before'] for item in authority['files']
            if item['path'] == str(c.NATIVE_RUNTIME_DESCRIPTOR)))
        component = Path(descriptor['runtimeRoot']).parent / 'native-source'
        original_read = module._read_at
        def read(directory, name, **kwargs):
            if Path(os.readlink('/proc/self/fd/' + str(directory))) == component:
                kwargs.update(uid=os.getuid(), gid=os.getgid())
            return original_read(directory, name, **kwargs)
        monkeypatch.setattr(module, '_read_at', read)
        return module
    monkeypatch.setattr(c, '_native_installer_old_publisher', load)
    first_journal = json.loads(n.upgrade.read_bytes())
    first_context = json.loads(n.context.read_bytes())
    finish_source_caller(n, monkeypatch)
    journal = installer._upgrade.RecoveryJournal(n.upgrade, json.loads(n.upgrade.read_bytes()))
    journal.finish(lambda: helper.verify_installer_code_only_kit(n.selected,
        transaction=n.transaction, proof=n.absence))
    assert not n.upgrade.exists()
    fixed = n.metadata / 'source-installer-code-only.json'
    n.original_record = (fixed.read_bytes(), fixed.stat().st_ino)
    n.original_transaction = dict(n.transaction)
    n.original_kit = (n.kit.stat().st_dev, n.kit.stat().st_ino)
    n.original_owner = {path: (path.read_bytes(), path.stat().st_ino) for path in (n.f.env, n.f.answers)}
    # The original canonical bundle is a distinct, explicitly selected second
    # target. No dummy executable, manifest or Source proof is synthesized.
    current, candidate = n.transaction['candidateDigest'], n.transaction['currentDigest']
    target = n.f.root.parent / candidate
    assert bundle.verify(target, expected_digest=candidate)[1] == candidate
    capture = c.native_installer_sidefiles_snapshot(n.selected)
    config = n.f.config.with_name('openclaw-' + candidate + '.json')
    config.write_bytes(n.f.config.read_bytes())
    config.chmod(0o600)
    previous = {item['path']: item for item in first_journal['files']}
    descriptor = json.loads(base64.b64decode(previous[str(c.NATIVE_RUNTIME_DESCRIPTOR)]['before']))
    manifest = base64.b64decode(previous[str(c.NATIVE_RUNTIME_MANIFEST)]['before'])
    rows = []
    for path in sorted(c.native_installer_source_family()):
        node = Path(path)
        if node == c.NATIVE_RUNTIME_DESCRIPTOR:
            after = c._native_encoded(descriptor)
        elif node == c.NATIVE_RUNTIME_MANIFEST:
            after = manifest
        elif node == n.metadata / 'source-runtime-installation.json':
            after = c._native_encoded(dict(schemaVersion=1, kind='ods-native-source-runtime-publication',
                bundleSha256=candidate, sourceRef=n.built.package[1]))
        else:
            after = (target / 'ods-source/bin' / node.relative_to(c.NATIVE_PROGRAM)).read_bytes()
        rows.append(dict(path=path, mode=0o600 if node.name == 'source-runtime-installation.json' else 0o644,
            gid=0, before=node.read_bytes(), after=after))
    launcher = Path(descriptor['launcher'])
    original_plist = plistlib.loads(base64.b64decode(previous[str(n.f.plist)]['before']))
    old_config = first_context['runtime_bundle']['source_sidefiles_before']['config']['path']
    original_plist['ProgramArguments'] = [arg.replace(old_config, str(config)) for arg in original_plist['ProgramArguments']]
    rows.extend((dict(path=str(launcher), mode=0o755, gid=0, before=launcher.read_bytes(),
        after=base64.b64decode(previous[str(launcher)]['before'])),
        dict(path=str(n.f.plist), mode=0o644, gid=0, before=n.f.plist.read_bytes(), after=plistlib.dumps(original_plist))))
    # The entire closed Source family is relocated as one mechanical Linux
    # namespace. Its codec and all actual file images remain production code.
    monkeypatch.setattr(installer._upgrade, 'SOURCE_ADDITIONS', c.native_installer_source_family())
    monkeypatch.setattr(installer._upgrade, 'SOURCE_PRIVATE_RECEIPT', str(n.metadata / 'source-runtime-installation.json'))
    family = installer._upgrade.source_family_hash(rows)
    context = dict(first_context, runtime_bundle=dict(digest=candidate, config_path=str(config),
        connection_sidefiles=capture['sidefiles'], source_sidefiles_before=capture['snapshot']),
        upgrade_qualification=dict(currentDigest=current, candidateDigest=candidate),
        source_lifecycle=dict(first_context['source_lifecycle'], nonce='2' * 32, currentDigest=current,
            candidateDigest=candidate, familySha256=family))
    raw = c._native_encoded(context)
    n.transaction = dict(kind='native-installer-upgrade', transaction=digest(raw), currentDigest=current, candidateDigest=candidate)
    n.context = n.metadata / ('runtime-upgrade-context-' + candidate + '.json')
    n.context.write_bytes(raw)
    n.context.chmod(0o600)
    n.absence = c.native_absent_for_installer(n.f.bridge, transaction=n.transaction)
    addition = dict(schemaVersion=1, kind='ods-native-source-installer-addition', contextSha256=n.transaction['transaction'],
        familySha256=family, proof=n.absence, absentPaths=[])
    value = installer._upgrade.encode_recovery(rows, current_digest=current, candidate_digest=candidate,
        allowed_paths={item['path'] for item in rows}, source_addition=addition)
    journal = installer._upgrade.RecoveryJournal.create(n.upgrade, value)
    n.second_rows, n.second_config, n.second_target, n.second_journal = rows, config, target, journal
    return n


def rollover(n):
    # Actual prepared caller, including capability selection and closed reply.
    installer._rollover_source_installer_history(n.f.bridge, n.transaction)
    return json.loads((n.metadata / 'source-installer-history.json').read_bytes())


@pytest.mark.parametrize('first_target', ['code-only-history'], indirect=True)
def test_second_prepared_installer_rollover_preserves_exact_original_record(second_prepared):
    n = second_prepared
    before = n.upgrade.read_bytes()
    ledger = rollover(n)
    entry = ledger['entries'][-1]
    artifact = entry['artifacts']['source-installer-code-only.json']
    assert entry['phase'] == 'committed' and entry['transaction'] == n.transaction
    assert not (n.metadata / 'source-installer-code-only.json').exists()
    retained = n.metadata / artifact['name']
    assert (retained.read_bytes(), retained.stat().st_ino) == n.original_record
    assert n.upgrade.read_bytes() == before and (n.kit.stat().st_dev, n.kit.stat().st_ino) == n.original_kit
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in n.original_owner} == n.original_owner
    snapshot = files(n)
    assert helper.rollover_installer_history(n.selected, transaction=n.transaction) == dict(status='retained', transaction=n.transaction)
    assert files(n) == snapshot and not c.NATIVE_STATE.exists()


@pytest.mark.parametrize('first_target', ['code-only-history'], indirect=True)
@pytest.mark.parametrize('where', ['intent', 'rename', 'committed'])
def test_second_installer_rollover_lost_ack_keeps_one_frozen_inode(second_prepared, monkeypatch, where):
    n = second_prepared
    original_save, original_rename, hits = c._native_save_at, os.rename, []
    def save(selection, directory, name, value, **kwargs):
        result = original_save(selection, directory, name, value, **kwargs)
        if name == 'source-installer-history.json' and not hits and value['entries'][-1]['phase'] == ('pending' if where == 'intent' else 'committed') and where != 'rename':
            hits.append(True)
            raise OSError('invented rollover reply loss')
        return result
    def renamed(first, second, **kwargs):
        result = original_rename(first, second, **kwargs)
        if where == 'rename' and first == 'source-installer-code-only.json' and not hits:
            hits.append(True)
            raise OSError('invented rollover reply loss')
        return result
    monkeypatch.setattr(c, '_native_save_at', save)
    monkeypatch.setattr(os, 'rename', renamed)
    with pytest.raises(OSError, match='rollover reply loss'):
        rollover(n)
    monkeypatch.setattr(c, '_native_save_at', original_save)
    monkeypatch.setattr(os, 'rename', original_rename)
    ledger = rollover(n)
    image = ledger['entries'][-1]['artifacts']['source-installer-code-only.json']
    target = n.metadata / image['name']
    assert hits and len(ledger['entries']) == 1 and ledger['entries'][-1]['phase'] == 'committed'
    assert (target.read_bytes(), target.stat().st_ino) == n.original_record


@pytest.mark.parametrize('first_target', ['code-only-history'], indirect=True)
@pytest.mark.parametrize('fault', ['context', 'old-publication', 'early-code', 'active-phase'])
def test_second_installer_rollover_unknown_origin_preserves_current_record(second_prepared, fault):
    n = second_prepared
    if fault == 'context':
        n.context.write_bytes(n.context.read_bytes() + b' ')
    elif fault == 'old-publication':
        path = n.metadata / 'source-publication.json'
        value = json.loads(path.read_bytes())
        value['nonce'] = 'e' * 32
        path.write_bytes(c._native_encoded(value))
    elif fault == 'early-code':
        path = c.NATIVE_RUNTIME_DESCRIPTOR
        path.write_bytes(c._native_encoded(json.loads(next(item['after'] for item in n.second_rows if item['path'] == str(path)))))
    else:
        value = json.loads(n.upgrade.read_bytes())
        value['phase'] = 'active'
        n.upgrade.write_bytes(c._native_encoded(value))
    before = files(n)
    with pytest.raises((ValueError, c.CustodyError, installer.InstallError, AccessError)):
        rollover(n)
    assert files(n) == before and not (n.metadata / 'source-installer-history.json').exists()


@pytest.mark.parametrize('first_target', ['code-only-history'], indirect=True)
def test_two_actual_bundle_updates_then_first_prepare_preserve_all_previous_history(second_prepared, monkeypatch):
    n = second_prepared
    ledger = rollover(n)
    # Actual Journal4 mixed-image proof is checked before every Source write.
    n.second_journal.phase('replacing-files')
    for image in n.second_rows:
        c.native_verify_installer_absence(n.f.bridge, n.absence, transaction=n.transaction)
        Path(image['path']).write_bytes(image['after'])
        Path(image['path']).chmod(image['mode'])
    n.f.bridge._runtime_config_path = n.second_config
    n.f.bridge.gateway_process['executable'] = str(n.second_target / 'node')
    n.f.root, n.f.config = n.second_target, n.second_config
    n.f.value = n.f.bridge.connection_deployment()
    n.selected = c.native_source_selection(n.f.bridge, n.f.value)
    n.second_journal.phase('active')
    finish_and_first_prepare(n, monkeypatch)
    assert (n.metadata / 'source-installer-history.json').read_bytes() == c._native_encoded(ledger)
    original = n.metadata / ledger['entries'][-1]['artifacts']['source-installer-code-only.json']['name']
    assert (original.read_bytes(), original.stat().st_ino) == n.original_record
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in n.original_owner} == n.original_owner


def test_second_connected_installer_rebind_slot_rollover_and_withdrawal_preserve_token_metadata(connected_upgraded, monkeypatch):
    n = connected_upgraded
    # Reuse two independently verified canonical bundles, now with an actual
    # admitted connected owner and a broker token whose body must stay unread.
    loader = c._native_installer_old_publisher
    def load(authority, **kwargs):
        module = loader(authority, **kwargs)
        descriptor = json.loads(next(item['before'] for item in authority['files'] if item['path'] == str(c.NATIVE_RUNTIME_DESCRIPTOR)))
        component = Path(descriptor['runtimeRoot']).parent / 'native-source'
        read = module._read_at
        def observed(directory, name, **kwargs):
            if Path(os.readlink('/proc/self/fd/' + str(directory))) == component:
                kwargs.update(uid=os.getuid(), gid=os.getgid())
            return read(directory, name, **kwargs)
        monkeypatch.setattr(module, '_read_at', observed)
        return module
    monkeypatch.setattr(c, '_native_installer_old_publisher', load)
    first_journal, first_context = json.loads(n.upgrade.read_bytes()), json.loads(n.context.read_bytes())
    helper.rebind_installer_generation(n.selected, transaction=n.transaction, receipt=n.receipt)
    first = installer._upgrade.RecoveryJournal(n.upgrade, json.loads(n.upgrade.read_bytes()))
    first.finish(lambda: helper.verify_installer_rebind_generation(n.selected, transaction=n.transaction, receipt=n.receipt))
    original = n.metadata / 'source-installer-rebind.json'
    before = (original.read_bytes(), original.stat().st_ino)
    token = (n.connected_token.stat().st_dev, n.connected_token.stat().st_ino)
    environment = (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino)
    owner_files = {path: (path.read_bytes(), path.stat().st_ino) for path in (n.f.env, n.f.answers)}
    capture = c.native_installer_sidefiles_snapshot(n.selected)
    candidate = n.transaction['currentDigest']
    target = n.f.root.parent / candidate
    assert bundle.verify(target, expected_digest=candidate)[1] == candidate
    old = {item['path']: item for item in first_journal['files']}
    after = {}
    for path in c.native_installer_source_family():
        node = Path(path)
        if node in (c.NATIVE_RUNTIME_DESCRIPTOR, c.NATIVE_RUNTIME_MANIFEST):
            after[path] = base64.b64decode(old[path]['before'])
        elif node == n.metadata / 'source-runtime-installation.json':
            after[path] = c._native_encoded(dict(schemaVersion=1, kind='ods-native-source-runtime-publication',
                bundleSha256=candidate, sourceRef=n.built.package[1]))
        else:
            after[path] = (target / 'ods-source/bin' / node.relative_to(c.NATIVE_PROGRAM)).read_bytes()
    config = n.f.config.with_name('openclaw-' + candidate + '.json')
    config.write_bytes(n.f.config.read_bytes())
    config.chmod(0o600)
    plist = plistlib.loads(base64.b64decode(old[str(n.f.plist)]['before']))
    old_config = first_context['runtime_bundle']['source_sidefiles_before']['config']['path']
    plist['ProgramArguments'] = [item.replace(old_config, str(config)) for item in plist['ProgramArguments']]
    launcher = Path(json.loads(after[str(c.NATIVE_RUNTIME_DESCRIPTOR)])['launcher'])
    make_prepared(n, monkeypatch, candidate_digest=candidate, source_after=after,
        runtime_bundle=dict(config_path=str(config), connection_sidefiles=capture['sidefiles'], source_sidefiles_before=capture['snapshot']),
        extra_after=[(launcher, base64.b64decode(old[str(launcher)]['before']), 0o755), (n.f.plist, plistlib.dumps(plist), 0o644)])
    # Rollover additionally opens the complete fixed old Source family by FD
    # before any write. Relocate only those exact Root namespace observations;
    # all bytes, modes, inodes and CAS decisions remain actual.
    root_images = {Path(path) for path in c.native_installer_source_family()} | {launcher, n.f.plist}
    root_parents = {path.parent for path in root_images}
    directory_before, fstat_before, file_before = mac.protected_directory, os.fstat, c._native_root_file
    reading = []
    @contextmanager
    def directory(path, **kwargs):
        if Path(path) in root_parents:
            assert not kwargs
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                yield fd
            finally:
                os.close(fd)
        else:
            with directory_before(path, **kwargs) as fd:
                yield fd
    def root_file(fd, name, **kwargs):
        reading.append(True)
        try:
            return file_before(fd, name, **kwargs)
        finally:
            reading.pop()
    def fstat(fd):
        value = fstat_before(fd)
        try:
            path = Path(os.readlink('/proc/self/fd/' + str(fd)))
        except OSError:
            return value
        if not reading or path not in root_images:
            return value
        fields = {key: getattr(value, key) for key in dir(value) if key.startswith('st_')}
        fields.update(st_uid=0, st_gid=0)
        return SimpleNamespace(**fields)
    monkeypatch.setattr(mac, 'protected_directory', directory)
    monkeypatch.setattr(c, '_native_root_file', root_file)
    monkeypatch.setattr(os, 'fstat', fstat)
    actual_read = os.read
    def read(fd, size):
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) == token:
            pytest.fail('Root read the connected token body')
        return actual_read(fd, size)
    monkeypatch.setattr(os, 'read', read)
    ledger = rollover(n)
    image = ledger['entries'][-1]['artifacts']['source-installer-rebind.json']
    retained = n.metadata / image['name']
    assert (retained.read_bytes(), retained.stat().st_ino) == before and not original.exists()
    receipt = helper.withdraw_installer_generation(n.selected, transaction=n.transaction, intent=n.intent)
    assert c.native_verify_installer_withdrawal(n.f.bridge, receipt, transaction=n.transaction) == receipt
    assert (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino) == environment
    assert (n.connected_token.stat().st_dev, n.connected_token.stat().st_ino) == token
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in owner_files} == owner_files
