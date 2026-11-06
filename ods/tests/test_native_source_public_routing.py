"""Public routing and release gating; native custody is tested independently."""
import ast
import io
import json
from pathlib import Path
import socketserver
import sys
from types import SimpleNamespace

import pytest

if sys.platform == 'win32':
    pytest.skip('POSIX controller imports; no physical Mac claim', allow_module_level=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'bin'), str(ROOT / 'extensions/services/pixel-agent/host')]
import pixel_access_client as client  # noqa: E402
import pixel_access_relay as relay  # noqa: E402
from pixel_access_bridge import AccessError  # noqa: E402
from pixel_access_protocol import control_request, decode_frame  # noqa: E402
from pixel_connections import catalog, google_app, status  # noqa: E402


def absent_native_status():
    value = status.unavailable('darwin')
    value.update(available=True, reason=None)
    return status.validate_public(value)


def release(monkeypatch, enabled):
    monkeypatch.setitem(catalog.GOOGLE, 'platforms', ['linux-systemd', 'wsl-systemd'] + (['darwin'] if enabled else []))


@pytest.mark.parametrize('config', [{}, {'PIXEL_OPENWEBUI_KEY': 'fixture'}])
def test_unreleased_native_routes_never_contact_a_controller(monkeypatch, config):
    release(monkeypatch, False)
    monkeypatch.setattr(relay.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(client, 'request_access', lambda *_a, **_k: pytest.fail('unreleased native socket request'))
    monkeypatch.setattr(relay, '_request_runtime_controller', lambda *_a, **_k: pytest.fail('unreleased native proxy'))
    code, value = relay.request_runtime_connections(config=config)
    assert code == 200 and value['available'] is False and value['surface'] == 'darwin'
    code, value = relay.request_runtime_connection_action('connection-app-status', {}, config=config)
    assert code == 200 and value['available'] is False
    assert relay.request_runtime_connection_action('connection-sync', {}, config=config) == (409, {'error': 'not-available-on-this-system'})


@pytest.mark.parametrize('proxied', [False, True])
def test_declared_native_read_uses_its_actual_root_transport_and_closed_reply(monkeypatch, proxied):
    release(monkeypatch, True)
    monkeypatch.setattr(relay.platform, 'system', lambda: 'Darwin')
    value, calls = absent_native_status(), []
    def local(operation):
        assert not proxied and operation == 'connection-status'
        calls.append('socket')
        return 200, value
    def proxy(request, **kwargs):
        assert proxied and request is None and kwargs['path'] == '/v1/connections' and kwargs['timeout'] == 96
        calls.append('proxy')
        return 200, value
    monkeypatch.setattr(client, 'request_access', local)
    monkeypatch.setattr(relay, '_request_runtime_controller', proxy)
    assert relay.request_runtime_connections(config={'PIXEL_OPENWEBUI_KEY': 'fixture'} if proxied else {}) == (200, value)
    assert calls == ['proxy' if proxied else 'socket']
    value['privatePath'] = '/private/never-export-this'
    with pytest.raises(relay.AccessRelayError, match='connections-unavailable'):
        relay.request_runtime_connections(config={'PIXEL_OPENWEBUI_KEY': 'fixture'} if proxied else {})


def test_native_reviewed_action_does_not_request_google_setup(monkeypatch):
    release(monkeypatch, True)
    monkeypatch.setattr(relay.platform, 'system', lambda: 'Darwin')
    calls = []
    value = {'app': 'notion', 'phase': 'configured', 'flowId': None, 'error': 'portal-access-unverified',
        'portalAccessVerified': False, 'scope': 'pixel-owner-default'}
    def local(operation, request):
        calls.append((operation, request))
        return 200, value
    monkeypatch.setattr(client, 'request_access', local)
    monkeypatch.setattr(google_app, 'public_status', lambda: pytest.fail('reviewed MCP action must not configure Google'))
    assert relay.request_runtime_connection_action('connection-app-check', {'app': 'notion'}, config={}) == (200, value)
    assert calls == [('connection-app-check', {'app': 'notion'})]
    assert relay.request_runtime_connection_action('connection-app-check', {'app': 'notion', 'path': '/other'}, config={})[0] == 400
    assert len(calls) == 1


def handler(request, uid, adapter):
    path = ROOT / 'extensions/services/pixel-agent/host/access_mode_server.py'
    tree = ast.parse(path.read_bytes())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    guard = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'connections_platform_enabled')
    cls = next(n for n in main.body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
    namespace = {'socketserver': socketserver, 'json': json, 'peer_ids': lambda _c: (uid, uid),
        'owner': SimpleNamespace(pw_uid=501), 'control_request': control_request, 'decode_frame': decode_frame,
        'make_adapter': lambda: adapter, 'platform': SimpleNamespace(system=lambda: 'Darwin'), 'AccessError': AccessError}
    exec(compile(ast.Module(body=[guard, cls], type_ignores=[]), str(path), 'exec'), namespace)
    instance = namespace['Handler'].__new__(namespace['Handler'])
    instance.connection = SimpleNamespace(settimeout=lambda _seconds: None)
    instance.rfile, instance.wfile = io.BytesIO(json.dumps(request).encode() + b'\n'), io.BytesIO()
    instance.handle()
    return json.loads(instance.wfile.getvalue())


@pytest.mark.parametrize('operation', ['connection-status', 'connection-agent-status'])
@pytest.mark.parametrize('enabled', [False, True])
def test_native_handler_uses_fixed_native_reader_and_preserves_owner_gate(monkeypatch, operation, enabled):
    release(monkeypatch, enabled)
    adapter, calls = object(), []
    public = absent_native_status() if operation == 'connection-status' else status._agent_empty(status.datetime.now(status.timezone.utc))
    def native(selected):
        assert selected is adapter
        calls.append(operation)
        return public
    monkeypatch.setattr(status, 'native_report', native)
    monkeypatch.setattr(status, 'native_agent_report', native)
    monkeypatch.setattr(status, 'report', lambda **_k: pytest.fail('native read must not choose systemd paths'))
    monkeypatch.setattr(status, 'agent_report', lambda **_k: pytest.fail('native read must not choose systemd agent projection'))
    monkeypatch.setattr(google_app, 'public_status', lambda: {'mode': 'managed', 'state': 'not-configured', 'advancedAvailable': True})
    reply = handler({'operation': operation}, 501, adapter)
    assert reply['status'] == 200 if enabled else reply['status'] in (400, 503)
    assert calls == ([operation] if enabled else [])
    reply = handler({'operation': operation}, 502, adapter)
    assert reply == {'status': 403, 'body': {'error': 'owner-required'}}
    assert calls == ([operation] if enabled else [])
    reply = handler({'operation': operation, 'request': {'path': '/other'}}, 501, adapter)
    assert reply['status'] == 400 and calls == ([operation] if enabled else [])
