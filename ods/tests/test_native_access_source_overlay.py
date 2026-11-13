"""A supported Source overlay can precede any app enrollment journal.

The renderer and source release are real. Only native SDK/provider operations,
gateway processes and privileged custody are simulated in private owner state.
"""
import sys
from types import SimpleNamespace
if sys.platform != 'linux':
    from unittest import SkipTest
    raise SkipTest('Ordinary WSL/Linux Source and Root fixtures')

from datetime import datetime, timezone
import hashlib
import json
import os

import pytest

import test_pixel_source_overlay as source
from pixel_connections import coordinator as c, native_access, native_apps, native_enrollment, native_mcp as native
from pixel_connections import native_public, state


@pytest.fixture(autouse=True)
def installer_umask():
    before = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(before)


def test_legacy_canonical_source_overlay_is_configured_not_ready_until_readonly_check(tmp_path, monkeypatch):
    chosen = native_apps.answers({'notion': True})
    f = source.make_held_overlay(tmp_path, monkeypatch, provider='local', native_mcp_apps=chosen)
    record = tmp_path / 'etc-ods/portal-connections.json'
    source.save(record, {'schemaVersion': 1, 'kind': 'ods-portal-connections', 'updatedAt': '2026-10-28T12:00:00Z',
        'ownerTimeZone': None, 'connectors': {}, 'nativeApps': {'notion': True}})
    monkeypatch.setattr(state, 'PATH', record)
    monkeypatch.setattr(c, 'RECORD', record)
    monkeypatch.setattr(c, 'OWNERS', (0, os.getuid()))
    assert f.bridge.model_finish(dict(transaction_id=source.TOKEN, outcome='applied'))['status'] == 'released'
    assert f.manager.journal()['phase'] == 'complete'
    assert native_apps.configured_choices(json.loads(f.config.read_bytes())) == {'notion': True}
    # Kernel identity and service processes are simulated by this Source
    # fixture; expose the same proved identity used by a real Root inspection.
    f.bridge.gateway_service = SimpleNamespace(transaction_identity=lambda **kwargs: {
        'pid': f.bridge.native_state['pid'], 'started': 1000, 'boot': '11111111-2222-3333-4444-555555555555'})
    f.bridge.native_state['proof']['pid'] = f.bridge.native_state['pid']
    f.bridge.edge_state['admission_blocked'] = False
    inspect = f.bridge.inspect
    monkeypatch.setattr(f.bridge, 'inspect', lambda: dict(inspect(), configured_mode='full-access', effective_mode='full-access'))
    controller = native_enrollment.Controller(f.bridge, now=lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc))
    assert not controller.path.exists() and not controller.consent.path.exists()
    monkeypatch.setattr(native_public, 'Controller', lambda bridge, app: controller)
    monkeypatch.setattr(f.bridge, 'gateway_installation_binding', lambda **kwargs: {'unit': f.bridge.unit_boundary()})
    monkeypatch.setattr(native_access, 'policy_fingerprint', lambda binding: native.digest('reviewed-sdk-fixture'))
    calls = []

    def binding(_bridge, name):
        assert _bridge is f.bridge and name == 'ods-notion'
        return {'ownerUid': os.getuid(), 'configSha256': hashlib.sha256(f.config.read_bytes()).hexdigest(),
                'packageDir': '/controlled-provider-fixture'}

    def probe(_bridge, name, **kwargs):
        calls.append('probe')
        current = binding(_bridge, name)
        revision = native._verified_runtime(_bridge, current)
        return {'reachable': True, 'permitted': True, 'reason': 'ready', 'scope': 'pixel-owner-default',
                'configSha256': current['configSha256'], 'runtimeRevision': revision}

    monkeypatch.setattr(native, 'bridge_binding', binding)
    monkeypatch.setattr(native, '_check_access_locked', probe)
    get = lambda: native_public.control(f.bridge, 'connection-app-status', {})['apps']['notion']
    initial = get()
    assert initial['phase'] == 'configured' and initial['flowId'] is None and not initial['portalAccessVerified']
    assert calls == []
    paths = [record, f.config, *f.owner_state.glob('*.json'), *f.bridge.state.glob('release-*.json')]
    before = {path: path.read_bytes() for path in paths}
    result = native_public.control(f.bridge, 'connection-app-check', {'app': 'notion'})
    assert result['phase'] == 'ready' and result['flowId'] is None and result['portalAccessVerified']
    assert calls == ['probe'] and {path: path.read_bytes() for path in paths} == before
    assert not controller.path.exists() and not controller.consent.path.exists()
    assert get()['portalAccessVerified'] and calls == ['probe']
