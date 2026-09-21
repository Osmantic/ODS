import hashlib
import base64
import importlib.util
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('native_finalize',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-finalize.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('fault', [None, 'runtime', 'services', 'storage', 'install', 'pending', 'inactive'])
def test_selection_binds_corrected_services_to_completed_docker_handover(fault):
    prepared = dict(kind='legacy-native', status='prepared', phase='awaiting-joint-activation',
        runtimeDigest='a' * 64, currentDigest='b' * 64, serviceDigest='c' * 64, installDir='/owners/test/ods')
    storage = {'volumes': {'pixel-native-runtime': {'external': True, 'name': 'retained-history'}}}
    digest = hashlib.sha256(json.dumps(storage, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    docker = {**prepared, 'serviceDigest': 'd' * 64, 'storageDigest': digest,
        'environmentStatus': 'configured'}
    journal = {'phase': 'infrastructure-ready', 'requiresRecovery': False}
    proof = {'status': 'active', 'runtimeDigest': prepared['runtimeDigest'], 'serviceDigest': prepared['serviceDigest']}
    if fault == 'runtime': docker['runtimeDigest'] = 'e' * 64
    if fault == 'services': proof['serviceDigest'] = docker['serviceDigest']
    if fault == 'storage': storage['volumes']['other'] = {}
    if fault == 'install': docker['installDir'] = '/other'
    if fault == 'pending': journal['requiresRecovery'] = True
    if fault == 'inactive': proof['status'] = 'restored'
    if fault:
        with pytest.raises(ValueError):
            module.selection_records(prepared, docker, journal, storage, proof)
    else:
        receipt, activation = module.selection_records(prepared, docker, journal, storage, proof)
        assert receipt['serviceDigest'] == activation['serviceDigest'] == prepared['serviceDigest']
        assert receipt['storageDigest'] == activation['storageDigest'] == digest
        assert 'environmentStatus' not in receipt
        assert activation['status'] == 'ready'


@pytest.mark.parametrize('fault', [None, 'pending', 'drift', 'service', 'stopped'])
def test_protected_proof_reads_completed_files_and_checks_live_services(monkeypatch, fault):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
    import pixel_access_bridge
    import pixel_macos_custody
    runtime, services, ref = 'a' * 64, 'b' * 64, 'c' * 40
    body = b'activated'
    completed = {'phase': 'active', 'candidateDigest': runtime, 'files': [{
        'path': '/protected/file', 'after': base64.b64encode(body).decode(),
        'afterSha256': hashlib.sha256(body).hexdigest()}]}
    selection = {'expected_digest': services if fault != 'service' else 'wrong', 'expected_ref': ref}
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module.os.path, 'lexists', lambda path: fault == 'pending')
    monkeypatch.setattr(pixel_access_bridge, 'private_json', lambda path, *args:
        {'selection': selection} if path.name == 'service-installation.json' else completed)
    monkeypatch.setattr(pixel_macos_custody, 'protected_bytes',
        lambda *args, **kwargs: b'changed' if fault == 'drift' else body)
    monkeypatch.setattr(module.pwd, 'getpwnam', lambda owner: SimpleNamespace(pw_uid=501))
    checked = []
    monkeypatch.setattr(module, 'helper', lambda name: SimpleNamespace(
        _verify_new_services=lambda plan: checked.append(plan)))
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs:
        SimpleNamespace(stdout='state = waiting' if fault == 'stopped' else 'state = running\n'))
    if fault:
        with pytest.raises(ValueError): module.protected_proof('owner', runtime, services, ref)
    else:
        assert module.protected_proof('owner', runtime, services, ref)['status'] == 'active'
        assert len(checked) == 1
