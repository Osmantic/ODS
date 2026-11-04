"""Actual native Source ledger-derived history; explicit Apple metadata driver."""
# ruff: noqa: E402,F401,F811
import copy
import json
import os
from pathlib import Path
import sys
import time

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs metadata harness, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_executor import executor, qualification_operation
from test_native_source_generation import generation, SPECS
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_runtime_publication import built, source, package
from pixel_provider import runtime_custody
from test_native_source_publication import material, publication, helper
from test_native_source_installer_rebind import source_before, restored, upgraded
from pixel_connections import custody as c
from pixel_connections import native_retirement as retirement
from pixel_connections import native_executor as ex
import pixel_access_bridge as bridge
import pixel_macos_custody as mac


@pytest.fixture(autouse=True)
def history_driver(material, monkeypatch):
    raw_clock = lambda: time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    monkeypatch.setattr(bridge, '_native_source_clock_ns', raw_clock)
    monkeypatch.setattr(c, '_native_clock_ns', raw_clock)
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: '01234567-89AB-4DEF-8123-0123456789AB')
    def publisher(selection):
        helper._material(selection)
        return helper
    monkeypatch.setattr(ex, '_publisher', publisher)
    parent = material.world / 'public-records'
    parent.mkdir(mode=0o755)
    monkeypatch.setattr(c, 'NATIVE_CONNECTION_RECORD', parent / 'portal-connections.json')
    previous = mac.protected_directory
    from contextlib import contextmanager
    @contextmanager
    def directory(path, **kwargs):
        if Path(path) == parent:
            assert not kwargs
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: yield fd
            finally: os.close(fd)
        else:
            with previous(path, **kwargs) as fd: yield fd
    monkeypatch.setattr(mac, 'protected_directory', directory)
    with material.f.bridge.bounded(300):
        yield


def runtime(n):
    _module, doc, _component, ref = helper._material(n.selected)
    return dict(deploymentSha256=n.selected.deployment.checksum, custodySha256=doc['runtimeCustodySha256'],
        bundleSha256=doc['bundleSha256'], sourceRef=ref)


def current_metadata(n):
    return {name: {'inode': [path.stat().st_dev, path.stat().st_ino], 'sha256': helper.sha(path.read_bytes())}
        for name in retirement._CURRENT if (path := n.metadata / name).is_file()}


def test_actual_inert_kit_inventory_is_code_only_without_generation_or_private_state(material, monkeypatch):
    n = material
    n.world.chmod(0o755)
    descriptor = json.loads(runtime_custody.DESCRIPTOR.read_bytes())
    monkeypatch.setattr(c, 'NATIVE_RUNTIME_DESCRIPTOR', runtime_custody.DESCRIPTOR)
    monkeypatch.setattr(c, 'NATIVE_RUNTIME_MANIFEST', Path(descriptor['manifest']))
    monkeypatch.setattr(c, 'NATIVE_RUNTIMES', n.f.root.parent)
    monkeypatch.setattr(n.f.bridge, 'state', n.metadata, raising=False)
    value = retirement.code_only_inventory(n.selected)
    assert value['kind'] == retirement.CODE_ONLY_KIND and value['kit']['inode']
    assert value['identity']['kind'] == 'retained' and value['history']['kits'] == {}
    assert 'generation' not in value and 'token' not in value
    assert not c.NATIVE_STATE.exists() and not c.NATIVE_LOGS.exists()
    assert set(p.name for p in n.kit.iterdir()) == {'source-kit.json', 'broker.py', 'source-broker.env'}


def test_actual_publication_history_uses_only_ledger_derived_backups(publication):
    n = publication
    helper.publish_kit(n.selected)
    record = json.loads((n.metadata / 'source-publication.json').read_bytes())
    metadata = {name: {'inode': [path.stat().st_dev, path.stat().st_ino], 'sha256': helper.sha(path.read_bytes())}
        for name in retirement._CURRENT if (path := n.metadata / name).is_file()}
    before = {p: (p.read_bytes(), p.stat().st_ino) for p in n.metadata.iterdir() if p.is_file()}
    result = retirement._capture_histories(n.selected, metadata, private={'generation': 'a' * 32})
    assert set(result['kits']) == {record['backup']}
    assert set(result['metadata']) == {'.source-binding-before-' + record['nonce'] + '-' + str(i)
        for i in range(len(record['bindings']))}
    assert {p: (p.read_bytes(), p.stat().st_ino) for p in before} == before
    assert retirement.validate_history(result, owner_uid=n.f.bridge.owner.pw_uid,
        owner_gid=n.f.bridge.owner.pw_gid, runtime=runtime(n)) == result


@pytest.mark.parametrize('kind', ['file', 'directory', 'kit'])
def test_unknown_source_prefixed_nodes_are_never_archive_authority(publication, kind):
    n = publication
    helper.publish_kit(n.selected)
    unknown = (n.kit.parent if kind == 'kit' else n.metadata) / ('.source-kit-' + 'f' * 32 if kind == 'kit' else '.source-unknown')
    if kind == 'file': unknown.write_bytes(b'INVENTED_PRIVATE_FILE_MUST_BE_PRESERVED'); unknown.chmod(0o600)
    else: unknown.mkdir(mode=0o700)
    inode = unknown.stat().st_ino
    with pytest.raises(c.CustodyError, match='namespace-unbound'):
        retirement._capture_histories(n.selected, current_metadata(n), private={'generation': 'a' * 32})
    assert unknown.exists() and unknown.stat().st_ino == inode


@pytest.mark.parametrize('fault', ['backup-bytes', 'backup-inode', 'ledger-nonce', 'symlink'])
def test_known_history_drift_or_link_refuses_without_renaming(publication, fault):
    n = publication
    helper.publish_kit(n.selected)
    record_path = n.metadata / 'source-publication.json'
    record = json.loads(record_path.read_bytes())
    target = n.kit.parent / record['backup'] / 'broker.py'
    if fault == 'backup-bytes': target.write_bytes(b'changed historical Root code')
    elif fault == 'backup-inode':
        replacement = target.with_name('replacement')
        replacement.write_bytes(target.read_bytes()); replacement.chmod(0o755)
        os.replace(replacement, target)
    elif fault == 'ledger-nonce':
        record['nonce'] = 'f' * 32; record_path.write_bytes(c._native_encoded(record))
    else:
        target.unlink(); target.symlink_to(n.kit / 'broker.py')
    before = set(n.metadata.iterdir()), set(n.kit.parent.iterdir())
    with pytest.raises((ValueError, c.CustodyError, OSError)):
        retirement._capture_histories(n.selected, current_metadata(n), private={'generation': 'a' * 32})
    assert (set(n.metadata.iterdir()), set(n.kit.parent.iterdir())) == before


def test_actual_installer_rebind_history_preserves_previous_generation_and_kit(restored):
    n = restored
    result = helper.rebind_installer_generation(n.selected, transaction=n.transaction, receipt=n.receipt)
    assert result['status'] == 'inactive'
    inventory = c.native_retirement_inventory(n.selected)
    before = {p: p.stat().st_ino for p in n.metadata.iterdir()}
    value = retirement.add_history(n.selected, inventory)
    rebound = json.loads((n.metadata / 'source-installer-rebind.json').read_bytes())
    assert rebound['backup'] in value['history']['kits']
    assert n.receipt['archive']['name'] in value['history']['metadata']
    assert 'source-environment-generation-' + n.receipt['withdrawal'] + '.json' in value['history']['metadata']
    assert {p: p.stat().st_ino for p in before} == before
