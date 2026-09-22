"""Owner-side service snapshot qualification; never starts a daemon."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


SPEC = importlib.util.spec_from_file_location('service_bundle_config',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-config.py')
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


@pytest.mark.parametrize('fault', [None, 'root', 'platform', 'existing', 'receipt-type',
    'receipt-ref', 'receipt-public', 'policy-bool', 'policy-type', 'broker-ref',
    'syntax', 'symlink', 'hardlink', 'public-config'])
def test_service_bundle_is_private_version_bound_and_nonactivating(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(config.sys, 'platform', 'linux' if fault == 'platform' else 'darwin')
    monkeypatch.setattr(config.os, 'geteuid', lambda: 0 if fault == 'root' else 501)
    monkeypatch.setattr(config.bootstrap, 'selected_release', lambda *args: {})
    monkeypatch.setattr(config, 'service_catalog', lambda *args: b'{"schemaVersion":1}')
    source, ods, candidate = [tmp_path / name for name in ('pixel', 'ods', 'candidate')]
    for path in (source, ods, candidate): path.mkdir()
    broker = source / 'deploy/ops-broker/broker.py'
    broker.parent.mkdir(parents=True)
    broker.write_bytes(b'print("broker")\n')
    monkeypatch.setattr(config.subprocess, 'run', lambda *args, **kwargs:
        subprocess.CompletedProcess(args[0], 0, b'wrong' if fault == 'broker-ref' else broker.read_bytes(), b''))
    for relative in set(config.SERVICE_SOURCES.values()):
        path = ods / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'VALUE = 1\n')
    receipt = {'pixelSourceRef': 'a' * 40, 'status': 'staged', 'requiresServiceQualification': True}
    if fault == 'receipt-type': receipt = []
    if fault == 'receipt-ref': receipt['pixelSourceRef'] = 'b' * 40
    policy = {'schemaVersion': True if fault == 'policy-bool' else 1}
    for name, value in [('candidate.json', receipt), ('operations-policy.json',
        [] if fault == 'policy-type' else policy), ('openclaw.json', {'secret': 'fixture-private-key'})]:
        path = candidate / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    if fault == 'receipt-public': (candidate / 'candidate.json').chmod(0o644)
    if fault == 'public-config': (candidate / 'openclaw.json').chmod(0o644)
    helper = ods / config.SERVICE_SOURCES['manager/extension_manager.py']
    if fault == 'syntax': helper.write_bytes(b'def invalid(\n')
    if fault == 'symlink':
        helper.unlink()
        helper.symlink_to(broker)
    if fault == 'hardlink': os.link(helper, tmp_path / 'alias.py')
    destination = tmp_path / 'services'
    if fault == 'existing': destination.mkdir()
    def run():
        return config.stage_services(source=source, ref='a' * 40, ods_source=ods,
            candidate=candidate, destination=destination)
    if fault:
        with pytest.raises((ValueError, OSError, SyntaxError)): run()
        assert not destination.exists() or fault == 'existing' and not list(destination.iterdir())
    else:
        digest = run()
        manifest_body = (destination / 'services.json').read_bytes()
        manifest = json.loads(manifest_body)
        assert hashlib.sha256(manifest_body).hexdigest() == digest
        assert manifest['requiresServiceQualification'] is True
        assert manifest['candidateConfigSha256'] == hashlib.sha256((candidate / 'openclaw.json').read_bytes()).hexdigest()
        assert set(manifest['files']) == set(config.SERVICE_SOURCES) | {'operations/broker.py', 'operations/policy.json', 'helpers/extension-catalog.json'}
        for name, record in manifest['files'].items():
            path = destination / name
            assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256']
            assert path.stat().st_size == record['bytes']
            assert path.stat().st_mode & 0o777 == 0o600
            assert b'fixture-private-key' not in path.read_bytes()
        assert not (destination / 'openclaw.json').exists()
        assert destination.stat().st_mode & 0o777 == 0o700
        approved = dict(expected_digest=digest, expected_ref='a' * 40,
            expected_config_digest=manifest['candidateConfigSha256'])
        snapshots = config.verified_services(destination, **approved)
        assert set(snapshots) == set(manifest['files'])
        original = (destination / 'manager/extension_manager.py').read_bytes()
        (destination / 'manager/extension_manager.py').write_bytes(b'VALUE = 2\n')
        with pytest.raises(ValueError, match='file-digest-mismatch'):
            config.verified_services(destination, **approved)
        assert snapshots['manager/extension_manager.py'] == original
        (destination / 'manager/extension_manager.py').write_bytes(original)
        for field in ('expected_digest', 'expected_ref', 'expected_config_digest'):
            changed = dict(approved)
            changed[field] = 'f' * len(changed[field])
            with pytest.raises(ValueError): config.verified_services(destination, **changed)
        (destination / 'services.json').write_bytes(manifest_body + b' ')
        with pytest.raises(ValueError, match='manifest-digest-mismatch'):
            config.verified_services(destination, **approved)
    assert not list(tmp_path.glob('.pixel-services-*'))
