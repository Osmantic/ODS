"""Admitted current owner images, real FDs and strict drift; no Mac/provider."""
# ruff: noqa: E402,F401,F811
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

if sys.platform != 'linux':
    pytest.skip('Linux metadata/OS-proof driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_backend import (backend, broker_file, executor, qualification_operation,
    generation, built, source, package, native, deployment, material, publication, helper)
from pixel_connections import custody as c, coordinator, state


def image(path):
    raw, info = path.read_bytes(), path.stat()
    return {'inode': [info.st_dev, info.st_ino], 'sha256': hashlib.sha256(raw).hexdigest()}


def test_connected_current_snapshot_preserves_stale_bootstrap_as_history_and_refuses_drift(backend, monkeypatch):
    n = backend
    owner = n.f.bridge.owner
    sidefiles = {'gateway.env': n.f.env, 'onboarding.json': n.f.answers}
    # Genuine original bootstrap receipt before later admitted connection writes.
    bootstrap = {'schemaVersion': 1, 'kind': 'ods-native-connection-sidefiles',
        'ownerUid': owner.pw_uid, 'ownerGid': owner.pw_gid, 'configPath': str(n.f.config),
        'bundleSha256': n.f.value.document['bundleSha256'],
        'files': {name: image(path)['sha256'] for name, path in sidefiles.items()},
        'parent': [n.f.config.parent.stat().st_dev, n.f.config.parent.stat().st_ino],
        'children': {name: {'inode': image(path)['inode'], 'ready': True} for name, path in sidefiles.items()}}
    receipt_path = n.metadata / 'source-sidefiles.json'
    receipt_path.write_bytes(c._native_encoded(bootstrap))
    receipt_path.chmod(0o600)
    bootstrap_image = image(receipt_path)
    record = {'schemaVersion': 1, 'kind': state.KIND, 'updatedAt': '2026-11-04T00:00:00Z',
        'ownerTimeZone': 'UTC', 'connectors': {'google': {'status': 'connected', 'account': 'fixture@example.com',
            'calendarId': 'primary', 'gmailWindowDays': 7, 'cloudModels': False,
            'connectedAt': '2026-11-04T00:00:00Z',
            'capabilities': {'calendar.read': False, 'calendar.write': False, 'calendar.direct': False,
                'gmail.read': True, 'gmail.drafts': False}}}}
    state.validate(record)
    record_path = n.metadata / 'portal-connections.json'
    record_path.write_bytes(c._native_encoded(record))
    record_path.chmod(0o644)
    monkeypatch.setattr(c, 'NATIVE_CONNECTION_RECORD', record_path)
    broker_file(n, 'private/google-token.json', b'INVENTED_TOKEN_NOT_READ')
    target = coordinator.gateway_target(record, custody_held=True)
    answers = state.connection_answers(record, google_custody=True)
    env = c.env_text(c.parse_env(n.f.env.read_text()), {'PIXEL_LIMB_EMAIL_ENABLED': '1'})
    for path, body in ((n.f.env, env.encode()), (n.f.answers, c._native_encoded(dict(answers, modelChoice='preserved-owner-model')))):
        staged = path.with_name('.admitted-' + path.name)
        staged.write_bytes(body)
        staged.chmod(0o600)
        os.replace(staged, path)
    config_sha = image(n.f.config)['sha256']
    def worker(operation, **_kwargs):
        assert operation == 'connection-owner-status'
        return {'pending': False, 'configSha256': config_sha, 'envSha256': image(n.f.env)['sha256'],
            'live': target, 'answers': answers, 'completion': None}
    monkeypatch.setattr(n.f.bridge, 'worker', worker)
    monkeypatch.setattr(n.f.bridge, 'inspect', lambda: {'runtime_verified': True, 'pending': False,
        'configured_mode': 'sandboxed', 'effective_mode': 'sandboxed',
        '_config': {'config_sha256': config_sha}, 'revision': 'e' * 64})
    captured = c.native_installer_sidefiles_snapshot(n.selected)
    snapshot = captured['snapshot']
    assert snapshot['bootstrap'] == bootstrap_image and snapshot['record'] == image(record_path)
    assert snapshot['files']['gateway.env']['inode'] != bootstrap['children']['gateway.env']['inode']
    assert json.loads(captured['sidefiles']['onboarding.json'])['modelChoice'] == 'preserved-owner-model'
    transaction = {'kind': 'native-installer-upgrade', 'transaction': 'b' * 64,
        'currentDigest': n.f.value.document['bundleSha256'], 'candidateDigest': 'a' * 64}
    context = {'runtime_bundle': {'connection_sidefiles': captured['sidefiles'], 'source_sidefiles_before': snapshot}}
    after = c._native_installer_sidefiles(n.f.value.document, context, bootstrap, transaction,
        old_runtime=snapshot['runtime'], before_image=bootstrap_image)
    assert after['files'] == {name: file['sha256'] for name, file in snapshot['files'].items()}
    assert after['children']['gateway.env']['inode'] == snapshot['files']['gateway.env']['inode']
    assert receipt_path.read_bytes() == c._native_encoded(bootstrap)
    # A changed admitted record is not permission to recapture under old intent.
    record_path.write_bytes(record_path.read_bytes() + b' ')
    with pytest.raises(c.CustodyError, match='authority-changed'):
        c._native_installer_sidefiles(n.f.value.document, context, bootstrap, transaction,
            old_runtime=snapshot['runtime'], before_image=bootstrap_image)
    record_path.write_bytes(c._native_encoded(record))
    n.f.env.write_bytes(n.f.env.read_bytes() + b"UNADMITTED='edit'\n")
    with pytest.raises(c.CustodyError, match='sidefiles-changed'):
        c._native_installer_sidefiles(n.f.value.document, context, bootstrap, transaction,
            old_runtime=snapshot['runtime'], before_image=bootstrap_image)
