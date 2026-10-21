"""Producer receipts bridge a real stage/old-bind rollback/uninstall boundary."""
import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest
from native_config_volume_fixture import KEY, NAME, StorageEngine, mapping
from test_uninstall_compose_volumes import FakeDocker, MODULE as uninstall

SPEC = importlib.util.spec_from_file_location('native_config_volume',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-config-volume.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
pytestmark = pytest.mark.skipif(not hasattr(os, 'getuid') or os.getuid() == 0,
    reason='requires an unprivileged POSIX owner')


def setup(tmp_path, existing=False):
    root = tmp_path / 'ods'
    root.mkdir()
    engine = StorageEngine(root, existing=existing)
    document = mapping({'services': {}})
    return root, engine, document, root / module.RECORD


def test_producer_receipt_precedes_create_and_survives_reuse_without_adopting_existing(tmp_path):
    root, engine, document, receipt = setup(tmp_path)
    def docker(*args, **kwargs):
        if args[:2] == ('volume', 'create'):
            pending = json.loads(receipt.read_bytes())
            assert pending['state'] == 'creating' and pending['preexisting'] is False
            assert module.LABEL + '=' + pending['nonce'] in args
        return engine(*args, **kwargs)
    record = module.ensure(docker, document, root)
    assert record == json.loads(receipt.read_bytes()) and record['state'] == 'ready'
    original = receipt.read_bytes()
    assert module.ensure(docker, document, root) == record
    assert receipt.read_bytes() == original
    assert sum(args[:2] == ('volume', 'create') for args in engine.calls) == 1


def test_preexisting_standard_volume_is_used_without_retroactive_custody(tmp_path):
    root, engine, document, receipt = setup(tmp_path, existing=True)
    before = copy.deepcopy(engine.volume)
    assert module.ensure(engine, document, root) is None
    assert engine.volume == before and not receipt.exists()
    assert not any(args[:2] == ('volume', 'create') for args in engine.calls)


def test_receipt_from_another_installation_cannot_be_bypassed_as_preexisting_storage(tmp_path):
    root, engine, document, receipt = setup(tmp_path, existing=True)
    engine.volume['Labels'][module.LABEL] = 'a' * 32
    with pytest.raises(ValueError, match='producer-receipt-required'):
        module.ensure(engine, document, root)
    assert not receipt.exists()


@pytest.mark.parametrize('fault', ['external', 'custom-name', 'driver-options', 'driver', 'alias',
    'missing-writer-mount', 'bind-mount', 'writable-ingress', 'subpath', 'duplicate-target'])
def test_unowned_mapping_never_reaches_docker_or_creates_a_receipt(tmp_path, fault):
    root, engine, document, receipt = setup(tmp_path)
    definition = document['volumes'][KEY]
    if fault == 'external': definition['external'] = True
    if fault == 'custom-name': definition['name'] = 'operator-owned'
    if fault == 'driver-options': definition['driver_opts'] = {'device': '/foreign'}
    if fault == 'driver': definition['driver'] = 'nfs'
    if fault == 'alias': document['volumes']['foreign'] = {'name': NAME}
    mounts = document['services']['pixel-native-ingress']['volumes']
    if fault == 'missing-writer-mount': document['services']['pixel-native-config-write']['volumes'] = []
    if fault == 'bind-mount': mounts[0]['type'] = 'bind'
    if fault == 'writable-ingress': mounts[0]['read_only'] = False
    if fault == 'subpath': mounts[0]['volume'] = {'subpath': 'foreign'}
    if fault == 'duplicate-target': mounts.append(copy.deepcopy(mounts[0]))
    with pytest.raises(ValueError, match='standard-mapping'):
        module.ensure(engine, document, root)
    assert engine.calls == [] and not receipt.exists()


@pytest.mark.parametrize('fault', ['timeout', 'racing-creator'])
def test_ambiguous_creation_retains_pending_evidence_and_cannot_be_replayed_or_purged(tmp_path, monkeypatch, fault):
    root, engine, document, receipt = setup(tmp_path)
    engine.create_failure = fault == 'timeout'
    engine.raced_create = fault == 'racing-creator'
    with pytest.raises((ValueError, TimeoutError)):
        module.ensure(engine, document, root)
    pending = receipt.read_bytes()
    assert json.loads(pending)['state'] == 'creating' and engine.volume is not None
    engine.calls.clear()
    with pytest.raises(ValueError, match='requires-review'):
        module.ensure(engine, document, root)
    assert engine.calls == [] and receipt.read_bytes() == pending
    with pytest.raises(ValueError, match='requires review'):
        uninstall.native_config_volume_provenance(root, 'ods', {NAME: engine.volume})


@pytest.mark.parametrize('fault', ['foreign-project', 'foreign-root', 'foreign-service'])
def test_preexisting_volume_with_foreign_consumer_never_reaches_config_writer(tmp_path, fault):
    root, engine, document, receipt = setup(tmp_path, existing=True)
    labels = {'com.docker.compose.project': 'ods', 'com.docker.compose.project.working_dir': str(root),
        'com.docker.compose.service': 'pixel-native-ingress'}
    field = {'foreign-project': 'com.docker.compose.project',
        'foreign-root': 'com.docker.compose.project.working_dir',
        'foreign-service': 'com.docker.compose.service'}[fault]
    labels[field] = 'foreign'
    engine.consumers = [{'Id': 'a' * 64, 'Config': {'Labels': labels}}]
    with pytest.raises(ValueError, match='foreign-consumer'):
        module.ensure(engine, document, root)
    assert not receipt.exists()


def retained_uninstall(tmp_path, monkeypatch):
    root, engine, document, receipt = setup(tmp_path)
    module.ensure(engine, document, root)
    # Old-bind rollback leaves no consumer for the new config volume. Use the
    # actual producer output as input to the independent uninstall boundary.
    fake = FakeDocker(root)
    fake.volumes = {NAME: engine.volume}
    fake.containers = []
    monkeypatch.setattr(uninstall, 'docker', fake)
    snapshot = tmp_path / 'uninstall.json'
    return root, fake, receipt, snapshot


def test_retained_new_volume_is_purged_from_producer_custody_without_a_container(tmp_path, monkeypatch):
    root, fake, receipt, snapshot = retained_uninstall(tmp_path, monkeypatch)
    uninstall.preflight(root, snapshot, [])
    assert json.loads(snapshot.read_bytes())['volumes'] == {NAME: uninstall.fingerprint(fake.volumes[NAME])}
    uninstall.complete(root, snapshot)
    assert fake.removed == [NAME] and fake.volumes == {} and receipt.exists()


@pytest.mark.parametrize('fault', ['no-receipt', 'nonce', 'fingerprint', 'root', 'project', 'key',
    'pending', 'preexisting', 'mode', 'symlink', 'hardlink', 'ancestor', 'consumer', 'custom-driver'])
def test_uninstall_rejects_unproven_or_changed_storage_without_removing_anything(tmp_path, monkeypatch, fault):
    root, fake, receipt, snapshot = retained_uninstall(tmp_path, monkeypatch)
    value = json.loads(receipt.read_bytes())
    if fault in ('nonce', 'root', 'project', 'key', 'pending', 'preexisting', 'fingerprint'):
        if fault == 'nonce': value['nonce'] = 'f' * 32
        if fault == 'root': value['installDir'] = str(tmp_path / 'other')
        if fault == 'project': value['project'] = 'foreign'
        if fault == 'key': value['key'] = 'foreign'
        if fault == 'pending': value['state'] = 'creating'
        if fault == 'preexisting': value['preexisting'] = True
        if fault == 'fingerprint': value['fingerprint']['CreatedAt'] = 'different'
        receipt.write_text(json.dumps(value))
    if fault == 'no-receipt': receipt.unlink()
    if fault == 'mode': receipt.chmod(0o644)
    if fault == 'symlink':
        original = receipt.with_suffix('.original')
        receipt.rename(original)
        receipt.symlink_to(original)
    if fault == 'hardlink': os.link(receipt, receipt.with_suffix('.linked'))
    if fault == 'ancestor': receipt.parent.chmod(0o777)
    if fault == 'consumer': fake.foreign_consumers[NAME] = ['b' * 64]
    if fault == 'custom-driver': fake.volumes[NAME]['Driver'] = 'foreign'
    with pytest.raises((ValueError, OSError)):
        uninstall.preflight(root, snapshot, [])
    assert fake.removed == [] and NAME in fake.volumes


def test_external_volume_remains_external_even_with_exact_producer_receipt(tmp_path, monkeypatch):
    root, fake, receipt, snapshot = retained_uninstall(tmp_path, monkeypatch)
    fake.config['volumes'] = {KEY: {'name': NAME, 'external': True}}
    uninstall.preflight(root, snapshot, [])
    uninstall.complete(root, snapshot)
    assert fake.removed == [] and NAME in fake.volumes


def test_failed_nonforce_cleanup_preserves_custody_and_changed_replacement_is_refused(tmp_path, monkeypatch):
    root, fake, receipt, snapshot = retained_uninstall(tmp_path, monkeypatch)
    uninstall.preflight(root, snapshot, [])
    original = receipt.read_bytes()
    def docker(root, *args):
        if args[:2] == ('volume', 'rm'):
            assert args == ('volume', 'rm', NAME)
            raise ValueError('volume busy')
        return fake(root, *args)
    monkeypatch.setattr(uninstall, 'docker', docker)
    with pytest.raises(ValueError, match='volume busy'):
        uninstall.complete(root, snapshot)
    assert receipt.read_bytes() == original and NAME in fake.volumes
    fake.volumes[NAME]['Labels'][module.LABEL] = 'e' * 32
    with pytest.raises(ValueError, match='requires review'):
        uninstall.preflight(root, snapshot, [])
    assert fake.removed == []
