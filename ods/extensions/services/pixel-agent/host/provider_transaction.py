"""Owner-side provider config changes under the existing access apply.lock.

The root coordinator retains revision/source qualification, deployment environment,
admission and restart authority. The callback verifies registration, NOT inference.
This module neither selects a service nor accepts arbitrary configuration documents.
Private owner receipts are recovery data, not root authorization.
Transaction IDs are root-generated, never public caller input. The bounded last
completion resolves a lost reply; it is not an indefinite transaction-ID ledger.
"""
import os

import pixel_access_mode as controller
import settings_transaction as storage
from pixel_provider.activation_config import (
    _binding,
    _validate_plan,
    plan_activation,
    restore_activation,
    update_activation,
)
from pixel_provider.store import StoreError
from pixel_settings.contract import SettingsError

JOURNAL = 'provider-journal.json'
MANAGED = 'provider-managed.json'
BACKUP = 'provider-before.json'
COMPLETED = 'provider-completed.json'


def _io(function, *args):
    # Reuse the same strict private IO and durable replacement primitives.
    try:
        return function(*args)
    except SettingsError:
        raise StoreError('invalid-provider-state') from None


def _encoded(value):
    return _io(storage._encoded, value)


def _record(sd, name):
    return _io(storage._record, sd, name)


def _write(sd, name, value):
    return _io(storage._write, sd, name, value)


def _header(value, keys, path):
    if (type(value) is not dict or set(value) != keys
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['kind'] != 'provider' or value['configPath'] != path):
        raise StoreError('invalid-provider-state')


def _validate_binding(value):
    if value is None:
        return None
    if type(value) is not dict or set(value) != {'schemaVersion', 'activationId', 'revision', 'allowCloud'}:
        raise StoreError('invalid-activation-binding')
    expected = _binding(value['revision'], value['allowCloud'], value['activationId'])
    if _encoded(expected) != _encoded(value):
        raise StoreError('invalid-activation-binding')
    return expected


def _managed(value, path):
    if value is not None:
        _header(value, {'schemaVersion', 'kind', 'configPath', 'plan'}, path)
        _validate_plan(value['plan'])
    return value


def _active(value):
    return value['plan']['fields']['binding']['after'] if value else None


def _completed(value, path):
    if value is not None:
        _header(value, {'schemaVersion', 'kind', 'configPath', 'transactionId', 'binding', 'outcome', 'configSha256'}, path)
        if (not storage._hash(value['transactionId']) or not storage._hash(value['configSha256'])
                or value['outcome'] not in ('applied', 'rolled-back')):
            raise StoreError('invalid-provider-state')
        _validate_binding(value['binding'])
    return value


def _journal(value, path, mode):
    _header(value, {'schemaVersion', 'kind', 'configPath', 'configMode', 'transactionId',
                    'beforeSha', 'afterSha', 'previous', 'next'}, path)
    if (type(value['configMode']) is not int or value['configMode'] != mode
            or any(not storage._hash(value[key]) for key in ('transactionId', 'beforeSha', 'afterSha'))
            or value['previous'] is None and value['next'] is None):
        raise StoreError('invalid-provider-state')
    _managed(value['previous'], path)
    _managed(value['next'], path)
    return value


def _guards(sd):
    receipt = controller._load_receipt(sd)
    if receipt and receipt.get('status') == controller.STATUS_PENDING:
        raise StoreError('access-recovery-required')
    if os.path.lexists(os.path.join(sd, storage.JOURNAL)):
        raise StoreError('settings-recovery-required')


def _requirements(checksum, transaction, validate, idle, activate):
    if (not storage._hash(checksum) or not storage._hash(transaction)
            or not all(callable(hook) for hook in (validate, idle, activate))):
        raise StoreError('provider-transaction-contract')


def _activate(activate):
    result = activate()
    if type(result) is not str or result not in ('verified', 'rejected', 'unavailable'):
        raise StoreError('provider-activation-contract')
    if result == 'unavailable':
        raise StoreError('provider-activation-unavailable')
    return result == 'verified'


def _check(path, checksum, mode):
    _, actual_mode, raw, _ = controller._load_config(path)
    if actual_mode != mode or controller._sha256_bytes(raw) != checksum:
        raise StoreError('provider-config-changed')


def _finish(sd, journal, outcome):
    state = journal['next'] if outcome == 'applied' else journal['previous']
    if state is None:
        _io(storage._remove, sd, MANAGED)
    else:
        _write(sd, MANAGED, state)
    checksum = journal['afterSha' if outcome == 'applied' else 'beforeSha']
    receipt = {'schemaVersion': 1, 'kind': 'provider', 'configPath': journal['configPath'],
        'transactionId': journal['transactionId'], 'binding': _active(state), 'outcome': outcome, 'configSha256': checksum}
    _write(sd, COMPLETED, receipt)
    try:
        _io(storage._remove, sd, JOURNAL)
    except OSError:
        _write(sd, JOURNAL, journal)  # Uncertain unlink durability is not success.
        raise
    return {'status': 'registration-verified' if outcome == 'applied' else 'rolled-back',
            'binding': _active(state), 'configSha256': checksum}


def _rollback(path, sd, journal, validate, idle, activate):
    before = _io(storage._read_private, os.path.join(sd, BACKUP))
    if controller._sha256_bytes(before) != journal['beforeSha']:
        raise StoreError('provider-backup-mismatch')
    # Recompute the exact transition from its immutable before-image. A valid
    # standalone plan must not be swapped into another transaction's journal.
    document, next_state = _plan(_io(storage._decode, before), journal['previous'], _active(journal['next']), path)
    if (controller._sha256_bytes(_encoded(document)) != journal['afterSha']
            or _encoded(next_state) != _encoded(journal['next'])):
        raise StoreError('provider-journal-mismatch')
    _, mode, raw, _ = controller._load_config(path)
    checksum = controller._sha256_bytes(raw)
    if mode != journal['configMode'] or checksum not in (journal['beforeSha'], journal['afterSha']):
        raise StoreError('provider-rollback-conflict')
    if checksum != journal['beforeSha']:
        staged = controller._stage_and_validate(path, before, mode, validate, 'provider rollback')
        try:
            controller._require_idle(idle, staged)
            _check(path, checksum, mode)
            controller._checked_replace(path, before, checksum, staged, durable=True)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
    controller._require_idle(idle)
    if not _activate(activate):
        raise StoreError('provider-rollback-unverified')
    controller._require_idle(idle)
    _check(path, journal['beforeSha'], mode)
    return _finish(sd, journal, 'rolled-back')


def _plan(config, previous, binding, path):
    if binding is None:
        if previous is None:
            raise StoreError('provider-not-managed')
        return restore_activation(config, previous['plan']), None
    options = {'revision': binding['revision'], 'allow_cloud': binding['allowCloud'], 'activation_id': binding['activationId']}
    plan = (update_activation(config, previous['plan'], **options) if previous
            else plan_activation(config, **options))
    return plan['document'], {'schemaVersion': 1, 'kind': 'provider', 'configPath': path, 'plan': plan}


def change_provider(config_path, *, state_dir, binding, transaction_id, expected_config_sha256,
                    validate_config, check_no_active_run, activate, expected_projection=None):
    _requirements(expected_config_sha256, transaction_id, validate_config, check_no_active_run, activate)
    binding = _validate_binding(binding)
    sd = controller._prepare_state_dir(state_dir)
    validate = controller._normalize_validate(validate_config)
    with controller._Lock(sd):
        _guards(sd)
        if os.path.lexists(os.path.join(sd, JOURNAL)):
            raise StoreError('provider-recovery-required')
        path, mode, before, _ = controller._load_config(config_path)
        if len(before) > storage.MAX_BYTES or controller._sha256_bytes(before) != expected_config_sha256:
            raise StoreError('provider-config-changed')
        config = _io(storage._decode, before)
        completed = _completed(_record(sd, COMPLETED), path)
        if completed and completed['transactionId'] == transaction_id:
            raise StoreError('provider-transaction-already-completed')
        previous = _managed(_record(sd, MANAGED), path)
        document, next_state = _plan(config, previous, binding, path)
        after = _encoded(document)
        if expected_projection is not None:
            # The root coordinator independently owns the original baseline and
            # projection. Compare UNDER apply.lock, before staging or writing.
            expected = {'afterSha': controller._sha256_bytes(after),
                        'previousPlanSha': controller._sha256_bytes(_encoded(previous['plan'])) if previous else None}
            if (type(expected_projection) is not dict or set(expected_projection) != set(expected)
                    or expected_projection != expected):
                raise StoreError('provider-root-projection-mismatch')
        journal = {'schemaVersion': 1, 'kind': 'provider', 'configPath': path, 'configMode': mode,
            'transactionId': transaction_id, 'beforeSha': expected_config_sha256,
            'afterSha': controller._sha256_bytes(after), 'previous': previous, 'next': next_state}
        _journal(journal, path, mode)
        _encoded(journal)
        controller._require_idle(check_no_active_run)
        staged = controller._stage_and_validate(path, after, mode, validate, 'provider apply')
        try:
            controller._require_idle(check_no_active_run, staged)
            _check(path, expected_config_sha256, mode)
            controller._atomic_write(os.path.join(sd, BACKUP), before, 0o600, durable=True)
            _write(sd, JOURNAL, journal)
            controller._require_idle(check_no_active_run, staged)
            _check(path, expected_config_sha256, mode)
            controller._checked_replace(path, after, expected_config_sha256, staged, durable=True)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
        if not _activate(activate):
            return _rollback(path, sd, journal, validate, check_no_active_run, activate)
        controller._require_idle(check_no_active_run)
        _check(path, journal['afterSha'], mode)
        return _finish(sd, journal, 'applied')


def recover_provider(config_path, *, state_dir, transaction_id, expected_config_sha256,
                     validate_config, check_no_active_run, activate):
    """Explicitly roll back an unacknowledged transaction, including late crashes.

    A pending journal wins over a written completion. The root coordinator must
    obtain rollback consent and preserve admission until the old registration is
    verified. A completion is consumable as success only once no journal remains.
    """
    _requirements(expected_config_sha256, transaction_id, validate_config, check_no_active_run, activate)
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd):
        _guards(sd)
        path, mode, raw, _ = controller._load_config(config_path)
        if controller._sha256_bytes(raw) != expected_config_sha256:
            raise StoreError('provider-config-changed')
        journal = _journal(_record(sd, JOURNAL), path, mode)
        if journal['transactionId'] != transaction_id:
            raise StoreError('provider-recovery-conflict')
        return _rollback(path, sd, journal, controller._normalize_validate(validate_config), check_no_active_run, activate)


def provider_status(config_path, *, state_dir):
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd, exclusive=False):
        path, _, raw, _ = controller._load_config(config_path)
        managed = _managed(_record(sd, MANAGED), path)
        completed = _completed(_record(sd, COMPLETED), path)
        pending = os.path.lexists(os.path.join(sd, JOURNAL))
        return {'configSha256': controller._sha256_bytes(raw), 'binding': _active(managed),
                'pending': pending, 'runtimeVerified': False,
                'completion': ({key: completed[key] for key in ('transactionId', 'binding', 'outcome', 'configSha256')}
                               if completed and not pending else None)}
