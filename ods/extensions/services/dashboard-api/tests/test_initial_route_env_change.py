"""Installer route changes must end obsolete observational model waits."""
import subprocess
import threading

import pytest
import test_model_activate as tma
from model_switchboard import state as sb


@pytest.fixture
def route(tmp_path, monkeypatch):
    install, env_path = tma._write_model_activation_fixture(tmp_path)[:2]
    monkeypatch.setattr(tma._mod, 'INSTALL_DIR', install)
    monkeypatch.setattr(tma._mod, '_model_lifecycle_operation', None)
    monkeypatch.setattr(tma._mod, '_switchboard_initial_verify_cancel', threading.Event())
    monkeypatch.delenv('ODS_HOST_INSTALL_DIR', raising=False)
    monkeypatch.setattr(tma._mod, '_chat_completion_ready', lambda *_a, **_k: True)
    monkeypatch.setattr(tma._mod, '_llama_runtime_context_length', lambda *_a: 65536)
    monkeypatch.setattr(tma._mod, '_load_model_library_records', lambda: [])
    monkeypatch.setattr(tma._mod.time, 'sleep', lambda _s: None)
    state_path = install / 'data' / 'model-state.json'
    sb.initialize_if_missing(state_path, tma._mod.load_env(env_path))
    return env_path, state_path


def set_model(path, gguf, context='2048'):
    path.write_text('GPU_BACKEND=nvidia\nOLLAMA_PORT=8080\n'
                    f'GGUF_FILE={gguf}\nLLM_MODEL={gguf.removesuffix(".gguf")}\n'
                    f'CTX_SIZE={context}\n')


def fake_response(command, model):
    return subprocess.CompletedProcess(command, 0,
        stdout=tma._llama_identity_response(model), stderr='')


def test_initial_wait_exits_after_installer_selects_bootstrap_then_new_proof_binds(route, monkeypatch):
    env_path, state_path = route
    before = state_path.read_bytes()
    calls = []

    def probe(command, **_kwargs):
        calls.append(command)
        set_model(env_path, 'bootstrap-2b.gguf')
        return fake_response(command, 'bootstrap-2b.gguf')

    monkeypatch.setattr(tma._mod.subprocess, 'run', probe)
    assert not tma._mod._publish_verified_initial_switchboard_route(
        reason='startup', attempts=3, initial_delay=0, interval=0)
    assert len(calls) == 1
    assert state_path.read_bytes() == before
    assert not tma._mod._switchboard_initial_verify_cancel.is_set()

    # The existing status scheduler can admit a fresh observation after the
    # stale one exits. Exercise the publisher against the newly selected route.
    monkeypatch.setattr(tma._mod.subprocess, 'run',
                        lambda command, **_k: fake_response(command, 'bootstrap-2b.gguf'))
    assert tma._mod._publish_verified_initial_switchboard_route(
        reason='status', attempts=1, initial_delay=0, interval=0)
    doc, errors = sb.read_state(state_path)
    assert not errors
    assert doc['active']['runtimeModelId'] == 'bootstrap-2b.gguf'
    assert doc['active']['proof'] == {'identity': 'bootstrap-2b.gguf', 'completion': True}


@pytest.mark.parametrize('contract,empty', [({}, False), ({'return_identity': True}, ''), ({'return_proof': True}, {})])
def test_stale_snapshot_never_probes_or_warms(contract, empty, route, monkeypatch):
    env_path, _ = route
    env = tma._mod.load_env(env_path)
    set_model(env_path, 'bootstrap-2b.gguf')
    monkeypatch.setattr(tma._mod.subprocess, 'run',
                        lambda *_a, **_k: pytest.fail('stale runtime must not be probed'))
    result = tma._mod._wait_for_model_readiness(
        env, model_id='old-model', gguf_file='old-model.gguf', llm_model_name='old-model',
        attempts=2, initial_delay=0, interval=0,
        env_still_current=lambda: tma._mod._initial_switchboard_route_env_matches(env), **contract)
    assert result == empty


def test_context_change_during_proof_does_not_publish_obsolete_binding(route, monkeypatch):
    env_path, state_path = route
    before = state_path.read_bytes()

    def completed_proof(*_args, **_kwargs):
        set_model(env_path, 'old-model.gguf', context='32768')
        return {'identity': 'old-model.gguf', 'contextLength': 65536,
                'contextVerified': True, 'verifiedAt': '2026-09-26T17:00:00Z'}

    monkeypatch.setattr(tma._mod, '_wait_for_model_readiness', completed_proof)
    assert not tma._mod._publish_verified_initial_switchboard_route(reason='test')
    assert state_path.read_bytes() == before


def test_mismatched_runtime_remains_unverified(route, monkeypatch):
    _, state_path = route
    before = state_path.read_bytes()
    monkeypatch.setattr(tma._mod.subprocess, 'run',
                        lambda command, **_k: fake_response(command, 'wrong-model.gguf'))
    assert not tma._mod._publish_verified_initial_switchboard_route(
        reason='test', attempts=2, initial_delay=0, interval=0)
    assert state_path.read_bytes() == before


def test_explicit_wait_retains_requested_model_contract(route, monkeypatch):
    env_path, _ = route
    snapshot = tma._mod.load_env(env_path)
    set_model(env_path, 'bootstrap-2b.gguf')
    monkeypatch.setattr(tma._mod.subprocess, 'run',
                        lambda command, **_k: fake_response(command, 'old-model.gguf'))
    assert tma._mod._wait_for_model_readiness(
        snapshot, model_id='old-model', gguf_file='old-model.gguf', llm_model_name='old-model',
        attempts=1, initial_delay=0, interval=0, return_identity=True) == 'old-model.gguf'


def test_fast_poll_preserves_stale_route_predicate(route, monkeypatch):
    env_path, _ = route
    snapshot = tma._mod.load_env(env_path)
    calls = []

    def probe(command, **_kwargs):
        calls.append(command)
        set_model(env_path, 'bootstrap-2b.gguf')
        return fake_response(command, 'bootstrap-2b.gguf')

    monkeypatch.setattr(tma._mod.subprocess, 'run', probe)
    result = tma._mod._wait_for_model_readiness(
        snapshot, model_id='old-model', gguf_file='old-model.gguf', llm_model_name='old-model',
        attempts=2, initial_delay=0, interval=0, fast_poll_seconds=.1, fast_poll_interval=.05,
        return_proof=True,
        env_still_current=lambda: tma._mod._initial_switchboard_route_env_matches(snapshot))
    assert result == {}
    assert len(calls) == 1


def test_env_change_during_native_identity_probe_prevents_old_model_warmup(route, monkeypatch):
    env_path, _ = route
    snapshot = tma._mod.load_env(env_path)
    monkeypatch.setattr(tma._mod, '_uses_lemonade_runtime', lambda _env: True)
    monkeypatch.setattr(tma._mod, '_lemonade_runtime_base_url', lambda _env: 'http://127.0.0.1:8080')
    monkeypatch.setattr(tma._mod, '_lemonade_loaded_model_identity', lambda *_a: '')
    monkeypatch.setattr(tma._mod, '_send_lemonade_warmup',
                        lambda *_a, **_k: pytest.fail('installer selected bootstrap; old full model must not be warmed'))

    def probe(command, **_kwargs):
        set_model(env_path, 'bootstrap-2b.gguf')
        return subprocess.CompletedProcess(command, 0, stdout='{"model_loaded":"bootstrap-2b"}', stderr='')

    monkeypatch.setattr(tma._mod.subprocess, 'run', probe)
    assert tma._mod._wait_for_model_readiness(
        snapshot, model_id='old-model', gguf_file='old-model.gguf', llm_model_name='old-model',
        lemonade_model_id='old-model', attempts=2, initial_delay=0, interval=0,
        return_proof=True,
        env_still_current=lambda: tma._mod._initial_switchboard_route_env_matches(snapshot)) == {}
