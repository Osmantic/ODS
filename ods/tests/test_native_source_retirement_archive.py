"""Actual Source withdrawal and preserved fixed archive cursor on owned files.

Apple account/ACL/launchd observations use the explicit Linux metadata driver;
the immutable runtime, Root witness, publisher, token FDs and all renames are
production implementations. No real native Core service or provider executes.
"""
# ruff: noqa: E402,F401,F811
from contextlib import contextmanager
import importlib.util
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import time

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
from pixel_connections import custody as c, native_retirement as archive
import pixel_macos_custody as mac
import pixel_access_bridge as bridge
import pixel_macos_process as processes

spec = importlib.util.spec_from_file_location('source_archive_core_serializer', ROOT / 'installers/macos/lib/pixel-native-uninstall.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


@pytest.fixture
def archive_ready(executor, monkeypatch):
    n = executor
    raw_clock = lambda: time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    monkeypatch.setattr(bridge, '_native_source_clock_ns', raw_clock)
    monkeypatch.setattr(c, '_native_clock_ns', raw_clock)
    monkeypatch.setattr(c, 'NATIVE_RUNTIMES', n.f.root.parent)
    monkeypatch.setattr(archive, 'LAUNCHER', Path(n.f.bridge.binary))
    monkeypatch.setattr(archive, 'SETTINGS', n.world / 'pixel-access.json')
    monkeypatch.setattr(archive, 'ARCHIVE_ROOT', n.world / 'retired')
    monkeypatch.setattr(c, 'NATIVE_CONNECTION_RECORD', n.world / 'public-records/portal-connections.json')
    c.NATIVE_CONNECTION_RECORD.parent.mkdir(mode=0o755)
    archive.ARCHIVE_ROOT.mkdir(mode=0o700)
    settings = dict(owner=n.f.bridge.owner.pw_name, install_dir=str(n.f.bridge.install),
        state_dir=str(n.metadata), openclaw_bin=str(archive.LAUNCHER))
    archive.SETTINGS.write_bytes(c._native_encoded(settings))
    archive.SETTINGS.chmod(0o600)
    previous_directory = mac.protected_directory
    allowed = {n.world, n.metadata, n.kit.parent, n.units, c.NATIVE_CONNECTION_RECORD.parent,
        archive.ARCHIVE_ROOT, n.f.root.parent.parent}
    @contextmanager
    def directory(path, **kwargs):
        path = Path(path)
        if path in allowed:
            assert not kwargs
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: yield fd
            finally: os.close(fd)
        else:
            with previous_directory(path, **kwargs) as fd: yield fd
    monkeypatch.setattr(mac, 'protected_directory', directory)
    previous_bytes = mac.protected_bytes
    def protected(path, *, limit=2 * 1024 * 1024):
        path = Path(path)
        if path.is_relative_to(n.world):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: return c._native_root_file(directory, path.name, limit=limit)[0]
            finally: os.close(directory)
        body = previous_bytes(path)
        if len(body) > limit: raise ValueError('fixture protected read exceeds actual bound')
        return body
    monkeypatch.setattr(mac, 'protected_bytes', protected)
    # Only OS command/birth observations are substituted. The actual fixed
    # Core-stop reader still checks its complete label/boot/hash/tree family.
    monkeypatch.setattr(archive, '_command', lambda argv: n.f.bridge.command(argv))
    monkeypatch.setattr(processes, 'process_birth', lambda pid: proc(pid)[1:])
    token = c.NATIVE_STATE / 'private/google-token.json'
    token.write_bytes(b'SYNTHETIC_OAUTH_BODY_MUST_NEVER_BE_READ')
    token.chmod(0o600)
    info = token.stat()
    n.owners[(info.st_dev, info.st_ino)] = (n.identity['id'], n.identity['id'])
    token_key = info.st_dev, info.st_ino
    previous_read = os.read
    def read(fd, count):
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) == token_key:
            raise AssertionError('retirement must not read the token body, even after rename')
        return previous_read(fd, count)
    monkeypatch.setattr(os, 'read', read)
    n.ops_identity = dict(schema=1, name='_ods_pixel_ops', id=60002,
        userGuid='11111111-2222-4333-8444-555555555555', groupGuid='66666666-7777-4888-8999-AAAAAAAAAAAA')
    for name, body in (('lock', b''), ('ops-identity.json', c._native_encoded(n.ops_identity)), ('ops-identity.lock', b'')):
        path = n.metadata / name
        path.write_bytes(body)
        path.chmod(0o600)
    with n.f.bridge.bounded(900):
        inventory = archive.add_history(n.selected, c.native_retirement_inventory(n.selected))
        doc = c._native_selected(n.selected, _publication=True)
        runtime = dict(deploymentSha256=n.selected.deployment.checksum, custodySha256=doc['runtimeCustodySha256'],
            bundleSha256=doc['bundleSha256'], sourceRef=inventory['binding']['sourceRef'])
        child = subprocess.Popen([sys.executable, '-B', '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
        try: _parent, sec, usec = proc(child.pid)
        finally:
            child.stdin.close()
            child.wait(timeout=5)
        core_before = dict(schema=1, owner=doc['ownerUid'], boot=inventory['boot'].lower(),
            hashes={str(path): digest(path.read_bytes()) for path in [archive.SETTINGS,
                *[n.metadata / name for name in ('lock', 'ops-identity.json', 'ops-identity.lock')]]},
            trees={'system/com.ods.pixel-' + role: [[child.pid, sec, usec]] for role in archive.CORE_ROLES}, sandboxes=[])
        source_before = dict(schemaVersion=1, kind='ods-native-source-retirement-intent', nonce='a' * 32,
            owner=dict(uid=doc['ownerUid'], gid=doc['ownerGid'], name=n.f.bridge.owner.pw_name,
                installDir=str(n.f.bridge.install)), runtime=runtime, inventory=inventory)
        n.witness = core.source_retirement_witness(core_before, source_before)
        n.raw = c._native_encoded(n.witness)
        path = n.metadata / 'retirement.json'
        path.write_bytes(n.raw)
        path.chmod(0o600)
        n.transaction = core.source_retirement_transaction(n.raw)
        n.intent = helper.retirement_withdrawal_intent(n.selected, transaction=n.transaction)
        n.withdrawal = helper.withdraw_retirement_generation(n.selected, transaction=n.transaction, intent=n.intent)
    n.archive_id = n.witness['source']['nonce']
    n.archive = archive.ARCHIVE_ROOT / n.archive_id
    n.archive.mkdir(mode=0o700)
    allowed.add(n.archive)
    prepared = dict(schema=2, kind=archive.ARCHIVE_KIND, owner=doc['ownerUid'], installDir=str(n.f.bridge.install),
        status='retiring', moves=[], source=dict(transaction=n.transaction,
            inventorySha256=digest(c._native_encoded(inventory)), planSha256=None, phase='prepared'))
    (n.archive / 'receipt.json').write_bytes(c._native_encoded(prepared))
    (n.archive / 'receipt.json').chmod(0o600)
    n.token_inode = token_key
    n.environment = c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino
    return n


def test_actual_source_archive_preserves_all_inodes_and_never_reads_token(archive_ready):
    n = archive_ready
    with pytest.raises(c.CustodyError, match='archive-id-invalid'):
        archive.archive_begin(transaction=n.transaction, archive_id='b' * 32)
    assert not (n.metadata / archive.CURSOR).exists()
    result = archive.archive_begin(transaction=n.transaction, archive_id=n.archive_id)
    original_cursor = json.loads((n.metadata / archive.CURSOR).read_bytes())
    assert result['phase'] == 'source' and result['completed'] == 0
    while result['phase'] != 'core':
        result = archive.archive_step(transaction=n.transaction)
    cursor = json.loads((n.metadata / archive.CURSOR).read_bytes())
    assert cursor['plan'] == original_cursor['plan'] and result['completed'] == result['total']
    for index, row in enumerate(cursor['plan']):
        target = n.archive / archive._destination(index)
        assert [target.stat().st_dev, target.stat().st_ino] == archive._entry_inode(row)
        assert not os.path.lexists(archive._row_origin(row))
    state_index = next(i for i, row in enumerate(cursor['plan']) if row['kind'] == 'state')
    token = n.archive / archive._destination(state_index) / 'private/google-token.json'
    assert (token.stat().st_dev, token.stat().st_ino) == n.token_inode
    saved_env = n.archive / archive._destination(state_index) / c.NATIVE_ENV.name
    assert (saved_env.read_bytes(), saved_env.stat().st_ino) == n.environment
    assert (n.metadata / 'source-identity.json').is_file()
    assert archive.archive_step(transaction=n.transaction) == result
    assert (n.metadata / 'retirement.json').read_bytes() == n.raw
    assert not list(n.units.iterdir())


@pytest.mark.parametrize('where', ['cursor-bind', 'item-rename', 'cursor-ack'])
def test_archive_lost_ack_reuses_exact_frozen_cursor_and_item_inode(archive_ready, monkeypatch, where):
    n = archive_ready
    original_rename, original_save = os.rename, archive._save_at
    hits = []
    def renamed(first, second, **kwargs):
        result = original_rename(first, second, **kwargs)
        if where == 'item-rename' and not hits and str(second).startswith('source-item-'):
            hits.append(True)
            raise OSError('invented archive reply loss')
        return result
    def saved(directory, name, value, **kwargs):
        result = original_save(directory, name, value, **kwargs)
        if not hits and name == archive.CURSOR and (where == 'cursor-bind' or where == 'cursor-ack' and value['next'] == 1):
            hits.append(True)
            raise OSError('invented archive reply loss')
        return result
    monkeypatch.setattr(os, 'rename', renamed)
    monkeypatch.setattr(archive, '_save_at', saved)
    with pytest.raises(OSError, match='archive reply loss'):
        archive.archive_begin(transaction=n.transaction, archive_id=n.archive_id)
        archive.archive_step(transaction=n.transaction)
    frozen = json.loads((n.metadata / archive.CURSOR).read_bytes())
    monkeypatch.setattr(os, 'rename', original_rename)
    monkeypatch.setattr(archive, '_save_at', original_save)
    result = archive.archive_begin(transaction=n.transaction, archive_id=n.archive_id)
    result = archive.archive_step(transaction=n.transaction)
    resumed = json.loads((n.metadata / archive.CURSOR).read_bytes())
    assert hits and resumed['plan'] == frozen['plan'] and resumed['archiveInode'] == frozen['archiveInode']
    assert result['completed'] >= 1


@pytest.mark.parametrize('fault', ['token-inode', 'new-metadata', 'live-label', 'owner-guid', 'witness'])
def test_changed_archive_authority_preserves_all_items_before_any_rename(archive_ready, monkeypatch, fault):
    n = archive_ready
    archive.archive_begin(transaction=n.transaction, archive_id=n.archive_id)
    if fault == 'token-inode':
        token = c.NATIVE_STATE / 'private/google-token.json'
        replacement = token.with_name('next-token')
        replacement.write_bytes(b'SYNTHETIC')
        replacement.chmod(0o600)
        info = replacement.stat()
        n.owners[(info.st_dev, info.st_ino)] = (n.identity['id'], n.identity['id'])
        os.replace(replacement, token)
    elif fault == 'new-metadata':
        path = n.metadata / 'source-unbound.json'
        path.write_bytes(b'{}')
        path.chmod(0o600)
    elif fault == 'live-label':
        command = archive._command
        monkeypatch.setattr(archive, '_command', lambda argv: 'system = {\ncom.ods.pixel-native-gateway\n}\n'
            if argv == ['/bin/launchctl', 'print', 'system'] else command(argv))
    elif fault == 'owner-guid':
        n.acl.identities[n.f.bridge.owner.pw_uid] = b'X' * 16
    else:
        (n.metadata / 'retirement.json').write_bytes(n.raw + b' ')
    before = {path.name: path.stat().st_ino for path in n.archive.iterdir()}
    cursor = (n.metadata / archive.CURSOR).read_bytes()
    with pytest.raises((ValueError, c.CustodyError)):
        archive.archive_step(transaction=n.transaction)
    assert {path.name: path.stat().st_ino for path in n.archive.iterdir()} == before
    assert (n.metadata / archive.CURSOR).read_bytes() == cursor


def core_prefix(n, monkeypatch):
    progress = archive.archive_begin(transaction=n.transaction, archive_id=n.archive_id)
    while progress['phase'] != 'core': progress = archive.archive_step(transaction=n.transaction)
    monkeypatch.setattr(core, 'STATE', n.metadata)
    monkeypatch.setattr(core, 'SETTINGS', archive.SETTINGS)
    monkeypatch.setattr(core, 'DIRECTORIES', (str(n.f.root.parent),))
    # Actual Operations receipt/attribute validators remain unchanged. Only
    # Directory Services observations use the same explicit OS driver model.
    original_helper = core.helper
    account = original_helper('pixel-native-ops-account')
    monkeypatch.setattr(account, 'read_record', lambda kind: {
        key: [value] for key, value in account.expected_attributes(n.ops_identity, kind).items()})
    monkeypatch.setattr(core, 'helper', lambda name: account if name == 'pixel-native-ops-account' else original_helper(name))
    # The shared runtime fixture models Root with the Linux test UID outside
    # n.world. After its complete immutable proof, model this one Core root as
    # UID0 on both sides of its rename; its inode/content are not substituted.
    runtime_info = n.f.root.parent.stat()
    runtime_inode = runtime_info.st_dev, runtime_info.st_ino
    old_fstat = os.fstat
    def fstat(fd):
        info = old_fstat(fd)
        if (info.st_dev, info.st_ino) != runtime_inode: return info
        fields = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
        fields.update(st_uid=0, st_gid=0)
        return SimpleNamespace(**fields)
    monkeypatch.setattr(os, 'fstat', fstat)
    paths = [n.f.root.parent, archive.SETTINGS]
    lock = os.open(n.metadata / 'lock', os.O_RDWR | os.O_NOFOLLOW)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return paths, lock


def test_actual_core_prefix_and_final_controls_preserve_same_archive(archive_ready, monkeypatch):
    n = archive_ready
    paths, lock = core_prefix(n, monkeypatch)
    original = {path: (path.stat().st_dev, path.stat().st_ino) for path in paths}
    try:
        result = core._source_core_archive(n.archive, transaction=n.transaction, witness=n.witness, paths=paths)
        assert result == {'status': 'retired', 'archive': str(n.archive)}
        for i, path in enumerate(paths):
            assert not path.exists()
            target = n.archive / ('item-' + str(i))
            assert (target.stat().st_dev, target.stat().st_ino) == original[path]
        assert core._source_core_archive(n.archive, transaction=n.transaction, witness=n.witness) == result
        assert core._source_finish_controls(n.archive, transaction=n.transaction, witness=n.witness) == result
    finally: os.close(lock)
    assert set(p.name for p in n.metadata.iterdir()) == {'source-identity.json', 'ops-identity.json', 'ops-identity.lock'}
    assert (n.archive / 'source-witness.json').read_bytes() == n.raw
    assert json.loads((n.archive / 'receipt.json').read_bytes())['status'] == 'retired'
    assert (n.archive / 'source-controller-lock').is_file() and (n.archive / 'source-cursor.json').is_file()


def test_actual_core_and_final_control_lost_replies_reuse_one_plan(archive_ready, monkeypatch):
    n = archive_ready
    paths, lock = core_prefix(n, monkeypatch)
    rename, save = os.rename, archive._save_at
    hits, fault = set(), {'where': 'core-rename'}
    def renamed(first, second, **kwargs):
        result = rename(first, second, **kwargs)
        selected = fault['where'] == 'core-rename' and second == 'item-0' or fault['where'] == 'final-witness' and second == 'source-witness.json' or fault['where'] == 'final-lock' and second == 'source-controller-lock'
        if selected and fault['where'] not in hits:
            hits.add(fault['where'])
            raise OSError('invented Core archive reply loss')
        return result
    def saved(directory, name, value, **kwargs):
        result = save(directory, name, value, **kwargs)
        if fault['where'] == 'core-cas' and fault['where'] not in hits and name == archive.CORE_PLAN and value['next'] == 1:
            hits.add(fault['where'])
            raise OSError('invented Core archive reply loss')
        return result
    monkeypatch.setattr(os, 'rename', renamed)
    monkeypatch.setattr(archive, '_save_at', saved)
    try:
        frozen = None
        for where in ('core-rename', 'core-cas', 'final-witness', 'final-lock'):
            fault['where'] = where
            with pytest.raises(OSError, match='Core archive reply loss'):
                core._source_core_archive(n.archive, transaction=n.transaction, witness=n.witness, paths=paths)
                core._source_finish_controls(n.archive, transaction=n.transaction, witness=n.witness)
            observed = json.loads((n.archive / archive.CORE_PLAN).read_bytes())
            if frozen is None: frozen = observed
            assert observed['plan'] == frozen['plan'] and observed['archiveInode'] == frozen['archiveInode']
        monkeypatch.setattr(os, 'rename', rename)
        monkeypatch.setattr(archive, '_save_at', save)
        result = core._source_finish_controls(n.archive, transaction=n.transaction, witness=n.witness)
        after = json.loads((n.archive / archive.CORE_PLAN).read_bytes())
        assert result['status'] == 'retired' and after['plan'] == frozen['plan']
        assert hits == {'core-rename', 'core-cas', 'final-witness', 'final-lock'}
        assert not list(archive.ARCHIVE_ROOT.glob('*/item-2'))
    finally: os.close(lock)


def test_public_retire_resumes_same_core_archive_after_settings_and_lock_moved(archive_ready, monkeypatch):
    n = archive_ready
    paths, lock = core_prefix(n, monkeypatch)
    rename = os.rename
    hit = []
    def renamed(first, second, **kwargs):
        result = rename(first, second, **kwargs)
        if not hit and second == 'source-controller-lock':
            hit.append(True)
            raise OSError('invented final lock reply loss')
        return result
    monkeypatch.setattr(os, 'rename', renamed)
    try:
        core._source_core_archive(n.archive, transaction=n.transaction, witness=n.witness, paths=paths)
        with pytest.raises(OSError, match='final lock reply loss'):
            core._source_finish_controls(n.archive, transaction=n.transaction, witness=n.witness)
    finally: os.close(lock)  # The interrupted caller no longer owns the flock.
    monkeypatch.setattr(os, 'rename', rename)
    assert not archive.SETTINGS.exists() and not (n.metadata / 'lock').exists()
    assert (n.metadata / archive.CURSOR).is_file()
    checks = []
    # The Source/private/Core control path is real. This final pre-existing
    # Operations DS verification boundary has its own actual account tests.
    account = core.helper('pixel-native-ops-account')
    monkeypatch.setattr(account, 'verify_identity_only', lambda **kwargs: checks.append(kwargs))
    monkeypatch.setattr(account, 'verify_empty_home_only', lambda **kwargs: checks.append(kwargs))
    monkeypatch.setattr(core.sys, 'platform', 'darwin')
    monkeypatch.setattr(core.os, 'geteuid', lambda: 0)
    result = core.retire(str(n.f.bridge.install), n.f.bridge.owner.pw_name)
    assert result == {'status': 'retired', 'archive': str(n.archive)}
    assert checks == [dict(source_owner=n.f.bridge.owner.pw_name, source_install_dir=str(n.f.bridge.install))]
    assert hit and not (n.metadata / archive.CURSOR).exists()
