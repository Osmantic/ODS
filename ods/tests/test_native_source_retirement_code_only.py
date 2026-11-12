"""Retirement of a committed code-only Source install through the actual archive and Core phases.

The fleet Mac mini's state of 11 November 2026: a completed code-only update (an
inert kit, its committed installer ledger, no account or private state), then a
Root witness and a prepared archive with no cursor. The installer, publisher,
witness and every rename are production code on owned files; only the existing
Apple metadata driver and OS observations are modeled.
"""
# ruff: noqa: E402,F401,F811
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs metadata harness, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_installer_code_only import (first_target, material, native, deployment, built, source,
    package, runtime_custody, bundle, services, helper, qualification_operation, old_component_owner_observation,
    finished_update, uninstall as core)
from test_native_source_custody import launchctl_system
from test_native_source_executor import proc
from test_native_source_installer_withdrawal import digest, files
from pixel_connections import custody as c, native_retirement as archive
import pixel_macos_custody as mac
import pixel_macos_process as processes

pytestmark = pytest.mark.parametrize('first_target', ['code-only'], indirect=True)
LEDGER = archive.CODE_ONLY_LEDGER


@pytest.fixture
def retiring(first_target, monkeypatch):
    n = first_target
    finished_update(n, monkeypatch)
    units = n.world / 'units'
    units.mkdir(mode=0o755)
    monkeypatch.setattr(c, 'NATIVE_DEFINITIONS', units)
    monkeypatch.setattr(archive, 'LAUNCHER', Path(n.f.bridge.binary))
    monkeypatch.setattr(archive, 'SETTINGS', n.world / 'pixel-access.json')
    monkeypatch.setattr(archive, 'ARCHIVE_ROOT', n.world / 'retired')
    archive.ARCHIVE_ROOT.mkdir(mode=0o700)
    archive.SETTINGS.write_bytes(c._native_encoded(dict(owner=n.f.bridge.owner.pw_name,
        install_dir=str(n.f.bridge.install), state_dir=str(n.metadata), openclaw_bin=str(archive.LAUNCHER))))
    archive.SETTINGS.chmod(0o600)
    previous = mac.protected_directory
    allowed = {n.world, n.metadata, n.kit.parent, units, archive.ARCHIVE_ROOT}
    @contextmanager
    def directory(path, **kwargs):
        path = Path(path)
        if path in allowed or path.parent == archive.ARCHIVE_ROOT:
            assert not kwargs
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: yield fd
            finally: os.close(fd)
        else:
            with previous(path, **kwargs) as fd: yield fd
    monkeypatch.setattr(mac, 'protected_directory', directory)
    # Only OS command/birth observations are substituted; the census still
    # parses the captured layout, including launchd's override table.
    monkeypatch.setattr(archive, '_command', lambda argv: n.f.bridge.command(argv))
    monkeypatch.setattr(processes, 'process_birth', lambda pid: proc(pid)[1:])
    n.ops_identity = dict(schema=1, name='_ods_pixel_ops', id=60002,
        userGuid='11111111-2222-4333-8444-555555555555', groupGuid='66666666-7777-4888-8999-AAAAAAAAAAAA')
    for name, body in (('lock', b''), ('ops-identity.json', c._native_encoded(n.ops_identity)), ('ops-identity.lock', b'')):
        (n.metadata / name).write_bytes(body)
        (n.metadata / name).chmod(0o600)
    n.inventory = archive.code_only_inventory(n.selected)
    doc = c._native_selected(n.selected, _publication=True)
    child = subprocess.Popen([sys.executable, '-B', '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
    try: _parent, sec, usec = proc(child.pid)
    finally:
        child.stdin.close()
        child.wait(timeout=5)
    core_before = dict(schema=1, owner=doc['ownerUid'], boot=n.inventory['boot'].lower(),
        hashes={str(path): digest(path.read_bytes()) for path in [archive.SETTINGS,
            *[n.metadata / name for name in ('lock', 'ops-identity.json', 'ops-identity.lock')]]},
        trees={'system/com.ods.pixel-' + role: [[child.pid, sec, usec]] for role in archive.CORE_ROLES}, sandboxes=[])
    source_before = dict(schemaVersion=1, kind='ods-native-source-retirement-intent', nonce='a' * 32,
        owner=dict(uid=doc['ownerUid'], gid=doc['ownerGid'], name=n.f.bridge.owner.pw_name,
            installDir=str(n.f.bridge.install)), runtime=n.inventory['runtime'], inventory=n.inventory)
    n.witness = core.source_retirement_witness(core_before, source_before)
    n.archive = archive.ARCHIVE_ROOT / ('a' * 32)
    n.archive.mkdir(mode=0o700)
    write_witness(n)
    n.ledger = n.metadata / LEDGER
    return n


def write_witness(n):
    """Publish the Root witness and its prepared archive receipt, as retire() does."""
    n.raw = c._native_encoded(n.witness)
    (n.metadata / 'retirement.json').write_bytes(n.raw)
    (n.metadata / 'retirement.json').chmod(0o600)
    n.transaction = core.source_retirement_transaction(n.raw)
    receipt = dict(schema=2, kind=archive.ARCHIVE_KIND, owner=n.witness['owner'],
        installDir=n.witness['source']['owner']['installDir'], status='retiring', moves=[],
        source=dict(transaction=n.transaction, inventorySha256=digest(c._native_encoded(n.witness['source']['inventory'])),
            planSha256=None, phase='prepared'))
    (n.archive / 'receipt.json').write_bytes(c._native_encoded(receipt))
    (n.archive / 'receipt.json').chmod(0o600)


def mac_stop_point(n):
    """Witness and prepared archive written, no cursor, the ledger still live and unbound by the witness."""
    assert (n.metadata / 'retirement.json').read_bytes() == n.raw
    assert not (n.metadata / archive.CURSOR).exists()
    assert [path.name for path in n.archive.iterdir()] == ['receipt.json']
    assert n.ledger.is_file() and LEDGER not in n.inventory['metadata']
    assert LEDGER not in n.inventory['history']['metadata']


def ops_account(n, monkeypatch):
    """Actual Operations receipt validators; only Directory Services reads are modeled."""
    original = core.helper
    account = original('pixel-native-ops-account')
    monkeypatch.setattr(account, 'read_record', lambda kind: {
        key: [value] for key, value in account.expected_attributes(n.ops_identity, kind).items()})
    monkeypatch.setattr(core, 'helper', lambda name: account if name == 'pixel-native-ops-account' else original(name))
    monkeypatch.setattr(core, 'STATE', n.metadata)
    monkeypatch.setattr(core, 'SETTINGS', archive.SETTINGS)
    return account


def test_plan_binds_only_the_witness_proven_committed_ledger(retiring):
    n = retiring
    mac_stop_point(n)
    record = json.loads(n.ledger.read_bytes())
    assert record['phase'] == 'committed' and archive._code_only_history_nonces(n.inventory) == {record['nonce']}
    inode = [n.ledger.stat().st_dev, n.ledger.stat().st_ino]
    result = archive.archive_begin(transaction=n.transaction, archive_id=n.archive.name)
    cursor = json.loads((n.metadata / archive.CURSOR).read_bytes())
    rows = {(row['kind'], row['name']): row['expected'] for row in cursor['plan']}
    assert result['phase'] == 'source' and set(rows) == {('metadata', LEDGER), ('kit', None),
        *(('metadata', name) for name in (*n.inventory['metadata'], *n.inventory['history']['metadata'])),
        *(('kit-history', name) for name in n.inventory['history']['kits'])}
    ledger = rows[('metadata', LEDGER)]
    assert (ledger['kind'], ledger['inode'], ledger['mode'], ledger['gid']) == ('file', inode, 0o600, 0)
    assert ledger['sha256'] == digest(n.ledger.read_bytes())
    # The cursor cannot widen that single binding.
    for change in ('mode', 'directory', 'name'):
        forged = json.loads(json.dumps(cursor))
        row = next(item for item in forged['plan'] if item['name'] == LEDGER)
        if change == 'mode': row['expected']['mode'] = 0o644
        elif change == 'directory': row['expected'] = dict(kind='directory', inode=inode, mode=0o700, gid=0, entries={})
        else: row['name'] = 'source-installer-code-only-' + 'e' * 64 + '.json'
        with pytest.raises(c.CustodyError, match='cursor-plan-unbound'):
            archive._bind_plan(n.witness, forged)
    archive._bind_plan(n.witness, cursor)


def test_mac_stop_point_resumes_and_retires_with_the_ledger_archived(retiring, monkeypatch):
    n = retiring
    mac_stop_point(n)
    receipt = (n.archive / 'receipt.json').read_bytes()
    original = archive._command
    loaded = ['com.ods.pixel-native-gateway']
    monkeypatch.setattr(archive, '_command', lambda argv: launchctl_system(*loaded)
        if argv == ['/bin/launchctl', 'print', 'system'] else original(argv))
    # A refused census, as on the Mac, leaves the same stop point.
    with pytest.raises(c.CustodyError, match='native-source-retirement-actors-present'):
        archive.archive_begin(transaction=n.transaction, archive_id=n.archive.name)
    mac_stop_point(n)
    assert (n.archive / 'receipt.json').read_bytes() == receipt
    loaded.clear()  # Booted out; launchd keeps listing the override entries.
    ledger = (n.ledger.read_bytes(), n.ledger.stat().st_ino)
    progress = archive.archive_begin(transaction=n.transaction, archive_id=n.archive.name)
    while progress['phase'] != 'core':
        progress = archive.archive_step(transaction=n.transaction)
    cursor = json.loads((n.metadata / archive.CURSOR).read_bytes())
    index = next(i for i, row in enumerate(cursor['plan']) if row['name'] == LEDGER)
    saved = n.archive / archive._destination(index)
    assert not n.ledger.exists() and (saved.read_bytes(), saved.stat().st_ino) == ledger
    assert {name for name in os.listdir(n.metadata) if name.startswith(archive._SOURCE_PREFIXES)} == {archive.CURSOR}
    # The Core phase moves the remaining Core records and settings, as retire() would.
    account = ops_account(n, monkeypatch)
    leftovers = [n.metadata / name for name in sorted(os.listdir(n.metadata))
        if name not in core.RETAIN | {'lock', 'retirement.json', archive.CURSOR}]
    assert leftovers and all(core.state_name_allowed(path.name) for path in leftovers)
    lock = os.open(n.metadata / 'lock', os.O_RDWR | os.O_NOFOLLOW)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    rename, hit = os.rename, []
    def renamed(first, second, **kwargs):
        result = rename(first, second, **kwargs)
        if not hit and second == 'source-controller-lock':
            hit.append(True)
            raise OSError('invented final lock reply loss')
        return result
    monkeypatch.setattr(os, 'rename', renamed)
    try:
        assert core._source_core_archive(n.archive, transaction=n.transaction, witness=n.witness,
            paths=[archive.SETTINGS, *leftovers]) == {'status': 'retired', 'archive': str(n.archive)}
        with pytest.raises(OSError, match='final lock reply loss'):
            core._source_finish_controls(n.archive, transaction=n.transaction, witness=n.witness)
    finally: os.close(lock)  # The interrupted caller no longer owns the flock.
    monkeypatch.setattr(os, 'rename', rename)
    # The rerun takes retire()'s existing Core-plan route to completion.
    checks = []
    monkeypatch.setattr(account, 'verify_identity_only', lambda **kwargs: checks.append(kwargs))
    monkeypatch.setattr(account, 'verify_empty_home_only', lambda **kwargs: checks.append(kwargs))
    monkeypatch.setattr(core.sys, 'platform', 'darwin')
    monkeypatch.setattr(core.os, 'geteuid', lambda: 0)
    assert core.retire(str(n.f.bridge.install), n.f.bridge.owner.pw_name) == {'status': 'retired', 'archive': str(n.archive)}
    assert hit and checks == [{}] and set(os.listdir(n.metadata)) == {'ops-identity.json', 'ops-identity.lock'}
    assert (n.archive / 'source-witness.json').read_bytes() == n.raw
    assert json.loads((n.archive / 'receipt.json').read_bytes())['status'] == 'retired'
    assert (saved.read_bytes(), saved.stat().st_ino) == ledger


@pytest.mark.parametrize('fault', ['uncommitted', 'other-nonce', 'kit-inode', 'before-image', 'schema', 'mode',
    'second-ledger', 'two-transactions'])
def test_unproven_code_only_ledger_is_refused_before_anything_moves(retiring, fault):
    n = retiring
    record = json.loads(n.ledger.read_bytes())
    if fault == 'second-ledger':
        copy = n.metadata / ('source-installer-code-only-' + 'e' * 64 + '.json')
        copy.write_bytes(n.ledger.read_bytes())
        copy.chmod(0o600)
    elif fault == 'mode':
        n.ledger.chmod(0o644)
    elif fault == 'two-transactions':
        # A second frozen code-only transaction leaves no single one to bind.
        other, history = 'd' * 32, n.witness['source']['inventory']['history']
        kit = n.kit.parent / ('.source-kit-before-' + other)
        kit.mkdir(mode=0o755)
        with mac.protected_directory(n.metadata) as metadata, mac.protected_directory(n.kit.parent) as kits:
            for label in ('publication', 'sidefiles'):
                name = '.source-code-only-' + label + '-before-' + other + '.json'
                (n.metadata / name).write_bytes(b'{}\n')
                (n.metadata / name).chmod(0o600)
                history['metadata'][name] = archive._capture_node(metadata, name, remaining=[64, 1024])
            history['kits'][kit.name] = archive._capture_node(kits, kit.name, remaining=[64, 1024])
        write_witness(n)
    else:
        if fault == 'uncommitted': record['phase'] = 'sidefiles'
        elif fault == 'other-nonce':
            nonce = 'b' * 32 if record['nonce'] != 'b' * 32 else 'c' * 32
            record.update(nonce=nonce, candidate='.source-kit-' + nonce, backup='.source-kit-before-' + nonce)
        elif fault == 'kit-inode': record['oldKit']['inode'][1] += 1
        elif fault == 'before-image': record['publication']['beforeInode'][1] += 1
        else: record['unexpected'] = True
        n.ledger.write_bytes(c._native_encoded(record))  # Same inode, still Root 0600.
    before = files(n)
    codes = {'uncommitted': 'native-source-retirement-code-only-ledger-unsettled',
        'schema': 'native-source-installer-code-only-record-changed', 'mode': 'native-source-generation-file-unsafe',
        'second-ledger': 'native-source-retirement-archive-namespace-unbound'}
    with pytest.raises((c.CustodyError, ValueError), match=codes.get(fault, 'native-source-retirement-code-only-ledger-unbound')):
        archive.archive_begin(transaction=n.transaction, archive_id=n.archive.name)
    assert not (n.metadata / archive.CURSOR).exists() and [path.name for path in n.archive.iterdir()] == ['receipt.json']
    assert files(n) == before


def test_ledger_changed_after_the_plan_froze_stops_before_the_next_rename(retiring):
    n = retiring
    archive.archive_begin(transaction=n.transaction, archive_id=n.archive.name)
    cursor = (n.metadata / archive.CURSOR).read_bytes()
    record = json.loads(n.ledger.read_bytes())
    record['proofSha256'] = 'f' * 64 if record['proofSha256'] != 'f' * 64 else 'e' * 64
    n.ledger.write_bytes(c._native_encoded(record))
    before = files(n)
    with pytest.raises(c.CustodyError, match='native-source-retirement-archive-image-changed'):
        archive.archive_step(transaction=n.transaction)
    assert files(n) == before and (n.metadata / archive.CURSOR).read_bytes() == cursor
