"""Installer identity across real access transactions with simulated services."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest
from test_access_bridge import FakeBridge
import pixel_access_bridge as access
import pixel_model_coordinator as coordinator
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import access_mode_config


class MarkerBridge(FakeBridge):
    prepare_access_marker = access.SystemdAccessBridge.prepare_access_marker
    finish_access_marker = access.SystemdAccessBridge.finish_access_marker

    def __init__(self, root):
        super().__init__(root)
        self.access_baseline = None
        self.discover()
        self.owner_config()
        self.path = self.home / '.openclaw/openclaw.json'
        config = json.loads(self.path.read_text())
        config['models'] = {'providers': {'ods-gateway': {'models': [{'id': 'current', 'contextWindow': 131072}]}}}
        config['plugins'] = {'entries': {'pixel-ods': {'config': {'modelContextWindow': 131072}}}}
        access.atomic_json(self.path, config)
        folder = self.home / '.config/ods'
        folder.mkdir(parents=True, mode=0o700)
        self.marker = folder / 'pixel-managed.json'
        access.atomic_json(self.marker, {
            'schema_version': 2, 'manager': 'ods', 'state': 'ready',
            'initial_active_state': 'absent', 'install_dir': str(self.install),
            'configuration_sha256': self.config_digest(), 'untouched': 'retained',
        })

    def config_digest(self):
        return coordinator._marker_digest(json.loads(self.path.read_text()))

    def owner_config(self):
        path = self.home / '.openclaw/openclaw.json'
        if not path.exists():
            return super().owner_config()
        config = json.loads(path.read_text())
        if self.mode == 'full-access':
            config, self.access_baseline = access_mode_config.enable(config, self.access_baseline)
        elif self.access_baseline is not None:
            config, _ = access_mode_config.restore(config, self.access_baseline)
            self.access_baseline = None
        access.atomic_json(path, config)

    def worker(self, operation='status', **kwargs):
        result = super().worker(operation, **kwargs)
        result['config_sha256'] = hashlib.sha256(self.path.read_bytes()).hexdigest()
        return result

    def request(self, mode='full-access'):
        return {'mode': mode, 'confirmed': mode == 'full-access', 'revision': self.status()['revision']}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    read = access.private_json
    owner_read = lambda path, uid, maximum=1048576: read(path, os.getuid(), maximum)
    monkeypatch.setattr(access, 'private_json', owner_read)
    monkeypatch.setattr(coordinator, 'private_json', owner_read)
    return MarkerBridge(tmp_path)


def assert_bound(runtime):
    marker = json.loads(runtime.marker.read_text())
    assert marker['configuration_sha256'] == runtime.config_digest()
    assert marker['untouched'] == 'retained'
    assert marker['state'] == 'ready'
    assert runtime.marker.stat().st_mode & 0o777 == 0o600


def test_enable_and_restore_keep_installer_marker_bound(runtime):
    before = runtime.config_digest()
    previous = json.loads(runtime.path.read_text())
    assert runtime.change(runtime.request())['effective_mode'] == 'full-access'
    assert runtime.config_digest() != before
    assert_bound(runtime)
    enabled = json.loads(runtime.path.read_text())
    assert enabled['models'] == previous['models']
    assert enabled['plugins'] == previous['plugins']
    assert runtime.change(runtime.request('sandboxed'))['effective_mode'] == 'sandboxed'
    assert runtime.config_digest() == before
    assert_bound(runtime)


@pytest.mark.parametrize('failure', ['probe', 'release', 'edge-release'])
def test_failed_transition_recovers_with_original_root_snapshot(runtime, failure):
    before = runtime.config_digest()
    runtime.fail = failure
    with pytest.raises(access.AccessError):
        runtime.change(runtime.request())
    assert runtime.pending() is not None
    runtime.fail = None
    assert runtime.change(runtime.request('sandboxed'))['effective_mode'] == 'sandboxed'
    assert runtime.config_digest() == before
    assert_bound(runtime)


def test_drift_before_transition_is_rejected_before_worker_or_lease(runtime):
    config = json.loads(runtime.path.read_text())
    config['unrelated'] = True
    access.atomic_json(runtime.path, config)
    before = runtime.marker.read_bytes()
    with pytest.raises(access.AccessError, match='access-marker-drifted'):
        runtime.change(runtime.request())
    assert runtime.log == []
    assert runtime.marker.read_bytes() == before
    assert runtime.pending() is None


def test_unrelated_change_during_worker_does_not_get_adopted(runtime):
    worker = runtime.worker
    def changed(operation='status', **kwargs):
        result = worker(operation, **kwargs)
        if operation == 'full-access':
            config = json.loads(runtime.path.read_text())
            config['unrelated'] = True
            access.atomic_json(runtime.path, config)
        return result
    runtime.worker = changed
    before = runtime.marker.read_bytes()
    with pytest.raises(access.AccessError, match='access-unrelated-config-changed'):
        runtime.change(runtime.request())
    assert runtime.marker.read_bytes() == before
    assert runtime.native_phase == runtime.edge_phase == 'held'


def test_changed_root_snapshot_is_rejected_on_recovery(runtime):
    runtime.fail = 'probe'
    with pytest.raises(access.AccessError):
        runtime.change(runtime.request())
    before = runtime.marker.read_bytes()
    snapshot = runtime.state / 'access-before.json'
    data = json.loads(snapshot.read_text())
    data['agents']['list'][0]['sandbox']['mode'] = 'non-main'
    access.atomic_json(snapshot, data)
    runtime.fail = None
    with pytest.raises(access.AccessError, match='model-before-changed'):
        runtime.change(runtime.request('sandboxed'))
    assert runtime.marker.read_bytes() == before


def test_legacy_restore_can_only_return_to_already_bound_config(runtime):
    runtime.fail = 'probe'
    with pytest.raises(access.AccessError):
        runtime.change(runtime.request())
    pending = runtime.pending()
    pending.pop('markerBeforeSha')
    access.atomic_json(runtime.state / 'transition.json', pending)
    runtime.fail = None
    assert runtime.change(runtime.request('sandboxed'))['effective_mode'] == 'sandboxed'
    assert_bound(runtime)


def test_verified_file_change_after_probe_keeps_marker_and_admission_closed(runtime):
    original = runtime.verify_held_mode
    def changed(token, mode):
        original(token, mode)
        config = json.loads(runtime.path.read_text())
        config['models']['providers']['ods-gateway']['models'][0]['contextWindow'] = 4096
        access.atomic_json(runtime.path, config)
    runtime.verify_held_mode = changed
    before = runtime.marker.read_bytes()
    with pytest.raises(access.AccessError, match='access-config-changed'):
        runtime.change(runtime.request())
    assert runtime.marker.read_bytes() == before
    assert runtime.native_phase == runtime.edge_phase == 'held'


def test_native_macos_uses_launchd_binding_without_linux_marker(runtime, monkeypatch):
    runtime.started = 1000
    runtime.stopped = False
    runtime.use_launchd_fixture(monkeypatch)
    runtime.marker.unlink()
    runtime.gateway_binding = {'definition': 'protected-launchd-fixture'}
    discover = runtime.discover
    def native_discover(**kwargs):
        discover(**kwargs)
        runtime.surface = 'darwin'
    monkeypatch.setattr(runtime, 'discover', native_discover)
    checks = []
    monkeypatch.setattr(runtime, 'verify_gateway_installation_binding', lambda: checks.append('verified'))
    assert runtime.change(runtime.request())['effective_mode'] == 'full-access'
    assert runtime.change(runtime.request('sandboxed'))['effective_mode'] == 'sandboxed'
    assert len(checks) == 4
    assert not runtime.marker.exists()
    assert not (runtime.state / 'access-before.json').exists()


def test_missing_or_symlink_marker_is_never_created_or_followed(runtime):
    contents = runtime.marker.read_bytes()
    runtime.marker.unlink()
    with pytest.raises(access.AccessError, match='model-marker-missing'):
        runtime.change(runtime.request())
    assert not runtime.marker.exists()
    target = runtime.home / 'untrusted-marker.json'
    target.write_bytes(contents)
    target.chmod(0o600)
    runtime.marker.symlink_to(target)
    with pytest.raises(access.AccessError):
        runtime.change(runtime.request())
    assert target.read_bytes() == contents
    assert runtime.log == []


def legacy_enabled(runtime):
    # Execute the prior release's transition without either new marker hook.
    prepare, finish = runtime.prepare_access_marker, runtime.finish_access_marker
    runtime.prepare_access_marker = lambda *args: None
    runtime.finish_access_marker = lambda *args: None
    runtime.change(runtime.request())
    runtime.prepare_access_marker, runtime.finish_access_marker = prepare, finish
    directory = runtime.path.parent / '.ods-access-mode'
    directory.mkdir(mode=0o700)
    receipt = directory / 'pixel-access-mode.json'
    access.atomic_json(receipt, {'version': 1, 'status': 'full-access',
        'config_path': str(runtime.path), 'config_sha256': hashlib.sha256(runtime.path.read_bytes()).hexdigest(),
        'baseline': runtime.access_baseline})
    return receipt


@pytest.mark.parametrize('mode', ['full-access', 'sandboxed'])
def test_prior_release_full_access_is_migrated_only_after_fresh_proof(runtime, mode):
    legacy_enabled(runtime)
    previous = runtime.marker.read_bytes()
    verify = runtime.verify_held_mode
    def verified(token, selected):
        assert runtime.marker.read_bytes() == previous
        verify(token, selected)
    runtime.verify_held_mode = verified
    assert runtime.change(runtime.request(mode))['effective_mode'] == mode
    assert_bound(runtime)


@pytest.mark.parametrize('change', ['unrelated', 'receipt', 'root-proof', 'boundary'])
def test_legacy_migration_refuses_unverified_or_unrelated_drift(runtime, change):
    receipt = legacy_enabled(runtime)
    before = runtime.marker.read_bytes()
    if change == 'unrelated':
        config = json.loads(runtime.path.read_text())
        config['models']['providers']['ods-gateway']['models'][0]['contextWindow'] = 4096
        access.atomic_json(runtime.path, config)
    elif change == 'receipt':
        receipt.unlink()
    elif change == 'root-proof':
        (runtime.state / 'verified.json').unlink()
    else:
        runtime.dropin.unlink()
    runtime.log.clear()
    with pytest.raises(access.AccessError, match='access-marker-drifted'):
        runtime.change(runtime.request())
    assert runtime.marker.read_bytes() == before
    assert runtime.log == []


def test_root_installer_reproof_preserves_installing_state_and_existing_mode(runtime, monkeypatch):
    legacy_enabled(runtime)
    marker = json.loads(runtime.marker.read_text())
    marker['state'] = 'installing'
    access.atomic_json(runtime.marker, marker)
    with pytest.raises(access.AccessError, match='model-marker-invalid'):
        runtime.change(runtime.request())
    monkeypatch.setattr(access.os, 'geteuid', lambda: 0)
    assert runtime.reprove_installer_access() == {'result': 'reproved', 'mode': 'full-access'}
    after = json.loads(runtime.marker.read_text())
    assert after['state'] == 'installing'
    assert after['configuration_sha256'] == runtime.config_digest()
    assert access._INSTALLER_ACCESS_REPROOF.get() is False
    with pytest.raises(access.AccessError, match='model-marker-invalid'):
        runtime.change(runtime.request())


def test_installer_reproof_refuses_nonroot_and_pending_transactions(runtime, monkeypatch):
    monkeypatch.setattr(access.os, 'geteuid', lambda: 1000)
    with pytest.raises(access.AccessError, match='root-installer-required'):
        runtime.reprove_installer_access()
    monkeypatch.setattr(access.os, 'geteuid', lambda: 0)
    runtime.fail = 'probe'
    with pytest.raises(access.AccessError):
        runtime.change(runtime.request())
    with pytest.raises(access.AccessError, match='installer-access-recovery-required'):
        runtime.reprove_installer_access()
    assert access._INSTALLER_ACCESS_REPROOF.get() is False


def test_installer_reproof_is_not_a_public_protocol_operation():
    from pixel_access_protocol import control_request, ProtocolError
    for operation in ('reprove-installer-access', 'reprove_installer_access'):
        with pytest.raises(ProtocolError):
            control_request({'operation': operation})
