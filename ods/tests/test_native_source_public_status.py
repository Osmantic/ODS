"""Status formatting over the fixed native reader, not a Darwin custody claim.

The Source reader's sealed FD boundary is qualified separately. These tests
prove that private reader inputs cannot become public status or fake a live
connection while its generation needs recovery. Public platform guards stay on.
"""
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import sys

import pytest

if sys.platform != 'linux' or os.geteuid() != 0:
    pytest.skip('temporary real Root-owned status files required', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pixel_connections import custody, source_backend, state, status  # noqa: E402
from pixel_connections.custody import NativeSourcePaths  # noqa: E402
from test_pixel_connection_status import NOW, broker, record  # noqa: E402


@pytest.fixture
def native_status(tmp_path, monkeypatch):
    class Bridge:
        def __init__(self):
            self.state = tmp_path / 'access'
            self.state.mkdir(mode=0o700)
            self.events = []
        @contextmanager
        def bounded(self, seconds):
            self.events.append(('bounded', seconds))
            yield
        @contextmanager
        def locked(self):
            self.events.append(('locked',))
            yield
        def discover(self):
            self.events.append(('discover',))
    bridge = Bridge()
    config = tmp_path / 'portal-connections.json'
    monkeypatch.setattr(state, 'PATH', config)
    monkeypatch.setattr(custody, 'NATIVE_STATE', tmp_path / 'broker')
    monkeypatch.setattr(custody, 'NATIVE_KIT', tmp_path / 'kit')
    monkeypatch.setattr(custody, 'NATIVE_ENV', tmp_path / 'broker/source-broker.env')
    paths = NativeSourcePaths(state=tmp_path / 'broker', install=tmp_path / 'kit',
        env=tmp_path / 'broker/source-broker.env', kit=tmp_path / 'kit')
    view = {'paths': paths, 'brokerUid': None, 'tokenIdentity': None, 'statePresent': False,
        'dispatchReady': False, 'rawStatus': None, 'reason': None}
    def read(selected):
        assert selected is bridge
        assert bridge.events[-3:] == [('bounded', 20), ('locked',), ('discover',)]
        bridge.events.append(('fixed-reader',))
        return dict(view)
    monkeypatch.setattr(source_backend, 'native_status_projection', read)
    return bridge, config, view


def save(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o640)


def assert_no_private_reader_fields(value):
    for name in ('paths', 'brokerUid', 'tokenIdentity', 'rawStatus', 'dispatchReady', 'statePresent'):
        assert name not in value
    status.validate_public(value)


def test_preconnection_native_status_does_not_provision_source(native_status):
    bridge, config, view = native_status
    value = status.native_report(bridge, now=NOW)
    assert value['surface'] == 'darwin' and value['available'] is True
    assert value['connectors']['google'] == status.not_connected()
    assert not config.exists() and not view['paths'].state.exists()
    assert_no_private_reader_fields(value)


def test_native_status_uses_fixed_fd_projection_and_token_metadata(native_status, monkeypatch):
    bridge, config, view = native_status
    save(config, record())
    view.update(brokerUid=60001, tokenIdentity=(1, 2, 100, 3, 4, 60001, 60001, 0o600, 1),
        statePresent=True, dispatchReady=True, rawStatus=json.dumps(broker()).encode())
    monkeypatch.setattr(status, 'read_broker_status', lambda *_a: pytest.fail('native status must not use systemd file reader'))
    value = status.native_report(bridge, now=NOW)
    assert value['connectors']['google']['state'] == 'connected'
    assert value['connectors']['google']['sources']['calendar']['recordCount'] == 12
    assert value['reason'] is None
    assert not view['paths'].state.exists()  # No source provisioning or token opening.
    assert_no_private_reader_fields(value)


def test_inactive_generation_cannot_use_stale_projection_as_connection_proof(native_status):
    bridge, config, view = native_status
    save(config, record())
    view.update(brokerUid=60001, tokenIdentity=(1, 2), statePresent=True,
        dispatchReady=False, rawStatus=None, reason='connection-admission-held')
    value = status.native_report(bridge, now=NOW)
    assert value['reason'] == 'connection-admission-held'
    assert value['connectors']['google']['state'] == 'first-sync'
    assert value['connectors']['google']['checkedAt'] is None
    assert_no_private_reader_fields(value)


def test_missing_token_metadata_never_uses_projection_to_claim_sign_in(native_status):
    bridge, config, view = native_status
    save(config, record())
    view.update(brokerUid=60001, statePresent=True, dispatchReady=True,
        rawStatus=json.dumps(broker()).encode())
    value = status.native_report(bridge, now=NOW)
    assert value['connectors']['google']['state'] == 'needs-reconnect'
    assert value['connectors']['google']['reason'] == 'sign-in-missing'
    assert_no_private_reader_fields(value)


def test_native_status_refuses_failed_custody_before_reading_owner_record(native_status, monkeypatch):
    bridge, _config, _view = native_status
    def refuse(_bridge):
        raise ValueError('invented custody drift')
    monkeypatch.setattr(source_backend, 'native_status_projection', refuse)
    monkeypatch.setattr(state, 'read', lambda *_a, **_k: pytest.fail('no record read after failed native proof'))
    with pytest.raises(ValueError, match='custody drift'):
        status.native_report(bridge, now=NOW)


def test_ordinary_report_cannot_enable_native_paths_from_surface_name(native_status):
    _bridge, config, _view = native_status
    save(config, record())
    assert status.report(surface='darwin', record_path=config)['available'] is False


@pytest.mark.parametrize('system,release,surface', [
    ('Darwin', '26.0.0', 'darwin'),
    ('Linux', '6.8.0', 'linux-systemd'),
    ('Linux', '6.6.87-microsoft-standard-WSL2', 'wsl-systemd'),
])
def test_reviewed_mcp_status_uses_own_observation_and_correct_surface(monkeypatch, system, release, surface):
    # This is the facade's format boundary, not a replacement for the native
    # Observation SDK proof. Reviewed MCP status must not provision Google.
    from pixel_connections import native_public
    bridge = type('Bridge', (), {'bounded': lambda self, _seconds: nullcontext()})()
    controller = object()
    monkeypatch.setattr(native_public.platform, 'system', lambda: system)
    monkeypatch.setattr(native_public.platform, 'release', lambda: release)
    monkeypatch.setattr(native_public, '_controller', lambda selected, app: controller if selected is bridge and app == 'notion' else pytest.fail('wrong reviewed app'))
    observed = []
    class Observation:
        def __init__(self, selected, selected_controller):
            assert selected is bridge and selected_controller is controller
        def status(self):
            observed.append('reviewed-mcp-status')
            return {'app': 'notion', 'phase': 'absent', 'flowId': None, 'error': None,
                'portalAccessVerified': False, 'scope': 'pixel-owner-default'}
    monkeypatch.setattr(native_public, 'Observation', Observation)
    monkeypatch.setattr(source_backend, 'native_status_projection', lambda *_args: pytest.fail('MCP status must not require or provision Google Source'))
    value = native_public.control(bridge, 'connection-app-status', {})
    assert value['surface'] == surface and value['available'] is True
    assert value['apps']['notion']['portalAccessVerified'] is False
    assert observed == ['reviewed-mcp-status']
