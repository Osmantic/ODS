"""Public reviewed-app facade with real isolated Root/owner transactions.

No installed owner or real account is touched. The native provider is controlled
by the existing fixture; the pinned SDK lane qualifies the native operations.
"""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('POSIX Root/owner fixture runs in ordinary Linux/WSL')

from urllib.parse import parse_qs, urlencode, urlsplit
from types import SimpleNamespace

import pytest

import test_native_enrollment as lifecycle
import pixel_access_bridge as bridge_module
from pixel_access_bridge import AccessError
from pixel_connections import native_access, native_host_api as host, native_public

world = lifecycle.world
owner = lifecycle.owner


@pytest.fixture
def public(owner, monkeypatch):
    owner.policy_stamp = 'reviewed-sdk-fixture'
    monkeypatch.setattr(native_access, 'policy_fingerprint', lambda binding: lifecycle.native.digest(owner.policy_stamp))
    monkeypatch.setattr(owner.bridge, 'gateway_installation_binding', lambda **kwargs: owner.bridge.unit_boundary())
    edge = owner.bridge.edge
    def edge_status(*args, **kwargs):
        value = edge(*args, **kwargs)
        return dict(value, admission_blocked=value['phase'] in ('held', 'interrupted'))
    monkeypatch.setattr(owner.bridge, 'edge', edge_status)
    monkeypatch.setattr(native_public, 'Controller', lambda bridge, app: owner.controller)
    owner.finishes = []
    monkeypatch.setattr(host, '_finish_later', lambda app, flow_id, **kwargs: owner.finishes.append((app, flow_id)))
    def relay(operation, request, **kwargs):
        try:
            return 200, native_public.control(owner.bridge, operation, request)
        except AccessError as error:
            return 409, {'error': error.code}
    owner.relay = relay
    yield owner
    host._close_listener('notion')


def begin(public):
    code, reply = host.action('notion', 'connect', {}, config={}, relay=public.relay)
    assert code == 200 and reply['phase'] == 'consent' and reply['portalAccessVerified'] is False
    redirect = parse_qs(urlsplit(reply['authUrl']).query)['redirect_uri'][0]
    address = redirect + '?' + urlencode({'state': public.auth_state, 'code': 'PRIVATE-native-code'})
    return reply['flowId'], address


def test_host_complete_is_not_ready_until_exact_finish_proves_runtime(public):
    flow, address = begin(public)
    code, authorized = host.action('notion', 'complete', {'flowId': flow, 'address': address}, config={}, relay=public.relay)
    assert code == 200 and authorized['phase'] == 'authorized' and not authorized['portalAccessVerified']
    assert public.finishes == [('notion', flow)]
    assert lifecycle.choice(public) == {'notion': False}
    code, ready = host.action('notion', 'finish', {'flowId': flow}, config={}, relay=public.relay)
    assert code == 200 and ready['phase'] == 'ready' and ready['portalAccessVerified'] is True
    assert ready['scope'] == 'pixel-owner-default' and public.native_calls == ['begin', 'complete', 'probe']
    status_code, current = host.status(config={}, relay=public.relay)
    assert status_code == 200 and current['apps']['notion']['portalAccessVerified'] is True
    assert current['catalog'][0]['tools'] == ['notion-search', 'notion-fetch']
    assert public.native_calls == ['begin', 'complete', 'probe']  # GET does no native/provider work.


def test_reused_sign_in_has_no_new_authorization_address_or_false_ready(public):
    public.authorized = True
    code, reply = host.action('notion', 'connect', {}, config={}, relay=public.relay)
    assert code == 200 and reply['phase'] == 'authorized' and reply['authUrl'] is None
    assert reply['portalAccessVerified'] is False and public.finishes == [('notion', reply['flowId'])]
    assert public.native_calls == ['begin']


def test_wrong_callback_and_duplicate_never_replay_native_exchange(public):
    flow, address = begin(public)
    wrong = address.replace(public.auth_state, 'x' * 43)
    assert host.action('notion', 'complete', {'flowId': flow, 'address': wrong}, config={}, relay=public.relay)[0] == 409
    assert public.native_calls == ['begin']
    assert host.action('notion', 'complete', {'flowId': flow, 'address': address}, config={}, relay=public.relay)[0] == 200
    assert host.action('notion', 'complete', {'flowId': flow, 'address': address}, config={}, relay=public.relay)[0] == 409
    assert public.native_calls == ['begin', 'complete']


def test_withdrawal_disables_and_verifies_before_forgetting_and_cannot_freshly_enable(public):
    flow, address = begin(public)
    assert host.action('notion', 'complete', {'flowId': flow, 'address': address}, config={}, relay=public.relay)[0] == 200
    code, reply = host.action('notion', 'disconnect', {}, config={}, relay=public.relay)
    assert code == 200 and reply['phase'] == 'disconnected' and reply['providerRevoked'] is False
    assert lifecycle.choice(public) == {'notion': False}
    assert public.native_calls == ['begin', 'complete', 'forget']
    assert host.action('notion', 'finish', {'flowId': flow}, config={}, relay=public.relay)[0] == 200
    assert public.native_calls == ['begin', 'complete', 'forget']


@pytest.mark.parametrize('action,body', [('connect', {'url': 'https://other.example/mcp'}),
    ('connect', {'tools': ['write']}), ('disconnect', {'app': 'notion'}),
    ('finish', {'flowId': 'a' * 32, 'transactionId': 'b' * 64})])
def test_public_input_cannot_choose_registry_configuration_or_authority(public, action, body):
    before = public.bridge.config_path.read_bytes()
    assert host.action('notion', action, body, config={}, relay=public.relay)[0] == 400
    assert public.bridge.config_path.read_bytes() == before and public.native_calls == []


def test_finish_keeps_one_deadline_across_recovery_then_activation(monkeypatch, tmp_path):
    clock = [1000.0]
    monkeypatch.setattr(bridge_module, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    bridge = SimpleNamespace(state=tmp_path)
    bridge.bounded = lambda seconds: bridge_module.SystemdAccessBridge.bounded(bridge, seconds)
    flow = 'a' * 32

    class Controller:
        def recover(self, *, flow_id):
            assert flow_id == flow
            with bridge.bounded(300):
                clock[0] += 290
                return {'phase': 'authorized'}

        def activate(self, *, flow_id):
            assert flow_id == flow
            with bridge.bounded(300):
                assert bridge_module.remaining(300) <= 10
                clock[0] += 11
                return {'phase': 'ready'}

    monkeypatch.setattr(native_public, 'Controller', lambda bridge, app: Controller())
    with pytest.raises(AccessError, match='operation-deadline-exceeded'):
        native_public.control(bridge, 'connection-app-finish', {'app': 'notion', 'flowId': flow})
