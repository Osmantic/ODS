"""The provider request completes before its API is independently refreshed."""
import json
from types import SimpleNamespace

import pytest

from test_host_agent import _mod, _FakeHandler


@pytest.fixture
def runtime(monkeypatch):
    state = SimpleNamespace(events=[],mode='cloud',running=True,exists=True,
                            healthy=True,pending=False,proof=True,failure=None)
    monkeypatch.setattr(_mod, 'load_env', lambda _path: {'ODS_MODE':'local','PIXEL_OPENWEBUI_KEY':'fixture'})
    monkeypatch.setattr(_mod, '_pixel_model_hold_pending', lambda: state.pending)
    monkeypatch.setattr(_mod, '_dashboard_container_mode', lambda: {
        'mode':state.mode,'running':state.running,'exists':state.exists,'healthy':state.healthy})

    class Hold:
        previous = {'model':'fixture','contextLength':8192,'maxTokens':1024,'reasoning':False}
        def verify_held(self):
            state.events.append('verify')
        def finish(self, outcome):
            assert outcome == 'rollback'
            state.events.append('release')
            if state.failure == 'release':
                raise _mod._PixelModelTransactionUncertain('unknown finish')
            state.pending = False

    def begin(config):
        state.events.append('hold')
        if state.failure == 'busy':
            raise _mod._PixelModelTransactionRejected('runtime-busy')
        state.pending = True
        return Hold()

    def recreate(container, previous, *, recreate):
        assert container == 'ods-dashboard-api' and recreate is True
        assert state.pending, 'API recreation must be inside admission hold'
        state.events.append('recreate')
        if state.failure == 'recreate':
            raise RuntimeError('failed')
        state.mode = 'local'
        return True

    def health(container):
        assert container == 'ods-dashboard-api'
        state.events.append('health')
        if state.failure == 'health':
            raise RuntimeError('unhealthy')

    monkeypatch.setattr(_mod, '_begin_pixel_model_transaction', begin)
    monkeypatch.setattr(_mod, '_restart_existing_container', recreate)
    monkeypatch.setattr(_mod, '_wait_for_container_health', health)
    monkeypatch.setattr(_mod, '_prove_pixel_model_contract', lambda *_args: state.proof)
    return state


def test_reconcile_holds_chat_recreates_proves_and_releases_unchanged_model(runtime):
    assert _mod._reconcile_dashboard_mode() == {'recreated':True,'reason':'mode_refreshed'}
    assert runtime.events == ['hold','recreate','health','verify','release']
    assert not runtime.pending
    assert _mod._reconcile_dashboard_mode() == {'recreated':False,'reason':'already_current'}
    assert runtime.events.count('recreate') == 1


@pytest.mark.parametrize('case', ['missing','stopped','current'])
def test_reconcile_preserves_optional_or_current_api(runtime, case):
    if case == 'missing': runtime.exists = False
    if case == 'stopped': runtime.running = False
    if case == 'current': runtime.mode = 'local'
    assert _mod._reconcile_dashboard_mode()['recreated'] is False
    assert runtime.events == []


@pytest.mark.parametrize('case', ['pending','busy'])
def test_reconcile_never_touches_api_during_chat_or_uncertain_transition(runtime, case):
    runtime.pending = case == 'pending'
    runtime.failure = case
    with pytest.raises(RuntimeError):
        _mod._reconcile_dashboard_mode()
    assert 'recreate' not in runtime.events


@pytest.mark.parametrize('failure', ['recreate','health'])
def test_failed_refresh_releases_only_proven_unchanged_model(runtime, failure):
    runtime.failure = failure
    with pytest.raises(RuntimeError):
        _mod._reconcile_dashboard_mode()
    assert runtime.events[-2:] == ['verify','release']
    assert not runtime.pending


@pytest.mark.parametrize('failure', ['proof','release'])
def test_uncertain_model_proof_or_release_is_never_cleared_or_replayed(runtime, failure):
    runtime.proof = failure != 'proof'
    runtime.failure = failure
    with pytest.raises(_mod._PixelModelTransactionUncertain):
        _mod._reconcile_dashboard_mode()
    assert runtime.pending
    assert runtime.events.count('recreate') == 1
    assert runtime.events.count('release') == (1 if failure == 'release' else 0)


def test_status_requires_mode_health_and_released_lifecycle(runtime, monkeypatch):
    runtime.mode = 'local'
    monkeypatch.setattr(_mod, '_model_lifecycle_status', lambda: {'lifecycleActive':False})
    assert _mod._dashboard_mode_status()['ready'] is True
    runtime.pending = True
    assert _mod._dashboard_mode_status()['ready'] is False
    runtime.pending = False
    runtime.healthy = False
    assert _mod._dashboard_mode_status()['ready'] is False
    runtime.healthy = True
    monkeypatch.setattr(_mod, '_model_lifecycle_status', lambda: {
        'lifecycleActive':True,'activeOperation':'dashboard_mode_reconcile'})
    status = _mod._dashboard_mode_status()
    assert status['reconciling'] is True and status['ready'] is False
    assert status['lifecycleActive'] is True
    monkeypatch.setattr(_mod, '_model_lifecycle_status', lambda: {
        'lifecycleActive':True,'activeOperation':'model_activation'})
    status = _mod._dashboard_mode_status()
    assert status['lifecycleActive'] is True and status['reconciling'] is False
    assert status['ready'] is False


def test_handler_requires_auth_and_serializes_reconcile(runtime, monkeypatch):
    monkeypatch.setattr(_mod, 'check_auth', lambda _handler: False)
    _mod.AgentHandler._handle_dashboard_mode(_FakeHandler(b''), reconcile=True)
    assert not runtime.events
    monkeypatch.setattr(_mod, 'check_auth', lambda _handler: True)
    acquired, _ = _mod._begin_model_lifecycle('model_activation')
    assert acquired
    try:
        handler = _FakeHandler(b'')
        _mod.AgentHandler._handle_dashboard_mode(handler, reconcile=True)
        assert handler.response_code == 409
        assert not runtime.events
    finally:
        _mod._end_model_lifecycle('model_activation')


def test_inspection_publishes_only_sanitized_mode(monkeypatch):
    monkeypatch.setattr(_mod, '_container_exists', lambda container: container == 'ods-dashboard-api')
    monkeypatch.setattr(_mod.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        returncode=0,stdout=json.dumps({'running':True,'health':'healthy',
                                      'env':['ODS_MODE=local','API_KEY=must-not-publish']})))
    result = _mod._dashboard_container_mode()
    assert result == {'exists':True,'running':True,'mode':'local','healthy':True}
    assert 'must-not-publish' not in json.dumps(result)


@pytest.mark.parametrize('method', ['get','post'])
def test_dashboard_mode_api_requires_auth(test_client, method):
    assert getattr(test_client, method)('/api/remote-provider/dashboard-mode').status_code == 401


def test_api_process_mode_still_must_match_after_host_recreation(test_client, monkeypatch):
    from routers import remote_provider_status as api
    monkeypatch.setattr(api, 'ODS_MODE_EFFECTIVE', 'cloud')
    monkeypatch.setattr(api, 'read_live_env_value', lambda _key: 'local')
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method,path))
        return {'ready':True,'matches':True,'containerMode':'local'}

    monkeypatch.setattr(api, 'async_request_agent_json', request)
    response = test_client.get('/api/remote-provider/dashboard-mode', headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()['ready'] is False
    assert response.json()['effectiveMode'] == 'cloud'
    monkeypatch.setattr(api, 'ODS_MODE_EFFECTIVE', 'local')
    assert test_client.get('/api/remote-provider/dashboard-mode', headers=test_client.auth_headers).json()['ready'] is True
    assert calls == [('GET','/v1/remote-provider/dashboard-mode')] * 2


def test_api_reconcile_is_separate_from_provider_mutation(test_client, monkeypatch):
    from routers import remote_provider_status as api
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method,path))
        return {'recreated':True}

    monkeypatch.setattr(api, 'async_request_agent_json', request)
    response = test_client.post('/api/remote-provider/dashboard-mode', headers=test_client.auth_headers)
    assert response.status_code == 200
    assert calls == [('POST','/v1/remote-provider/dashboard-mode')]


def test_model_hold_is_respected_even_if_portal_key_was_removed(runtime, monkeypatch):
    runtime.pending = True
    monkeypatch.setattr(_mod,'load_env',lambda _path: {'ODS_MODE':'local'})
    assert _mod._dashboard_mode_status()['pending'] is True
    with pytest.raises(_mod._PixelModelTransactionUncertain):
        _mod._reconcile_dashboard_mode()
    assert runtime.events == []


@pytest.mark.parametrize('failure,code', [('busy','dashboard_refresh_busy'), ('release','model_recovery_required')])
def test_handler_distinguishes_busy_refusal_from_required_recovery(runtime, monkeypatch, failure, code):
    monkeypatch.setattr(_mod, 'check_auth', lambda _handler: True)
    runtime.failure = failure
    handler = _FakeHandler(b'')
    _mod.AgentHandler._handle_dashboard_mode(handler, reconcile=True)
    assert handler.response_code == 409
    assert handler.parse_response()['code'] == code
    assert not _mod._model_lifecycle_status().get('lifecycleActive')
    assert runtime.pending is (failure == 'release')
