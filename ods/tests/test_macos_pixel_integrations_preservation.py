"""Disposable filesystem tests for rollback preservation authority, never Root."""
import copy
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


LIB = Path(__file__).resolve().parents[1] / 'installers/macos/lib'
SPEC = importlib.util.spec_from_file_location('native_integrations_preservation', LIB / 'pixel-native-integrations-preservation.py')
preservation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preservation)


@pytest.fixture
def authority():
    return dict(owner=SimpleNamespace(pw_uid=501, pw_name='owner'), install_dir='/Users/owner/ods',
        settings=json.dumps(dict(owner='owner', install_dir='/Users/owner/ods')).encode(),
        services=json.dumps(dict(owner=501, progress={'phase': 'services-active'},
            recovery={'definitions': dict.fromkeys(('manager', 'promoter', 'operations'), {})})).encode(),
        installation=b'{"owner":501,"phase":"active"}')


def make(authority):
    return preservation.make(**authority, current_digest='a' * 64, candidate_digest='b' * 64,
                             common_body=b'VERIFIED = True\n')


@pytest.mark.parametrize('fault', [None, 'owner', 'name', 'install', 'settings', 'services', 'installation',
    'extra-root', 'partial-roots', 'unknown-key', 'source', 'digest', 'new-role', 'pending'])
def test_exact_root_family_binds_independent_prior_deployment(authority, fault):
    value = make(authority)
    if fault == 'owner': value['owner'] = 502
    if fault == 'name': value['ownerName'] = 'another'
    if fault == 'install': value['installDir'] = '/other'
    if fault in ('settings', 'services', 'installation'): authority[fault] += b' '
    if fault == 'extra-root': value['rootsBefore']['/Users/owner/Documents'] = None
    if fault == 'partial-roots': value['rootsBefore'].pop(next(iter(value['rootsBefore'])))
    if fault == 'unknown-key': value['adopt'] = True
    if fault == 'source': value['common']['body'] = 'ZWxzZQ=='
    if fault == 'digest': value['candidateDigest'] = value['currentDigest']
    if fault in ('new-role', 'pending'):
        services = json.loads(authority['services'])
        if fault == 'new-role': services['recovery']['definitions']['integrations'] = {}
        else: services['requiresRecovery'] = True
        authority['services'] = json.dumps(services).encode()
        value['prior']['services'] = preservation.sha(authority['services'])
    if fault:
        with pytest.raises(ValueError): preservation.validate(value, **authority)
    else:
        assert preservation.validate(value, **authority) == b'VERIFIED = True\n'
        assert tuple(value['rootsBefore']) == preservation.ROOTS
        assert value['rootsBefore'] == dict.fromkeys(preservation.ROOTS)


@pytest.fixture
def filesystem(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    roots = tuple(str(tmp_path / name) for name in ('runtime', 'auth', 'logs'))
    monkeypatch.setattr(preservation, 'ROOTS', roots)
    @contextmanager
    def directory(path):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: yield descriptor
        finally: os.close(descriptor)
    def read(path, **kwargs): return Path(path).read_bytes()
    custody = SimpleNamespace(protected_directory=directory, protected_bytes=read,
        protected_inspection_bytes=lambda path, **kwargs: Path(path).read_bytes() if os.path.lexists(path) else None)
    return tmp_path, roots, custody


@pytest.mark.parametrize('fault', [None, 'write', 'rename', 'after-rename'])
def test_interrupted_atomic_publication_never_provisions_without_durable_intent(filesystem, authority, monkeypatch, fault):
    state, roots, custody = filesystem
    value = make(authority)
    original_rename, original_fsync = preservation.os.rename, preservation.os.fsync
    calls = []
    def rename(*args, **kwargs):
        if fault == 'rename': raise OSError('before rename')
        calls.append('rename')
        return original_rename(*args, **kwargs)
    def sync(descriptor):
        if (fault == 'write' and not calls) or (fault == 'after-rename' and calls):
            raise OSError('sync interrupted')
        return original_fsync(descriptor)
    with monkeypatch.context() as patch:
        patch.setattr(preservation.os, 'rename', rename)
        patch.setattr(preservation.os, 'fsync', sync)
        if fault:
            with pytest.raises(OSError): preservation.publish(state, value, custody)
        else:
            preservation.publish(state, value, custody)
    assert not any(Path(root).exists() for root in roots)
    # Retry can use a complete published intent or preserve an interrupted
    # pending file before exclusively publishing a fresh complete intent.
    pending = state / preservation.PENDING
    saved = pending.read_bytes() if pending.exists() else None
    preservation.publish(state, value, custody)
    assert preservation.load(state, custody) == value
    assert not pending.exists()
    if saved is not None:
        history = state / ('integrations-preservation-interrupted-' + preservation.sha(saved) + '.json')
        assert history.read_bytes() == saved
    # Real preparation may now have begun; idempotent publication never resets it.
    Path(roots[0]).mkdir()
    preservation.publish(state, value, custody)
    assert Path(roots[0]).exists()


def test_existing_private_data_without_root_intent_cannot_be_adopted(filesystem, authority):
    state, roots, custody = filesystem
    Path(roots[1]).mkdir()
    with pytest.raises(ValueError, match='unbound-roots'):
        preservation.publish(state, make(authority), custody)
    assert not (state / preservation.NAME).exists()


def test_retry_does_not_overwrite_prior_owner_or_source_authority(filesystem, authority):
    state, _, custody = filesystem
    value = make(authority)
    preservation.publish(state, value, custody)
    changed = copy.deepcopy(value)
    changed['candidateDigest'] = 'c' * 64
    with pytest.raises(ValueError, match='conflict'): preservation.publish(state, changed, custody)
    assert preservation.load(state, custody) == value
