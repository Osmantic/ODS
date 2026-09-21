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


@pytest.mark.parametrize('fault', [None, 'current', 'install', 'runtime', 'services', 'pending', 'phase'])
def test_update_retains_storage_and_supports_verified_replay(fault):
    previous = dict(runtimeDigest='a' * 64, serviceDigest='b' * 64,
        storageDigest='retained', home='/owner/home', kind='legacy-native')
    activation = dict(status='ready', phase='services-ready',
        runtimeDigest='a' * 64, serviceDigest='b' * 64, storageDigest='retained')
    prepared = dict(kind='legacy-native', status='prepared', phase='awaiting-joint-activation',
        currentDigest='a' * 64, runtimeDigest='c' * 64, serviceDigest='d' * 64,
        pixelSourceRef='e' * 40, installDir='/owner/ods')
    proof = dict(status='active', runtimeDigest='c' * 64, serviceDigest='d' * 64)
    if fault == 'current': prepared['currentDigest'] = 'e' * 64
    if fault == 'install': prepared['installDir'] = '/other'
    if fault == 'runtime': proof['runtimeDigest'] = 'e' * 64
    if fault == 'services': proof['serviceDigest'] = 'e' * 64
    if fault == 'pending': proof['status'] = 'pending'
    if fault == 'phase': activation['phase'] = 'error'
    if fault:
        with pytest.raises(ValueError):
            module.update_selection_records(previous, activation, prepared, proof, '/owner/ods')
    else:
        result = module.update_selection_records(previous, activation, prepared, proof, '/owner/ods')
        assert result['preparation']['home'] == previous['home']
        assert result['preparation']['pixelSourceRef'] == prepared['pixelSourceRef']
        assert result['preparation']['storageDigest'] == result['activation']['storageDigest'] == 'retained'
        assert result['activation']['runtimeDigest'] == 'c' * 64
        assert module.update_selection_records(result['preparation'], result['activation'],
            prepared, proof, '/owner/ods') == result
        assert previous['runtimeDigest'] == 'a' * 64


@pytest.mark.parametrize('fault', [None, 'proof', 'replace', 'symlink-lock', 'directory-sync'])
def test_update_publication_is_atomic_and_replayable(tmp_path, monkeypatch, fault):
    stack = module.helper('pixel-native-stack')
    installed = tmp_path / 'ods'
    directory = installed / 'data/pixel-native/preparation'
    directory.mkdir(parents=True)
    previous = dict(status='prepared', phase='awaiting-protected-activation',
        runtimeDigest='a' * 64, serviceDigest='b' * 64, home=str(installed / 'data/pixel-native/home'))
    activation = dict(status='ready', phase='services-ready', runtimeDigest='a' * 64, serviceDigest='b' * 64)
    for name, value in [('preparation.json', previous), ('activation.json', activation)]:
        (directory / name).write_text(json.dumps(value))
        (directory / name).chmod(0o600)
    for fragment in stack.installer.FRAGMENTS:
        path = installed / fragment
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('services: {}\n')
    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    prepared = dict(kind='legacy-native', status='prepared', phase='awaiting-joint-activation',
        currentDigest='a' * 64, runtimeDigest='c' * 64, serviceDigest='d' * 64,
        pixelSourceRef='e' * 40, installDir=str(installed))
    (candidate / 'preparation.json').write_text(json.dumps(prepared))
    (candidate / 'preparation.json').chmod(0o600)
    proof = dict(status='active', runtimeDigest='c' * 64, serviceDigest='d' * 64)
    if fault == 'proof': proof['status'] = 'pending'
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    calls = []
    def verify(args, **kwargs):
        calls.append(args)
        assert '--verify-protected' in args
        return SimpleNamespace(stdout=json.dumps(proof))
    monkeypatch.setattr(module.subprocess, 'run', verify)
    if fault == 'replace':
        def fail(*args): raise OSError('fixture publication failure')
        monkeypatch.setattr(module.os, 'replace', fail)
    if fault == 'symlink-lock':
        (directory / '.selection.lock').symlink_to(directory / 'preparation.json')
    if fault == 'directory-sync':
        fsync = module.os.fsync
        interrupted = []
        def interrupt_once(fd):
            if (directory / stack.UPDATE_SELECTION).exists() and not interrupted:
                interrupted.append(True)
                raise OSError('fixture durability interruption')
            return fsync(fd)
        monkeypatch.setattr(module.os, 'fsync', interrupt_once)
        with pytest.raises(OSError): module.finalize_update(candidate)
        assert stack.read_selection(directory)[0]['runtimeDigest'] == 'c' * 64
        assert module.finalize_update(candidate)['status'] == 'selection-ready'
        assert len(calls) == 2
    elif fault:
        with pytest.raises((ValueError, OSError)):
            module.finalize_update(candidate)
        assert not (directory / stack.UPDATE_SELECTION).exists()
        assert stack.read_selection(directory)[0] == previous
    else:
        result = module.finalize_update(candidate)
        assert result['status'] == 'selection-ready'
        assert module.finalize_update(candidate) == result
        assert len(calls) == 2
        assert stack.read_selection(directory)[0]['runtimeDigest'] == 'c' * 64
        assert (directory / stack.UPDATE_SELECTION).stat().st_mode & 0o777 == 0o600
    assert json.loads((directory / 'preparation.json').read_text()) == previous
    assert json.loads((directory / 'activation.json').read_text()) == activation
    assert not list(directory.glob('.selection-*'))


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
