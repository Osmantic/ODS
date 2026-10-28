"""Exact reviewed-app contracts across the Root, host relay and Edge copies."""
import copy
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
sys.path.insert(0, str(ROOT / 'extensions/services/pixel-edge'))
from pixel_connections import native_contract as wire  # noqa: E402
from pixel_access_protocol import ProtocolError, control_request  # noqa: E402
from pixel_access_relay import CONNECTION_TIMEOUTS, public_connection_action, valid_connection_action  # noqa: E402

SPEC = importlib.util.spec_from_file_location('native_edge_access', ROOT / 'extensions/services/pixel-edge/access_mode.py')
edge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(edge)

FLOW = 'a' * 32


def state(phase='absent'):
    result = {'app': 'notion', 'phase': phase, 'flowId': None if phase == 'absent' else FLOW,
              'error': 'native-app-unavailable' if phase in ('failed', 'configured') else None,
              'portalAccessVerified': phase == 'ready', 'scope': wire.SCOPE}
    if phase == 'ready':
        result['checkedAt'] = '2026-10-28T12:00:00Z'
    if phase == 'disconnected':
        result['providerRevoked'] = False
    return result


def begin(phase='consent'):
    return dict(state(phase), authUrl='https://provider.example/auth?state=private' if phase == 'consent' else None,
                expiresAt='2026-10-28T12:15:00Z')


def test_edge_contract_is_exact_installed_contract_and_is_copied_into_image():
    assert (ROOT / 'bin/pixel_connections/native_contract.py').read_bytes() == (ROOT / 'extensions/services/pixel-edge/native_app_contract.py').read_bytes()
    assert 'COPY native_app_contract.py ./' in (ROOT / 'extensions/services/pixel-edge/Dockerfile').read_text()


@pytest.mark.parametrize('operation,payload', [
    ('connection-app-status', {}), ('connection-app-begin', {'app': 'notion', 'redirectPort': 43123}),
    ('connection-app-complete', {'app': 'notion', 'flowId': FLOW, 'address': 'http://127.0.0.1:43123/?state=x&code=private'}),
    ('connection-app-finish', {'app': 'notion', 'flowId': FLOW}),
    ('connection-app-cancel', {'app': 'notion', 'flowId': FLOW}), ('connection-app-disconnect', {'app': 'notion'}),
    ('connection-app-check', {'app': 'notion'}),
])
def test_exact_actions_cross_every_admission_boundary(operation, payload):
    value = {'operation': operation, 'request': payload}
    assert wire.valid_action(value) and valid_connection_action(value) and edge.valid_connection_action(value)
    assert control_request(value) == value
    assert CONNECTION_TIMEOUTS[operation] == wire.TIMEOUTS[operation]
    for extra in ('url', 'command', 'server', 'configPath', 'transactionId', 'owner', 'tools'):
        changed = copy.deepcopy(value)
        changed['request'][extra] = 'PRIVATE-sentinel'
        assert not wire.valid_action(changed)
        assert not valid_connection_action(changed) and not edge.valid_connection_action(changed)
        with pytest.raises(ProtocolError):
            control_request(changed)


@pytest.mark.parametrize('address', ['https://127.0.0.1:43123/?code=x', 'http://localhost:43123/?code=x',
    'http://127.0.0.1:43123?code=x', 'http://127.0.0.1:43123/path?code=x',
    'http://owner@127.0.0.1:43123/?code=x', 'http://127.0.0.1:43123/?code=x#private',
    'http://127.0.0.1:1/?code=x', 'http://127.0.0.1:43123/', 'http://127.0.0.1:43123/?code=x#', 'http://127.0.0.1:43123/?code=private\n'])
def test_pasted_callback_keeps_exact_loopback_shape(address):
    assert not wire.valid_callback(address)


@pytest.mark.parametrize('phase', wire.PHASES)
def test_public_states_are_content_free_scoped_and_never_infer_ready_from_tokens(phase):
    value = state(phase)
    frame = {'schemaVersion': 1, 'available': True, 'surface': 'linux-systemd', 'reason': None, 'apps': {'notion': value}}
    for project in (wire.public_reply, public_connection_action, edge.public_connection_action):
        assert project('connection-app-status', frame) == frame
        for extra in ('tokens', 'binding', 'step', 'authUrl', 'configSha256', 'record', 'authorizationCode'):
            changed = copy.deepcopy(frame)
            changed['apps']['notion'][extra] = 'PRIVATE-sentinel'
            with pytest.raises(ValueError):
                project('connection-app-status', changed)
    bad = dict(value, portalAccessVerified=phase != 'ready')
    with pytest.raises(ValueError):
        wire.public_state(bad)


@pytest.mark.parametrize('phase', ['consent', 'authorized'])
def test_begin_alone_may_return_its_authorization_address(phase):
    value = begin(phase)
    assert wire.public_reply('connection-app-begin', value) == value
    with pytest.raises(ValueError):
        wire.public_reply('connection-app-complete', value)
    with pytest.raises(ValueError):
        wire.public_reply('connection-app-begin', dict(value, portalAccessVerified=True))


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://provider.example/auth?state=x',
    'https://owner:secret@provider.example/auth?state=x', 'https://provider.example/auth?state=x#private',
    'https://provider.example/auth?state=private\n'])
def test_begin_refuses_unsafe_authorization_addresses(url):
    with pytest.raises(ValueError):
        wire.public_reply('connection-app-begin', dict(begin(), authUrl=url))


def test_disconnection_never_claims_provider_revocation_or_unqualified_readiness():
    for changed in (dict(state('disconnected'), providerRevoked=True), dict(state('absent'), checkedAt=None),
                    dict(state('ready'), scope='all-conversations'), dict(state('failed'), error=None)):
        with pytest.raises(ValueError):
            wire.public_state(changed)
    assert wire.public_reply('connection-app-status', wire.unavailable('darwin'))['apps'] == {}


@pytest.mark.parametrize('operation,phase', [('connection-app-begin', 'ready'), ('connection-app-complete', 'ready'),
    ('connection-app-cancel', 'ready'), ('connection-app-disconnect', 'ready'), ('connection-app-finish', 'authorized')])
def test_valid_state_is_not_authority_for_another_operations_outcome(operation, phase):
    for project in (wire.public_reply, public_connection_action, edge.public_connection_action):
        with pytest.raises(ValueError):
            project(operation, state(phase))
