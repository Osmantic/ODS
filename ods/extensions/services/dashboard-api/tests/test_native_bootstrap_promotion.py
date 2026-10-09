"""Exercise native bootstrap against the actual host transaction participant."""
import copy
import importlib.util
from pathlib import Path

import pytest

from test_model_transaction import controller, host  # noqa: F401

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location('native_bootstrap_promotion',
    ROOT / 'installers/macos/lib/pixel-native-model-promotion.py')
promotion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promotion)
OLD = 'Qwen3.5-2B-Q4_K_M.gguf'
NEW = 'Qwen3.5-9B-Q4_K_M.gguf'


@pytest.fixture
def native(controller, monkeypatch):  # noqa: F811
    _, state, calls, initial_control = controller
    def control(operation, request=None, *, config):
        result = initial_control(operation, request, config=config)
        if operation == 'model-finish' and request['outcome'] == 'rollback':
            state['contract'] = copy.deepcopy(host._read_pixel_model_journal()['previous'])
            result = copy.deepcopy(state)
        return result
    monkeypatch.setattr(host, '_runtime_model_control', control)
    root = host.INSTALL_DIR
    env_path = root / '.env'
    ini = root / 'models.ini'
    env_path.write_text(f'GGUF_FILE={OLD}\nCTX_SIZE=65536\nPIXEL_OPENWEBUI_KEY=fixture-private\n')
    ini.write_text('bootstrap\n')
    monkeypatch.setattr(host, '_pixel_model_config_paths', lambda: {'.env': env_path, 'config/llama-server/models.ini': ini})
    state['contract'] = dict(model=OLD, contextLength=65536, maxTokens=8192, reasoning=False)
    restarted = []
    monkeypatch.setattr(host, '_restart_macos_native_llama_server', lambda *args: restarted.append(host.load_env(env_path)))
    proof = dict(identity=NEW, contextLength=65536, contextVerified=True)
    def readiness(env, **kwargs):
        assert kwargs['require_exact_context'] and kwargs['return_proof']
        return dict(proof)
    monkeypatch.setattr(host, '_wait_for_model_readiness', readiness)
    monkeypatch.setattr(host, '_catalog_model_for_current_env', lambda env: ('qwen3.5-9b', {}))
    monkeypatch.setattr(host, '_normal_switchboard_mode', lambda env: 'disabled')
    original = env_path.read_bytes()
    return env_path, original, state, calls, control, restarted, proof


def stage(native):
    path, _, _, _, _, _, _ = native
    token = promotion.begin(host, host.load_env(path))
    path.write_text(path.read_text().replace(OLD, NEW))
    return token, host.load_env(path)


@pytest.mark.parametrize('same_model', [False, True])
def test_native_promotion_proves_and_commits_once(native, same_model):
    path, _, state, calls, _, restarted, proof = native
    if same_model:
        state['contract']['model'] = NEW
        path.write_text(path.read_text().replace(OLD, NEW))
    token, env = stage(native)
    promotion.promote(host, env, token, NEW, 65536)
    assert len(restarted) == 1
    assert state['contract']['model'] == proof['identity']
    assert state['status'] == 'completed' and state['outcome'] == 'commit'
    journal = host._read_pixel_model_journal()
    assert journal['phase'] == 'completed' and journal['target']['model'] == NEW
    assert 'fixture-private' not in host._pixel_model_journal_path().read_text()
    with pytest.raises(promotion.PromotionError, match='recovery-required'):
        promotion.promote(host, env, token, NEW, 65536)
    assert calls.count('model-begin') == calls.count('model-apply') == calls.count('model-finish') == 1
    assert len(restarted) == 1


def test_unproven_previous_contract_never_begins(native, monkeypatch):
    path, _, _, calls, _, restarted, _ = native
    monkeypatch.setattr(host, '_prove_pixel_model_contract', lambda *_: False)
    with pytest.raises(promotion.PromotionError, match='previous-contract-unverified'):
        promotion.begin(host, host.load_env(path))
    assert calls == ['model-status'] and restarted == []
    assert not host._pixel_model_journal_path().exists()


@pytest.mark.parametrize('bad_proof', [{}, {'identity': 'Other.gguf'}, {'contextLength': 32768}, {'contextVerified': False}])
def test_failed_runtime_proof_allows_only_proven_rollback(native, monkeypatch, bad_proof):
    path, original, state, calls, _, restarted, proof = native
    token, env = stage(native)
    proof.update(bad_proof)
    if not bad_proof:
        proof.clear()
    with pytest.raises(promotion.RuntimeNotReady):
        promotion.promote(host, env, token, NEW, 65536)
    assert state['status'] == 'held' and 'model-apply' not in calls
    path.write_bytes(original)
    monkeypatch.setattr(host, '_prove_pixel_model_contract', lambda *_: False)
    with pytest.raises(promotion.PromotionError, match='rollback-unverified'):
        promotion.rollback(host, host.load_env(path), token, restart_runtime=True)
    assert state['status'] == 'held' and 'model-finish' not in calls
    monkeypatch.setattr(host, '_prove_pixel_model_contract', lambda *_: True)
    promotion.rollback(host, host.load_env(path), token)
    assert state['status'] == 'completed' and state['outcome'] == 'rollback'
    assert [env['GGUF_FILE'] for env in restarted] == [NEW, OLD]


@pytest.mark.parametrize('operation,confirmed', [('model-apply', False), ('model-apply', True), ('model-finish', False), ('model-finish', True)])
def test_lost_mutation_reply_is_read_back_without_replay(native, monkeypatch, operation, confirmed):
    _, _, state, calls, control, restarted, _ = native
    token, env = stage(native)
    def lose_reply(op, request=None, *, config):
        if op == operation:
            if confirmed:
                control(op, request, config=config)
            else:
                calls.append(op)
            raise TimeoutError('fixture-lost-response')
        return control(op, request, config=config)
    monkeypatch.setattr(host, '_runtime_model_control', lose_reply)
    if confirmed:
        promotion.promote(host, env, token, NEW, 65536)
        assert state['status'] == 'completed'
    else:
        with pytest.raises(host._PixelModelTransactionUncertain):
            promotion.promote(host, env, token, NEW, 65536)
        saved = copy.deepcopy(host._read_pixel_model_journal())
        assert saved['phase'] == ('applying' if operation == 'model-apply' else 'committing')
        with pytest.raises(promotion.PromotionError, match='recovery-required'):
            promotion.rollback(host, env, token, restart_runtime=True)
        assert host._read_pixel_model_journal() == saved
        assert calls.count('model-finish') == (operation == 'model-finish')
    assert calls.count(operation) == 1 and len(restarted) == 1


def test_foreign_transaction_and_host_drift_never_restart(native, monkeypatch):
    _, _, _, calls, _, restarted, _ = native
    token, env = stage(native)
    with pytest.raises(promotion.PromotionError, match='recovery-required'):
        promotion.promote(host, env, 'f' * 64, NEW, 65536)
    paths = host._pixel_model_config_paths()
    path = host.INSTALL_DIR / 'foreign'
    path.write_text('changed')
    monkeypatch.setattr(host, '_pixel_model_config_paths', lambda: {**paths, 'foreign': path})
    with pytest.raises((RuntimeError, promotion.PromotionError)):
        promotion.promote(host, env, token, NEW, 65536)
    assert restarted == [] and 'model-apply' not in calls


def test_pending_controller_and_missing_managed_credentials_refuse(native):
    path, _, state, calls, _, restarted, _ = native
    state['pending'] = True
    with pytest.raises(promotion.PromotionError, match='previous-contract-unverified'):
        promotion.begin(host, host.load_env(path))
    with pytest.raises(promotion.PromotionError, match='configuration-required'):
        promotion.begin(host, {'PIXEL_NATIVE_CONFIG_PATH': '/private/fixture'})
    assert calls == ['model-status'] and restarted == []


def test_configuration_changed_during_apply_remains_held(native, monkeypatch):
    path, _, state, calls, control, _, _ = native
    token, env = stage(native)
    def drift(operation, request=None, *, config):
        result = control(operation, request, config=config)
        if operation == 'model-apply':
            path.write_text(path.read_text() + 'CTX_SIZE=32768\n')
        return result
    monkeypatch.setattr(host, '_runtime_model_control', drift)
    with pytest.raises(promotion.PromotionError, match='host-state-changed'):
        promotion.promote(host, env, token, NEW, 65536)
    assert state['pending'] and state['status'] == 'applied'
    assert 'model-finish' not in calls
    assert host._read_pixel_model_journal()['phase'] == 'applied'


def test_router_target_published_before_pixel_apply(native, monkeypatch):
    _, _, _, calls, _, _, _ = native
    token, env = stage(native)
    monkeypatch.setattr(host, '_normal_switchboard_mode', lambda env: 'enabled')
    def publish(env, model_id, proof, capabilities):
        assert proof['identity'] == NEW and proof['contextVerified']
        assert 'model-apply' not in calls
        calls.append('publish')
    monkeypatch.setattr(host, '_publish_activation_route', publish)
    promotion.promote(host, env, token, NEW, 65536)
    assert calls.index('publish') < calls.index('model-apply') < calls.index('model-finish')


def test_unchanged_failure_releases_only_matching_proven_state(native):
    path, _, state, calls, _, restarted, _ = native
    token = promotion.begin(host, host.load_env(path))
    promotion.rollback(host, host.load_env(path), token)
    assert state['status'] == 'completed' and state['outcome'] == 'rollback'
    assert restarted == [] and 'model-apply' not in calls


def test_runtime_rollback_waits_for_exact_previous_context(native):
    path, original, state, calls, _, restarted, proof = native
    token, _ = stage(native)
    path.write_bytes(original)
    proof.update(identity=OLD, contextVerified=True, contextLength=65536)
    promotion.rollback(host, host.load_env(path), token, restart_runtime=True)
    assert restarted[0]['GGUF_FILE'] == OLD
    assert state['status'] == 'completed' and state['outcome'] == 'rollback'
    assert 'model-apply' not in calls


def test_settings_drift_during_route_publication_does_not_apply(native, monkeypatch):
    path, _, state, calls, _, _, _ = native
    token, env = stage(native)
    monkeypatch.setattr(host, '_normal_switchboard_mode', lambda env: 'enabled')
    monkeypatch.setattr(host, '_publish_activation_route',
        lambda *_: path.write_text(path.read_text() + 'OWNER_SETTING=changed\n'))
    with pytest.raises(promotion.PromotionError, match='host-state-changed'):
        promotion.promote(host, env, token, NEW, 65536)
    assert state['status'] == 'held' and state['pending']
    assert 'model-apply' not in calls and 'model-finish' not in calls
