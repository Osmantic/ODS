"""Reviewed apps: exact owner-authenticated API, scoped proof and no configuration input."""
import copy

from host_agent_client import AgentHTTPError
import pytest

import test_pixel_connections as fixtures
from test_pixel_connections import AUTH, flag
from pixel_connections import native_apps, native_contract as wire

client = fixtures.client
mock_request = fixtures.mock_request

FLOW = 'a' * 32
BASE = '/api/pixel/connections/apps'


def state(phase='absent'):
    value = {'app': 'notion', 'phase': phase, 'flowId': None if phase == 'absent' else FLOW,
             'error': None, 'portalAccessVerified': phase == 'ready', 'scope': wire.SCOPE}
    if phase == 'ready':
        value['checkedAt'] = '2026-10-28T12:00:00Z'
    if phase == 'disconnected':
        value['providerRevoked'] = False
    return value


def status():
    return {'schemaVersion': 1, 'available': True, 'surface': 'wsl-systemd', 'reason': None,
            'apps': {'notion': state()}, 'catalog': native_apps.public_catalog()}


@pytest.mark.parametrize('name,body', [('connect', {}), ('complete', {'flowId': FLOW,
    'address': 'http://127.0.0.1:43123/?state=x&code=PRIVATE-code'}), ('finish', {'flowId': FLOW}),
    ('cancel', {'flowId': FLOW}), ('disconnect', {}), ('check', {})])
def test_every_native_action_requires_owner_and_rejects_caller_configuration(client, mock_request, name, body):
    path = BASE + '/notion/' + name
    assert client.post(path, json=body).status_code == 401
    for key in ('app', 'url', 'server', 'tools', 'owner', 'configPath', 'transactionId', 'client'):
        assert client.post(path, headers=AUTH, json=dict(body, **{key: None})).status_code == 422
    assert client.post(BASE + '/arbitrary/' + name, headers=AUTH, json=body).status_code == 422
    mock_request.assert_not_awaited()


def test_reviewed_status_is_independent_owner_only_and_preserves_exact_catalog(client, mock_request):
    assert client.get(BASE).status_code == 401
    mock_request.return_value = status()
    response = client.get(BASE, headers=AUTH)
    assert response.status_code == 200 and response.json() == status()
    assert response.headers['cache-control'] == 'no-store'
    mock_request.assert_awaited_once_with('GET', '/v1/pixel/connections/apps', timeout=30)


@pytest.mark.parametrize('change', [lambda v: v['apps']['notion'].update(tokens='PRIVATE-token'),
    lambda v: v['apps']['notion'].update(portalAccessVerified=True),
    lambda v: v['apps']['notion'].update(scope='all-conversations'),
    lambda v: v['apps']['notion'].update(checkedAt=None),
    lambda v: v['catalog'][0].update(server='PRIVATE-command'),
    lambda v: v['catalog'][0].update(tools=['notion-search', 'notion-write']),
    lambda v: v.update(apps={'unreviewed': state()})])
def test_private_or_unqualified_status_is_refused_without_echoing_it(client, mock_request, change):
    value = status()
    change(value)
    mock_request.return_value = value
    response = client.get(BASE, headers=AUTH)
    assert response.status_code == 503
    assert 'PRIVATE' not in response.text and 'all-conversations' not in response.text


@pytest.mark.parametrize('phase,auth_url', [('consent', 'https://provider.example/auth?state=private'), ('authorized', None)])
def test_connect_preserves_reused_authorization_without_claiming_ready(client, mock_request, phase, auth_url):
    reply = dict(state(phase), authUrl=auth_url, expiresAt='2026-10-28T12:15:00Z')
    mock_request.return_value = copy.deepcopy(reply)
    response = client.post(BASE + '/notion/connect', headers=AUTH, json={})
    assert response.status_code == 200 and response.json() == reply
    assert response.json()['portalAccessVerified'] is False
    mock_request.assert_awaited_once_with('POST', '/v1/pixel/connections/apps/notion/connect', payload={}, timeout=330)


@pytest.mark.parametrize('name,phase,body,timeout', [('complete', 'authorized', {'flowId': FLOW,
    'address': 'http://127.0.0.1:43123/?state=x&code=PRIVATE-code'}, 90),
    ('finish', 'ready', {'flowId': FLOW}, 330), ('cancel', 'cancelled', {'flowId': FLOW}, 330),
    ('disconnect', 'disconnected', {}, 330), ('check', 'ready', {}, 90)])
def test_native_action_preserves_scope_and_exacts_controller_budgets(client, mock_request, name, phase, body, timeout):
    reply = state(phase)
    mock_request.return_value = reply
    response = client.post(BASE + '/notion/' + name, headers=AUTH, json=body)
    assert response.status_code == 200 and response.json() == reply
    mock_request.assert_awaited_once_with('POST', '/v1/pixel/connections/apps/notion/' + name, payload=body, timeout=timeout)


@pytest.mark.parametrize('phase,error', [('configured', 'native-app-verification-required'), ('ready', None)])
def test_existing_grant_can_be_observed_without_fabricating_an_enrollment_flow(client, mock_request, phase, error):
    reply = dict(state(phase), flowId=None, error=error)
    mock_request.return_value = reply
    response = client.post(BASE + '/notion/check', headers=AUTH, json={})
    assert response.status_code == 200 and response.json() == reply
    assert response.json()['portalAccessVerified'] is (phase == 'ready')
    mock_request.assert_awaited_once_with('POST', '/v1/pixel/connections/apps/notion/check', payload={}, timeout=90)


def test_check_cannot_supply_a_flow_or_oauth_authority(client, mock_request, caplog):
    for body in ({'flowId': FLOW}, {'address': 'PRIVATE-code-token'}, {'client': 'PRIVATE-code-token'}):
        response = client.post(BASE + '/notion/check', headers=AUTH, json=body)
        assert response.status_code == 422 and 'PRIVATE' not in response.text and 'PRIVATE' not in caplog.text
    mock_request.assert_not_awaited()


@pytest.mark.parametrize('code', ['state-mismatch', 'sign-in-state-mismatch'])
def test_native_callback_error_is_not_explained_as_google_setup(client, mock_request, code):
    mock_request.side_effect = AgentHTTPError(409, code)
    response = client.post(BASE + '/notion/complete', headers=AUTH,
        json={'flowId': FLOW, 'address': 'http://127.0.0.1:43123/?state=x&code=PRIVATE-code'})
    assert response.status_code == 409 and 'another sign-in' in response.text
    assert 'Google' not in response.text and 'PRIVATE-code' not in response.text


def test_native_status_respects_connected_apps_preference_without_runtime_call(client, mock_request, monkeypatch):
    flag(monkeypatch, 'false')
    assert client.get(BASE, headers=AUTH).status_code == 404
    assert client.post(BASE + '/notion/connect', headers=AUTH, json={}).status_code == 404
    mock_request.assert_not_awaited()


@pytest.mark.parametrize('body', [
    {'flowId': FLOW, 'address': 'PRIVATE-callback-code-token'},
    {'flowId': FLOW, 'address': 'http://127.0.0.1:43123/?code=PRIVATE-callback-code-token' + 'x' * 8192},
    {'flowId': FLOW, 'address': {'code': 'PRIVATE-callback-code-token'}},
    {'flowId': FLOW, 'address': 'http://127.0.0.1:43123/?code=x', 'clientSecret': 'PRIVATE-callback-code-token'},
])
def test_validation_never_echoes_rejected_callback_inputs_or_logs_them(client, mock_request, caplog, body):
    response = client.post(BASE + '/notion/complete', headers=AUTH, json=body)
    assert response.status_code == 422
    assert response.json() == {'detail': {'code': 'invalid-native-app-request',
        'message': 'That app connection request is invalid. Check the connection and try again.', 'help': None}}
    assert 'PRIVATE' not in response.text and 'PRIVATE' not in caplog.text
    assert response.headers['cache-control'] == 'no-store'
    mock_request.assert_not_awaited()


def test_malformed_callback_json_has_only_a_fixed_validation_error(client, mock_request, caplog):
    response = client.post(BASE + '/notion/complete', headers={**AUTH, 'Content-Type': 'application/json'},
        content='{"flowId":"' + FLOW + '","address":"PRIVATE-callback-code-token')
    assert response.status_code == 422 and 'PRIVATE' not in response.text and 'PRIVATE' not in caplog.text
    mock_request.assert_not_awaited()


@pytest.mark.parametrize('name,body,reply', [('complete', {'flowId': FLOW,
    'address': 'http://127.0.0.1:43123/?state=x&code=PRIVATE-code'}, state('ready')),
    ('cancel', {'flowId': FLOW}, state('ready')), ('disconnect', {}, state('ready')),
    ('finish', {'flowId': FLOW}, state('authorized'))])
def test_authorization_or_other_action_cannot_return_a_structurally_valid_but_wrong_outcome(client, mock_request, name, body, reply):
    mock_request.return_value = reply
    response = client.post(BASE + '/notion/' + name, headers=AUTH, json=body)
    assert response.status_code == 502 and 'PRIVATE-' not in response.text


@pytest.mark.parametrize('reply', [dict(state('ready'), checkedAt=None), dict(state('disconnected'), providerRevoked=True),
    dict(state('authorized'), authUrl='https://provider.example/auth?state=private'),
    dict(state('consent'), authUrl='http://unreviewed.example/auth?state=private', expiresAt='2026-10-28T12:15:00Z')])
def test_false_ready_revocation_and_unsafe_authorization_are_refused(client, mock_request, reply):
    mock_request.return_value = reply
    response = client.post(BASE + '/notion/connect', headers=AUTH, json={})
    assert response.status_code == 502 and 'provider.example' not in response.text and 'unreviewed.example' not in response.text
