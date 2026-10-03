"""Actual dashboard/host HTTP; explicitly simulated privileged control socket."""
import copy

import pytest
from routers import pixel_providers
from test_pixel_settings_host_integration import (
    actual_stack,  # noqa: F401 - shared pytest fixture
)

BINDING = {'schemaVersion': 1, 'activationId': '123e4567-e89b-12d3-a456-426614174000',
           'revision': 3, 'allowCloud': False}
CHANGE = {'operation': 'apply', 'revision': 'a' * 64, 'providerRevision': 3}


@pytest.fixture
def provider_stack(actual_stack, monkeypatch):  # noqa: F811 - imported pytest fixture
    import pixel_access_client
    from pixel_provider import host_api
    client, agent, handler = actual_stack
    client.app.include_router(pixel_providers.router)
    monkeypatch.setattr(host_api.platform, 'system', lambda: 'Linux')
    calls = []
    state = {'status': 'inactive', 'revision': 'a' * 64, 'providerRevision': 3, 'binding': None,
             'pending': False, 'registrationVerified': True, 'transportVerified': False,
             'lastVerifiedAt': '2026-09-09T18:01:25.584Z'}
    def control(operation, body=None, **kwargs):
        calls.append((operation, body, kwargs))
        if operation == 'provider-status': return 200, copy.deepcopy(state)
        binding = BINDING if body['operation'] == 'apply' else None
        state.update(status='applied' if binding else 'inactive', binding=binding, pending=False)
        return 200, {'outcome': 'rolled-back' if body['operation'] == 'recover' else 'applied',
                     'binding': binding, 'registrationVerified': True, 'transportVerified': False}
    monkeypatch.setattr(pixel_access_client, 'request_access', control)
    return client, agent, handler, calls, state


def test_actual_host_runtime_apply_status_deactivate(provider_stack, tmp_path):
    client, _agent, handler, calls, _state = provider_stack
    status = client.get('/api/pixel/providers/runtime')
    assert status.status_code == 200, status.text
    assert status.json()['status'] == 'inactive'
    assert status.headers['cache-control'] == 'no-store'
    result = client.post('/api/pixel/providers/runtime', json=CHANGE)
    assert result.status_code == 200, result.text
    assert result.json()['binding'] == BINDING and result.json()['transportVerified'] is False
    assert result.headers['cache-control'] == 'no-store'
    current = client.get('/api/pixel/providers/runtime').json()
    assert current['status'] == 'applied' and current['registrationVerified'] is True
    off = client.post('/api/pixel/providers/runtime', json=dict(CHANGE, operation='deactivate'))
    assert off.status_code == 200 and off.json()['binding'] is None
    assert calls[1] == ('provider-change', CHANGE, {'settings_data_dir': tmp_path})
    assert handler.posts == 2
    assert list(tmp_path.iterdir()) == []  # No settings/credentials written by the public adapter.


def test_pending_recovery_does_not_require_readable_saved_configuration(provider_stack, tmp_path):
    client, _agent, _handler, _calls, state = provider_stack
    state.update(status='pending', pending=True, binding=None, registrationVerified=False, lastVerifiedAt=None)
    directory = tmp_path / 'pixel-providers'
    directory.mkdir(mode=0o700)
    saved = directory / 'provider-config.json'
    saved.write_bytes(b'corrupt-owner-state')
    saved.chmod(0o600)
    assert client.get('/api/pixel/providers').status_code == 503
    inspected = client.get('/api/pixel/providers/runtime')
    assert inspected.status_code == 200 and inspected.json()['pending'] is True
    result = client.post('/api/pixel/providers/runtime', json=dict(CHANGE, operation='recover'))
    assert result.status_code == 200 and result.json()['outcome'] == 'rolled-back'
    assert saved.read_bytes() == b'corrupt-owner-state'


@pytest.mark.parametrize('extra', [{'binding': BINDING}, {'path': '/etc'}, {'data_dir_id': 'a' * 64},
                                  {'transaction_id': 'a' * 64}, {'expected_projection': {}}])
def test_public_authority_fields_never_reach_host(provider_stack, extra):
    client, _agent, handler, calls, _state = provider_stack
    response = client.post('/api/pixel/providers/runtime', json=dict(CHANGE, **extra))
    assert response.status_code == 400
    assert calls == [] and handler.posts == 0


def test_authentication_and_size_limits_precede_host(provider_stack):
    client, _agent, handler, calls, _state = provider_stack
    assert client.get('/api/pixel/providers/runtime', headers={'Authorization': ''}).status_code == 401
    assert client.post('/api/pixel/providers/runtime', json=CHANGE, headers={'Authorization': ''}).status_code == 401
    assert client.post('/api/pixel/providers/runtime', content=b' ' * 2049).status_code == 413
    assert client.post('/api/pixel/providers/runtime', content=b'{"operation":"apply","operation":"recover"}').status_code == 400
    assert handler.posts == 0 and calls == []


def test_lifecycle_busy_prevents_controller_change(provider_stack):
    client, agent, _handler, calls, _state = provider_stack
    acquired, _ = agent._begin_model_lifecycle('model_switch')
    assert acquired
    try:
        response = client.post('/api/pixel/providers/runtime', json=CHANGE)
        assert response.status_code == 409
        assert response.json()['detail']['reason'] == 'model-lifecycle-busy'
        assert calls == []
    finally:
        agent._end_model_lifecycle('model_switch')


def test_actual_host_preserves_controller_conflict_reason_without_replay(provider_stack, monkeypatch):
    import pixel_access_client
    client, _agent, handler, _calls, _state = provider_stack
    calls = []
    def conflict(*args, **kwargs):
        calls.append((args, kwargs))
        return 409, {'error': 'provider-inspection-changed'}
    monkeypatch.setattr(pixel_access_client, 'request_access', conflict)
    response = client.post('/api/pixel/providers/runtime', json=dict(CHANGE, operation='deactivate'))
    assert response.status_code == 409
    assert response.json()['detail']['reason'] == 'provider-inspection-changed'
    assert len(calls) == 1 and handler.posts == 1
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('failure', ['timeout', 'malformed', 'private-error'])
def test_uncertain_response_is_not_retried_and_releases_lifecycle(provider_stack, monkeypatch, failure):
    import pixel_access_client
    client, agent, handler, _calls, _state = provider_stack
    calls = []
    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        if failure == 'timeout': raise TimeoutError('private-sentinel')
        if failure == 'private-error': return 503, {'error': 'private-sentinel'}
        return 200, {'outcome': 'applied', 'binding': dict(BINDING, revision=99),
                     'registrationVerified': True, 'transportVerified': False}
    monkeypatch.setattr(pixel_access_client, 'request_access', fail)
    response = client.post('/api/pixel/providers/runtime', json=CHANGE)
    assert response.status_code == 503 and 'private-sentinel' not in response.text
    assert len(calls) == 1 and handler.posts == 1
    acquired, _ = agent._begin_model_lifecycle('model_switch')
    assert acquired
    agent._end_model_lifecycle('model_switch')


def test_unavailable_control_does_not_claim_inactive(provider_stack, monkeypatch):
    import pixel_access_client
    client, _agent, _handler, _calls, _state = provider_stack
    def missing(*_args, **_kwargs): raise FileNotFoundError('private-controller-path')
    monkeypatch.setattr(pixel_access_client, 'request_access', missing)
    response = client.get('/api/pixel/providers/runtime')
    assert response.status_code == 200
    assert response.json()['status'] == 'unavailable' and response.json()['pending'] is None
    assert 'private-controller-path' not in response.text


@pytest.mark.parametrize('path,body,status', [
    ('/api/pixel/providers/runtime', {}, 400),
    ('/api/pixel/providers/runtime', 'oversized', 413),
    ('/api/pixel/providers/runtime', CHANGE, 200),
    ('/api/pixel/providers/save', {}, 400),
])
def test_provider_responses_are_not_cacheable(provider_stack, path, body, status):
    client, _agent, _handler, _calls, _state = provider_stack
    response = client.post(path, content=b' ' * 2049) if body == 'oversized' else client.post(path, json=body)
    assert response.status_code == status
    assert response.headers['cache-control'] == 'no-store'
