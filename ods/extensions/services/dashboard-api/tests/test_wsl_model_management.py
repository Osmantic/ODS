"""Dashboard polling coalesces; model mutations retain fresh ownership proofs."""
from concurrent.futures import ThreadPoolExecutor
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

    def probe(_env):
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

    def probe(_env):
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
                        lambda env: calls.append(env) or {'managed': True, 'running': True})
    assert host._model_management_snapshot()[0] == 200
    assert host._begin_model_lifecycle('model_runtime')[0]
    host._end_model_lifecycle('model_runtime')
    assert host._model_management_snapshot()[0] == 200
    management.write_text(management.read_text() + 'AMD_INFERENCE_PORT=14444\n')
    assert host._model_management_snapshot()[0] == 200
    assert len(calls) == 3


def test_lifecycle_change_during_probe_does_not_publish_old_proof(management, monkeypatch):
    def probe(_env):
        assert host._begin_model_lifecycle('model_runtime')[0]
        host._end_model_lifecycle('model_runtime')
        return {'managed': True, 'running': True}

    monkeypatch.setattr(host, '_managed_wsl_lemonade', probe)
    assert host._model_management_snapshot()[0] == 503
    assert host._model_management_cache is None


def test_handler_authenticates_before_cache_and_preserves_no_store(management, monkeypatch):
    calls = []
    monkeypatch.setattr(host, '_managed_wsl_lemonade',
                        lambda env: calls.append(env) or {'managed': True, 'running': False})
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
                        lambda env: calls.append(env) or {'managed': len(calls) == 1, 'running': True})
    assert host._model_management_snapshot()[1]['managed'] is True
    handler = fixtures._ResponseHandler(request_body={})
    host.AgentHandler._handle_model_runtime(handler, 'stop')
    assert handler.response_code == 409
    assert len(calls) == 2


def test_waiting_for_an_existing_probe_is_bounded(management, monkeypatch):
    class Busy:
        def acquire(self, timeout):
            assert timeout == 19
            return False
    monkeypatch.setattr(host, '_model_management_lock', Busy())
    monkeypatch.setattr(host, '_managed_wsl_lemonade', lambda env: pytest.fail('must not start another probe'))
    assert host._model_management_snapshot()[0] == 503
