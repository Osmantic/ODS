"""Real disposable filesystem transactions; callbacks are NOT runtime acceptance."""
import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'bin'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import provider_transaction as tx
import test_settings_transaction as settings_tests
from pixel_provider.store import StoreError
from test_settings_transaction import private_json, sha

owner = settings_tests.owner


@pytest.fixture
def provider_owner(owner):
    path, state, _, _ = owner
    config = json.loads(path.read_bytes())
    config['plugins']['entries']['pixel-ods']['hooks'] = {'allowConversationAccess': True}
    original = (json.dumps(config, indent=4) + '\n\n').encode()
    path.write_bytes(original)
    return path, state, original


def binding(revision=3):
    return {'schemaVersion': 1, 'activationId': str(uuid.uuid4()), 'revision': revision, 'allowCloud': False}


def change(owner, target, **overrides):
    path, state, _ = owner
    options = {'state_dir': str(state), 'binding': target, 'transaction_id': sha(uuid.uuid4().bytes),
        'expected_config_sha256': sha(path.read_bytes()), 'validate_config': lambda staged: True,
        'check_no_active_run': lambda: False, 'activate': lambda: 'verified'}
    options.update(overrides)
    return tx.change_provider(str(path), **options)


def recover(owner, transaction_id, **overrides):
    path, state, _ = owner
    options = {'state_dir': str(state), 'transaction_id': transaction_id,
        'expected_config_sha256': sha(path.read_bytes()), 'validate_config': lambda staged: True,
        'check_no_active_run': lambda: False, 'activate': lambda: 'verified'}
    options.update(overrides)
    return tx.recover_provider(str(path), **options)


def status(owner):
    return tx.provider_status(str(owner[0]), state_dir=str(owner[1]))


def test_activation_updates_and_deactivation_preserve_original_route(provider_owner):
    path, state, original = provider_owner
    target = binding()
    calls = []
    def validate(staged):
        assert path.read_bytes() == original
        assert Path(staged) != path and Path(staged).parent == path.parent
        assert json.loads(Path(staged).read_bytes())['agents']['list'][0]['model']['primary'] == 'ods-policy/managed'
        calls.append('validate')
        return True
    change(provider_owner, target, validate_config=validate)
    assert calls == ['validate']
    assert status(provider_owner)['runtimeVerified'] is False
    # Preserve unrelated edits made after activation, including runtime settings.
    config = json.loads(path.read_bytes())
    config['agents']['list'][0]['verboseDefault'] = 'on'
    private_json(path, config)
    change(provider_owner, binding(4))
    change(provider_owner, None)
    expected = json.loads(original)
    expected['agents']['list'][0]['verboseDefault'] = 'on'
    assert json.loads(path.read_bytes()) == expected
    assert not (state / tx.JOURNAL).exists()
    assert not (state / tx.MANAGED).exists()
    assert 'synthetic-secret' not in json.dumps(status(provider_owner))


def test_unavailable_requires_explicit_matching_recovery(provider_owner):
    path, state, original = provider_owner
    tid = 'a' * 64
    with pytest.raises(StoreError):
        change(provider_owner, binding(), transaction_id=tid, activate=lambda: 'unavailable')
    assert path.read_bytes() != original and (state / tx.JOURNAL).exists()
    with pytest.raises(StoreError):
        change(provider_owner, binding(4))
    with pytest.raises(StoreError):
        recover(provider_owner, 'b' * 64)
    recover(provider_owner, tid)
    assert path.read_bytes() == original
    assert not (state / tx.JOURNAL).exists()


def test_rejected_update_restores_immediate_previous_bytes_and_managed_state(provider_owner):
    change(provider_owner, binding())
    path, state, _ = provider_owner
    before = path.read_bytes()
    previous = (state / tx.MANAGED).read_bytes()
    outcomes = iter(['rejected', 'verified'])
    change(provider_owner, binding(4), activate=lambda: next(outcomes))
    assert path.read_bytes() == before
    assert (state / tx.MANAGED).read_bytes() == previous


def test_deactivation_failure_recovers_active_route(provider_owner):
    change(provider_owner, binding())
    path, state, _ = provider_owner
    before = path.read_bytes()
    with pytest.raises(StoreError):
        change(provider_owner, None, transaction_id='a' * 64, activate=lambda: 'unavailable')
    recover(provider_owner, 'a' * 64)
    assert path.read_bytes() == before and (state / tx.MANAGED).exists()


def test_recovery_refuses_unrelated_concurrent_changes(provider_owner):
    path, state, _ = provider_owner
    with pytest.raises(StoreError):
        change(provider_owner, binding(), transaction_id='a' * 64, activate=lambda: 'unavailable')
    config = json.loads(path.read_bytes())
    config['agents']['list'][0]['verboseDefault'] = 'on'
    private_json(path, config)
    changed = path.read_bytes()
    with pytest.raises(StoreError):
        recover(provider_owner, 'a' * 64)
    assert path.read_bytes() == changed and (state / tx.JOURNAL).exists()


@pytest.mark.parametrize('overrides', [
    {'expected_config_sha256': '0' * 64}, {'transaction_id': 'bad'},
    {'check_no_active_run': lambda: True}, {'validate_config': lambda staged: False},
])
def test_preflight_rejection_does_not_change_owner_config(provider_owner, overrides):
    # Validation and idle guards deliberately preserve controller error types.
    from pixel_access_mode import AccessModeError
    with pytest.raises((StoreError, AccessModeError)):
        change(provider_owner, binding(), **overrides)
    assert provider_owner[0].read_bytes() == provider_owner[2]


def test_settings_journal_blocks_provider_changes(provider_owner):
    private_json(provider_owner[1] / 'settings-journal.json', {'pending': True})
    with pytest.raises(StoreError):
        change(provider_owner, binding())
    assert provider_owner[0].read_bytes() == provider_owner[2]


def test_saved_plan_corruption_is_rejected_before_update(provider_owner):
    change(provider_owner, binding())
    path, state, _ = provider_owner
    before = path.read_bytes()
    private_json(state / tx.MANAGED, {'schemaVersion': 1})
    with pytest.raises(StoreError):
        change(provider_owner, binding(4))
    assert path.read_bytes() == before


def test_completed_transaction_id_cannot_be_reused(provider_owner):
    change(provider_owner, binding(), transaction_id='a' * 64)
    before = provider_owner[0].read_bytes()
    with pytest.raises(StoreError):
        change(provider_owner, binding(4), transaction_id='a' * 64)
    assert provider_owner[0].read_bytes() == before


def test_pending_provider_blocks_settings_and_access(provider_owner):
    import settings_transaction
    from pixel_access_mode import AccessModeRejected, _reject_pending_settings
    from pixel_settings.contract import SettingsError
    private_json(provider_owner[1] / tx.JOURNAL, {'pending': True})
    with pytest.raises(SettingsError, match='provider-recovery-required'):
        settings_transaction._no_pending_access(str(provider_owner[1]))
    with pytest.raises(AccessModeRejected) as result:
        _reject_pending_settings(str(provider_owner[1]))
    assert result.value.code == 'provider-recovery-required'


def test_recovery_rejects_journal_plan_swapped_after_interruption(provider_owner):
    with pytest.raises(StoreError):
        change(provider_owner, binding(), transaction_id='a' * 64, activate=lambda: 'unavailable')
    path, state, _ = provider_owner
    before = path.read_bytes()
    journal = json.loads((state / tx.JOURNAL).read_bytes())
    journal['next']['plan']['fields']['model']['before'] = 'another/original'
    private_json(state / tx.JOURNAL, journal)
    with pytest.raises(StoreError, match='provider-journal-mismatch'):
        recover(provider_owner, 'a' * 64)
    assert path.read_bytes() == before


def test_late_completion_does_not_override_pending_explicit_rollback(provider_owner, monkeypatch):
    path, state, original = provider_owner
    remove = tx.storage._remove
    def fail_unlink(directory, name):
        if name == tx.JOURNAL:
            raise OSError('injected journal removal failure')
        return remove(directory, name)
    monkeypatch.setattr(tx.storage, '_remove', fail_unlink)
    with pytest.raises(OSError):
        change(provider_owner, binding(), transaction_id='a' * 64)
    assert (state / tx.COMPLETED).exists() and (state / tx.JOURNAL).exists()
    assert status(provider_owner)['pending'] is True
    assert status(provider_owner)['completion'] is None
    monkeypatch.setattr(tx.storage, '_remove', remove)
    recover(provider_owner, 'a' * 64)
    assert path.read_bytes() == original
    assert status(provider_owner)['completion']['outcome'] == 'rolled-back'


def test_replay_of_original_frame_remains_stale_after_later_transactions(provider_owner):
    original_hash = sha(provider_owner[0].read_bytes())
    original_binding = binding()
    change(provider_owner, original_binding, transaction_id='a' * 64)
    change(provider_owner, binding(4), transaction_id='b' * 64)
    before = provider_owner[0].read_bytes()
    with pytest.raises(StoreError, match='provider-config-changed'):
        change(provider_owner, original_binding, transaction_id='a' * 64, expected_config_sha256=original_hash)
    assert provider_owner[0].read_bytes() == before
