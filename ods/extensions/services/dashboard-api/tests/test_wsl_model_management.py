"""Dashboard polling coalesces; model mutations retain fresh ownership proofs."""
from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading

import pytest
import test_model_activate as fixtures

host = fixtures._mod


@pytest.fixture
def management(tmp_path, monkeypatch):
    monkeypatch.setattr(host, 'INSTALL_DIR', tmp_path)
    monkeypatch.setattr(host, 'AGENT_API_KEY', 'test-agent-key')
    monkeypatch.setattr(host, '_model_management_cache', None)
    monkeypatch.setattr(host, '_model_management_lock', threading.Lock())
    monkeypatch.setattr(host, '_model_lifecycle_lock', threading.Lock())
    monkeypatch.setattr(host, '_model_lifecycle_state_lock', threading.Lock())
    monkeypatch.setattr(host, '_model_lifecycle_operation', None)
    monkeypatch.setattr(host, '_model_lifecycle_target', None)
    monkeypatch.setattr(host, '_model_lifecycle_revision', 0)
    path = tmp_path / '.env'
    path.write_text('LEMONADE_HOST_TRANSPORT=model-router\n')
    return path


@pytest.mark.parametrize('failed', [False, True])
def test_concurrent_management_polls_share_one_probe_and_its_failure(management, monkeypatch, failed):
    ready = threading.Barrier(5)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def probe(_env, *, deadline=None):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        if failed:
            raise RuntimeError('fixture unavailable')
        return {'managed': True, 'running': True}

    def poll():
        ready.wait(5)
        return host._model_management_snapshot()

    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(poll) for _ in range(4)]
        try:
            ready.wait(5)
            assert entered.wait(5)
        finally:
            release.set()
        results = [future.result(timeout=5) for future in futures]
    assert len(calls) == 1
    assert all(code == (503 if failed else 200) for code, _ in results)
    assert all(value.get('managed') is (None if failed else True) for _, value in results)


def test_cache_lasts_one_second_after_completion_and_never_reuses_success_for_failure(management, monkeypatch):
    clock = [10.0]
    calls = []

    def probe(_env, *, deadline=None):
        calls.append(True)
        if len(calls) > 1:
            raise RuntimeError('ownership lost')
        clock[0] = 20.0  # Slow proof must still give waiting polls a full cache interval.
        return {'managed': True, 'running': True}

    monkeypatch.setattr(host.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    assert host._model_management_snapshot()[0] == 200
    clock[0] = 20.9
    assert host._model_management_snapshot()[0] == 200
    clock[0] = 21.0
    assert host._model_management_snapshot()[0] == 503
    assert host._model_management_snapshot()[0] == 503
    assert len(calls) == 2


def test_configuration_and_completed_lifecycle_invalidate_cache(management, monkeypatch):
    calls = []
    monkeypatch.setattr(host, '_managed_wsl_lemonade',
                        lambda env, *, deadline=None: calls.append(env) or {'managed': True, 'running': True})
    assert host._model_management_snapshot()[0] == 200
    assert host._begin_model_lifecycle('model_runtime')[0]
    host._end_model_lifecycle('model_runtime')
    assert host._model_management_snapshot()[0] == 200
    management.write_text(management.read_text() + 'AMD_INFERENCE_PORT=14444\n')
    assert host._model_management_snapshot()[0] == 200
    assert len(calls) == 3


def test_lifecycle_change_during_probe_requires_fresh_proof(management, monkeypatch, caplog):
    calls = []

    def probe(_env, *, deadline=None):
        calls.append(deadline)
        if len(calls) == 1:
            assert host._begin_model_lifecycle('model_runtime')[0]
            host._end_model_lifecycle('model_runtime')
            return {'managed': True, 'running': True, 'modelStoreId': 'stale-store'}
        return {'managed': True, 'running': False, 'modelStoreId': 'fresh-store'}

    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    code, result = host._model_management_snapshot()
    assert code == 200
    assert result == {'managed': True, 'canActivate': False, 'canUnload': True,
                      'running': False, 'modelStoreId': 'fresh-store'}
    assert isinstance(calls[0], float) and calls[0] == calls[1]
    assert host._model_management_cache[0][1][0] == 2
    assert 'reason=key_drift' in caplog.text
    assert 'revision_before=0 revision_after=2' in caplog.text
    assert 'route_key_changed=False' in caplog.text


def test_second_lifecycle_change_fails_closed_without_another_probe(management, monkeypatch):
    calls = []

    def probe(_env, *, deadline=None):
        calls.append(deadline)
        assert host._begin_model_lifecycle('model_runtime')[0]
        host._end_model_lifecycle('model_runtime')
        return {'managed': True, 'running': True}

    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    assert host._model_management_snapshot()[0] == 503
    assert len(calls) == 2
    assert host._model_management_cache is None


def test_lifecycle_drift_after_failed_proof_does_not_retry(management, monkeypatch):
    calls = []

    def probe(_env, *, deadline=None):
        calls.append(True)
        assert host._begin_model_lifecycle('model_runtime')[0]
        host._end_model_lifecycle('model_runtime')
        raise subprocess.TimeoutExpired(cmd='read-only-status', timeout=15)

    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    assert host._model_management_snapshot()[0] == 503
    assert len(calls) == 1
    assert host._model_management_cache is None


def test_lifecycle_drift_near_deadline_does_not_start_another_probe(management, monkeypatch):
    clock = [0.0]
    calls = []

    def probe(_env, *, deadline=None):
        calls.append(True)
        clock[0] = 16.0
        assert host._begin_model_lifecycle('model_runtime')[0]
        host._end_model_lifecycle('model_runtime')
        return {'managed': True, 'running': True}

    monkeypatch.setattr(host.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    assert host._model_management_snapshot()[0] == 503
    assert len(calls) == 1


def test_late_fresh_proof_is_not_published(management, monkeypatch):
    clock = [0.0]
    calls = []

    def probe(_env, *, deadline=None):
        calls.append(deadline)
        if len(calls) == 1:
            assert host._begin_model_lifecycle('model_runtime')[0]
            host._end_model_lifecycle('model_runtime')
            return {'managed': True, 'running': True}
        assert deadline == 18.0
        clock[0] = 18.1
        return {'managed': True, 'running': True}

    monkeypatch.setattr(host.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    assert host._model_management_snapshot()[0] == 503
    assert len(calls) == 2
    assert calls == [18.0, 18.0]
    assert host._model_management_cache[2] == 503


def test_first_proof_shares_request_deadline_and_late_success_is_not_published(
        management, monkeypatch):
    clock = [0.0]
    calls = []

    def probe(_env, *, deadline=None):
        calls.append(deadline)
        clock[0] = 18.1
        return {'managed': True, 'running': True}

    monkeypatch.setattr(host.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    assert host._model_management_snapshot()[0] == 503
    assert calls == [18.0]
    assert host._model_management_cache[2] == 503


def test_retry_deadline_caps_windows_controller_proof(management, monkeypatch):
    bridge = host._wsl_lemonade
    clock = [100.0]
    controller_timeouts = []
    monkeypatch.setattr(bridge.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(bridge, 'candidate', lambda _env: True)
    context = bridge._Context('distro', '/install', 'controller',
                              bridge._WindowsTools('shell', 'probe', 'modules'))
    monkeypatch.setattr(bridge, '_context', lambda *_args, **_kwargs: context)
    monkeypatch.setattr(bridge, '_select_socket', lambda *_args, **_kwargs: ('socket', (1, 2)))

    def controller(_context, _socket, _request, timeout):
        controller_timeouts.append(timeout)
        return {'managed': True, 'running': True}

    monkeypatch.setattr(bridge, '_call', controller)
    monkeypatch.setattr(bridge, '_endpoint_matches_plan', lambda *_args: None)
    assert bridge.status(management.parent, {}, deadline=112.0)['managed'] is True
    assert controller_timeouts == [12.0]


def test_retry_deadline_caps_path_translation_and_refuses_expiry(management, monkeypatch):
    bridge = host._wsl_lemonade
    clock = [100.0]
    timeouts = []
    monkeypatch.setattr(bridge.time, 'monotonic', lambda: clock[0])

    def translate(_command, *, timeout):
        timeouts.append(timeout)
        return subprocess.CompletedProcess([], 0, b'/mnt/c\n', b'')

    monkeypatch.setattr(bridge, '_run', translate)
    assert bridge._path('C:\\', '-u', deadline=101.0) == '/mnt/c'
    assert timeouts == [1.0]
    clock[0] = 101.0
    with pytest.raises(bridge.BridgeError, match='deadline'):
        bridge._path('C:\\', '-u', deadline=101.0)
    assert timeouts == [1.0]


def test_route_and_active_lifecycle_drift_log_only_safe_metadata(management, monkeypatch, caplog):
    def probe(_env, *, deadline=None):
        assert host._begin_model_lifecycle('pixel_startup_reproof', 'private-model-target')[0]
        management.write_text(management.read_text() + 'LEMONADE_BASE_URL=https://private-route.example\n')
        return {'managed': True, 'running': True}

    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    try:
        assert host._model_management_snapshot() == (
            503, {'error': 'Windows runtime management could not be verified'})
    finally:
        host._end_model_lifecycle('pixel_startup_reproof')
    assert host._model_management_cache is None
    assert 'reason=key_drift' in caplog.text
    assert 'revision_before=0 revision_after=1' in caplog.text
    assert 'operation_before=none operation_after=pixel_startup_reproof' in caplog.text
    assert 'route_key_changed=True' in caplog.text
    assert 'private-model-target' not in caplog.text
    assert 'private-route.example' not in caplog.text


def test_management_verification_error_keeps_generic_response_and_safe_log(management, monkeypatch, caplog):
    def probe(_env, *, deadline=None):
        raise RuntimeError('private-token-and-path')

    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    assert host._model_management_snapshot() == (
        503, {'error': 'Windows runtime management could not be verified'})
    assert 'reason=verification_error error_type=RuntimeError' in caplog.text
    assert 'private-token-and-path' not in caplog.text


def test_unknown_lifecycle_operation_is_not_logged(management, monkeypatch, caplog):
    def probe(_env, *, deadline=None):
        assert host._begin_model_lifecycle('private-token-and-path')[0]
        return {'managed': True, 'running': True}

    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    try:
        assert host._model_management_snapshot()[0] == 503
    finally:
        host._end_model_lifecycle('private-token-and-path')
    assert 'operation_after=unknown' in caplog.text
    assert 'private-token-and-path' not in caplog.text


def test_handler_authenticates_before_cache_and_preserves_no_store(management, monkeypatch):
    calls = []
    monkeypatch.setattr(host, '_managed_wsl_lemonade',
                        lambda env, *, deadline=None: calls.append(env) or {'managed': True, 'running': False})
    denied = fixtures._ResponseHandler(request_body={}, api_key='wrong')
    host.AgentHandler._handle_model_management(denied)
    assert denied.response_code == 403
    assert calls == []
    allowed = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_management(allowed)
    assert allowed.parse_response() == {'managed': True, 'running': False, 'canActivate': False, 'canUnload': True}
    assert ('Cache-Control', 'no-store') in allowed.response_headers


def test_mutation_preflight_does_not_consume_management_cache(management, monkeypatch):
    calls = []
    monkeypatch.setattr(host, '_managed_wsl_lemonade',
                        lambda env, *, deadline=None: calls.append(env) or {'managed': len(calls) == 1, 'running': True})
    assert host._model_management_snapshot()[1]['managed'] is True
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_runtime(handler, 'stop')
    assert handler.response_code == 409
    assert len(calls) == 2


def test_waiting_for_an_existing_probe_is_bounded(management, monkeypatch, caplog):
    class Busy:
        def acquire(self, timeout):
            assert 0 < timeout <= 18
            return False
    monkeypatch.setattr(host, '_model_management_lock', Busy())
    monkeypatch.setattr(host, '_managed_wsl_lemonade', lambda env, *, deadline=None: pytest.fail('must not start another probe'))
    assert host._model_management_snapshot() == (
        503, {'error': 'Windows runtime management could not be verified'})
    assert 'reason=lock_timeout' in caplog.text


def test_lock_acquired_after_request_deadline_does_not_start_probe(
        management, monkeypatch):
    clock = [100.0]

    class LateLock:
        def acquire(self, timeout):
            assert timeout == 18.0
            clock[0] = 118.1
            return True

        def release(self):
            pass

    monkeypatch.setattr(host.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(host, '_model_management_lock', LateLock())
    monkeypatch.setattr(host, '_managed_wsl_lemonade',
                        lambda env, *, deadline=None: pytest.fail('must not start proof'))
    assert host._model_management_snapshot() == (
        503, {'error': 'Windows runtime management could not be verified'})


def test_management_handler_exception_keeps_generic_response_and_safe_log(management, monkeypatch, caplog):
    def fail():
        raise RuntimeError('private-token-and-path')

    monkeypatch.setattr(host, '_model_management_snapshot', fail)
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_management(handler)
    assert handler.response_code == 503
    assert handler.parse_response() == {'error': 'Windows runtime management could not be verified'}
    assert 'reason=verification_error error_type=RuntimeError' in caplog.text
    assert 'private-token-and-path' not in caplog.text
