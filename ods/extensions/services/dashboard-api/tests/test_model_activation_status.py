"""Only host-owned phases and explicit outcomes may label model recovery."""
import pytest

from model_activation_status import model_activation_status
from test_pixel import pixel, pixel_env as pixel_env


@pytest.mark.parametrize('phase', ['preparing', 'loading', 'verifying', 'rolling_back', 'rollback_verifying'])
def test_owned_phase_is_projected_without_private_diagnostics(phase):
    value = model_activation_status({'lifecycleActive': True, 'activeOperation': 'model_activation',
        'activationPhase': phase, 'activationFailureCode': 'runtime_load_failed', 'error': '/private/raw failure',
        'activationResult': {'outcome': 'rolled_back', 'failureCode': None}})
    assert value == {'active': True, 'phase': phase, 'failureCode': 'runtime_load_failed'}


@pytest.mark.parametrize('value', [None, {}, {'modelTransactionPending': True},
    {'activationPhase': 'rolling_back'},
    {'lifecycleActive': True, 'activeOperation': 'model_activation', 'activationPhase': 'crashed'},
    {'lifecycleActive': True, 'activeOperation': 'model_activation', 'activationPhase': 'loading', 'activationFailureCode': {}},
    {'lifecycleActive': True, 'activeOperation': 'model_download', 'activationResult': {'outcome': 'rolled_back'}},
    {'activationResult': {'outcome': 'invented', 'failureCode': None}}])
def test_unknown_pending_or_unowned_state_is_not_inferred(value):
    assert model_activation_status(value) is None


@pytest.mark.parametrize('outcome', ['activated', 'rolled_back', 'rollback_unconfirmed'])
def test_terminal_outcome_must_be_explicit(outcome):
    assert model_activation_status({'activationResult': {'outcome': outcome, 'failureCode': None, 'private': 'discard'}}) == {
        'active': False, 'outcome': outcome, 'failureCode': None}


@pytest.mark.parametrize('operation', ['pixel_startup_reproof', 'pixel_access_mode', 'pixel_open_app',
                                     'pixel_providers', 'pixel_settings'])
def test_neutral_lifecycle_keeps_the_explicit_terminal_result(operation):
    assert model_activation_status({'lifecycleActive': True, 'activeOperation': operation,
        'activationResult': {'outcome': 'rolled_back', 'failureCode': 'runtime_load_failed'}}) == {
            'active': False, 'outcome': 'rolled_back', 'failureCode': 'runtime_load_failed'}


@pytest.mark.asyncio
async def test_portal_projection_keeps_existing_admission_gate_during_owned_rollback(monkeypatch, pixel_env):
    async def status():
        return {'lifecycleActive': True, 'activeOperation': 'model_activation', 'modelTransactionPending': True,
                'activationPhase': 'rolling_back', 'activationFailureCode': 'runtime_load_failed'}
    monkeypatch.setattr(pixel, '_host_model_status', status)
    value = await pixel.pixel_status()
    assert value['available'] is False and value['state'] == 'model_switching'
    assert value['modelActivation'] == {'active': True, 'phase': 'rolling_back', 'failureCode': 'runtime_load_failed'}


@pytest.mark.asyncio
async def test_terminal_result_does_not_override_unfinished_transaction(monkeypatch, pixel_env):
    async def status():
        return {'modelTransactionPending': True, 'activationResult': {'outcome': 'activated', 'failureCode': None}}
    monkeypatch.setattr(pixel, '_host_model_status', status)
    value = await pixel.pixel_status()
    assert value['available'] is False and value['state'] == 'model_switching'
