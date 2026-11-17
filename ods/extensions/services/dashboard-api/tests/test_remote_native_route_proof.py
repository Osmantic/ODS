"""A native health check alone cannot restore a reconstructed router route."""
import ast
from pathlib import Path
from typing import Any

import pytest

from test_host_agent import _mod
from test_remote_native_restore import route as native_route  # noqa: F401 -- shared lifecycle fixture


def router_accepts(doc):
    # Exercise the actual router predicate without starting its ASGI app or
    # importing a second service's dependency stack into the host-agent suite.
    path = Path(__file__).resolve().parents[2] / 'model-router/app/main.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    predicate = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name == '_has_verified_active_route')
    namespace: dict[str, Any] = {'Any': Any}
    exec(compile(ast.Module(body=[predicate], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['_has_verified_active_route'](doc)


@pytest.fixture
def reconstructed(request, monkeypatch):
    route = request.getfixturevalue('native_route')
    route.env.write_text(route.env.read_text() +
        'GGUF_FILE=local.gguf\nLLM_MODEL=local\nCTX_SIZE=65536\nODS_MODEL_SWITCHBOARD=enabled\n')
    env = dict(_mod.load_env(route.env), ODS_MODE='local')
    route.state_path = _mod._switchboard_state_path()
    doc = _mod._switchboard_state.initialize_if_missing(route.state_path, env)
    assert doc and not router_accepts(doc)
    route.proof = {'identity': 'local.gguf', 'contextLength': 65536, 'contextVerified': True}
    route.identity_live = True
    monkeypatch.setattr(_mod, '_macos_native_llama_identity_live',
        lambda identity: route.identity_live and identity == route.identity)
    monkeypatch.setattr(_mod, '_catalog_model_for_current_env', lambda env: ('local', {'tools': True}))
    def prove(env, **kwargs):
        route.events.append('native-completion-proof')
        assert kwargs['return_proof'] and kwargs['require_exact_context']
        assert kwargs['env_still_current']()
        return route.proof
    monkeypatch.setattr(_mod, '_wait_for_model_readiness', prove)
    def consumer_proof(env, *, model):
        route.events.append('consumer-proof')
        assert env['ODS_MODE'] == 'local' and model == 'ods/current'
        doc, errors = _mod._switchboard_state.read_state(route.state_path)
        assert not errors
        if not router_accepts(doc):
            raise RuntimeError('router rejected reconstructed local route without completion proof')
        if route.fault == 'consumer-proof':
            raise RuntimeError('injected consumer failure after publication')
    monkeypatch.setattr(_mod, '_verify_litellm_route', consumer_proof)
    return route


@pytest.mark.parametrize('present', [False, True])
def test_restoration_publishes_proven_route_before_gateway(reconstructed, present):
    route = reconstructed
    route.present = present
    result = _mod._deactivate_remote_provider_route()
    assert result['restored'] and result['proven']
    assert _mod.load_env(route.env)['ODS_MODE'] == 'local'
    doc, errors = _mod._switchboard_state.read_state(route.state_path)
    assert not errors and router_accepts(doc)
    order = ['health', 'native-completion-proof', 'consumer-start', 'consumer-proof', 'apply']
    assert [route.events.index(x) for x in order] == sorted(route.events.index(x) for x in order)


@pytest.mark.parametrize('proof', [None, False, {},
    {'identity': 'foreign.gguf', 'contextLength': 65536, 'contextVerified': True},
    {'identity': 'local.gguf', 'contextLength': 32768, 'contextVerified': True},
    {'identity': 'local.gguf', 'contextLength': 65536, 'contextVerified': False}])
def test_unproven_runtime_never_publishes_or_starts_consumer(reconstructed, proof):
    route = reconstructed
    route.proof = proof
    before = route.state_path.read_bytes()
    with pytest.raises(RuntimeError, match='did not pass'):
        _mod._deactivate_remote_provider_route()
    assert route.state_path.read_bytes() == before
    assert 'consumer-start' not in route.events
    assert _mod.load_env(route.env)['ODS_MODE'] == 'cloud'
    assert route.events[-1] == 'finish:rollback'


@pytest.mark.parametrize('change', ['birth-before', 'birth-during', 'environment', 'saved-model', 'saved-context', 'invalid-contract', 'malformed-state'])
def test_changed_or_invalid_route_is_not_published(reconstructed, monkeypatch, change):
    route = reconstructed
    prove = _mod._wait_for_model_readiness
    if change == 'birth-before':
        route.identity_live = False
    elif change in {'birth-during', 'environment'}:
        def changing_proof(env, **kwargs):
            result = prove(env, **kwargs)
            if change == 'birth-during':
                route.identity_live = False
            else:
                route.env.write_text(route.env.read_text().replace('CTX_SIZE=65536', 'CTX_SIZE=32768'))
            return result
        monkeypatch.setattr(_mod, '_wait_for_model_readiness', changing_proof)
    elif change == 'saved-model':
        route.previous['pixel'] = dict(route.local, model='another.gguf')
    elif change == 'saved-context':
        route.previous['pixel'] = dict(route.local, contextLength=32768)
    elif change == 'invalid-contract':
        route.previous['pixel'] = 'invalid'
    else:
        route.state_path.write_text('{invalid state')
    before = route.state_path.read_bytes()
    with pytest.raises(RuntimeError):
        _mod._deactivate_remote_provider_route()
    assert route.state_path.read_bytes() == before
    assert 'consumer-start' not in route.events
    assert _mod.load_env(route.env)['ODS_MODE'] == 'cloud'


def test_cloud_rollback_keeps_only_the_real_monotonic_local_proof(reconstructed):
    route = reconstructed
    before, _ = _mod._switchboard_state.read_state(route.state_path)
    route.fault = 'consumer-proof'
    with pytest.raises(RuntimeError, match='consumer failure'):
        _mod._deactivate_remote_provider_route()
    doc, errors = _mod._switchboard_state.read_state(route.state_path)
    assert not errors and router_accepts(doc) and doc['seq'] > before['seq']
    assert doc['active']['proof']['identity'] == route.proof['identity']
    assert _mod.load_env(route.env)['ODS_MODE'] == 'cloud'
    assert route.events.count('stop') == 1
    assert route.events[-2:] == ['proof:remote', 'finish:rollback']


def test_existing_verified_record_does_not_replace_fresh_runtime_proof(reconstructed):
    route = reconstructed
    _mod._publish_activation_route({}, 'local', route.proof, {'chat': True})
    before, _ = _mod._switchboard_state.read_state(route.state_path)
    assert router_accepts(before)
    _mod._deactivate_remote_provider_route()
    after, _ = _mod._switchboard_state.read_state(route.state_path)
    assert route.events.count('native-completion-proof') == 1
    assert after['seq'] == before['seq'] + 1


def test_measured_capabilities_survive_route_republication(reconstructed, monkeypatch):
    route = reconstructed
    traits = {'measured': True}
    expected = {'chat': True, 'tools': False, 'vision': False, 'agentViable': True}
    monkeypatch.setattr(_mod, '_model_profiles_mode', lambda env: 'enabled')
    monkeypatch.setattr(_mod, '_stored_profile_traits', lambda model_id: traits if model_id == 'local' else None)
    def profiled(capabilities, observed, model):
        assert observed is traits and model['tools'] is True
        return expected
    monkeypatch.setattr(_mod, '_profiled_capabilities', profiled)
    _mod._deactivate_remote_provider_route()
    doc, errors = _mod._switchboard_state.read_state(route.state_path)
    assert not errors and doc['active']['capabilities'] == expected
