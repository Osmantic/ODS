"""Owner-side release access migration; the root caller retains admission.

No service, approval or permission mode is selected here. A protected caller
must supply reviewed hashes, keep admission held, and prove the runtime before
removing the journal. Config and receipt publication are recoverable, not a
claim that two filesystem replacements are one atomic operation.
"""
import os

import pixel_access_mode as controller
import settings_transaction as storage
from pixel_settings.contract import SettingsError

JOURNAL = 'access-release-journal.json'
FILES = ('access-release-config-before.json', 'access-release-config-after.json',
         'access-release-receipt-before.json', 'access-release-receipt-after.json')
COMPLETED = 'access-release-completed.json'


def _requirements(transaction_id, idle):
    if not storage._hash(transaction_id) or not callable(idle):
        raise SettingsError('access-release-contract')


def _unprepared_state(config_path, sd, expected_current, expected_receipt=None):
    # lexists also refuses a dangling or replaced journal; absence is never
    # inferred from a JSON decoding error or an unavailable worker.
    if os.path.lexists(os.path.join(sd, JOURNAL)):
        raise SettingsError('access-release-prepared-recovery-required')
    controller._reject_pending_settings(sd)
    path, mode, raw, config = controller._load_config(config_path)
    receipt = controller._load_receipt(sd)
    if (controller._sha256_bytes(raw) != expected_current or receipt is None
            or receipt['status'] != controller.STATUS_ENABLED or receipt['config_path'] != path):
        raise SettingsError('access-release-original-state-changed')
    receipt_sha = controller._sha256_bytes(storage._encoded(receipt))
    if expected_receipt is not None and receipt_sha != expected_receipt:
        raise SettingsError('access-release-original-receipt-changed')
    enabled, _ = controller._helper.enable(config, receipt['baseline'])
    if storage._encoded(enabled) != storage._encoded(config):
        raise SettingsError('access-release-original-state-changed')
    return path, mode, raw, receipt, receipt_sha


def baseline(config_path, state_dir, *, transaction_id, expected_current, check_no_active_run):
    """Read exact pre-attempt identity for the root journal, without mutation."""
    _requirements(transaction_id, check_no_active_run)
    if not storage._hash(expected_current):
        raise SettingsError('access-release-contract')
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd, exclusive=True):
        before = _unprepared_state(config_path, sd, expected_current)
        controller._require_idle(check_no_active_run)
        if _unprepared_state(config_path, sd, expected_current) != before:
            raise SettingsError('access-release-original-state-changed')
        return dict(configSha256=expected_current, receiptSha256=before[4])


def abort_unprepared(config_path, state_dir, *, transaction_id, expected_current,
                     expected_receipt, verify_runtime, check_no_active_run):
    """Prove an untouched rejected attempt; never clear a prepared journal."""
    _requirements(transaction_id, check_no_active_run)
    if (not storage._hash(expected_current) or not storage._hash(expected_receipt)
            or not callable(verify_runtime)):
        raise SettingsError('access-release-contract')
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd, exclusive=True):
        before = _unprepared_state(config_path, sd, expected_current, expected_receipt)
        controller._require_idle(check_no_active_run)
        # The root hook probes the native runtime directly. It must not launch
        # another owner worker while this process holds apply.lock.
        if verify_runtime(expected_current) != 'verified':
            raise SettingsError('access-release-runtime-unverified')
        controller._require_idle(check_no_active_run)
        if _unprepared_state(config_path, sd, expected_current, expected_receipt) != before:
            raise SettingsError('access-release-original-state-changed')
        return dict(configSha256=expected_current)


def prepare(config_path, candidate_path, state_dir, *, transaction_id,
            expected_current, expected_candidate, expected_receipt, validate_config, check_no_active_run):
    _requirements(transaction_id, check_no_active_run)
    if (not storage._hash(expected_current) or not storage._hash(expected_candidate)
            or not storage._hash(expected_receipt) or not callable(validate_config)):
        raise SettingsError('access-release-contract')
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd, exclusive=True):
        existing = storage._record(sd, JOURNAL)
        if existing is None:
            controller._reject_pending_settings(sd)
        path, mode, before, config = controller._load_config(config_path)
        candidate_path, _, candidate_raw, candidate = controller._load_config(candidate_path)
        if (path == candidate_path or controller._sha256_bytes(before) != expected_current
                or controller._sha256_bytes(candidate_raw) != expected_candidate):
            raise SettingsError('access-release-input-changed')
        receipt = controller._load_receipt(sd)
        if (receipt is None or receipt['status'] != controller.STATUS_ENABLED
                or receipt['config_path'] != path):
            raise SettingsError('access-release-receipt-unbound')
        if controller._sha256_bytes(storage._encoded(receipt)) != expected_receipt:
            raise SettingsError('access-release-original-receipt-changed')
        # Access receipts retain the original five-field restore baseline;
        # their hash is the enable-time snapshot, not a restriction on later
        # model/settings changes. The root caller supplies a freshly proved
        # current hash above, and rebase_enabled verifies all five current
        # overrides below. Requiring the historical receipt hash here would
        # reject legitimate ODS runtime overlays on the next release update.
        migrated, baseline = controller._helper.rebase_enabled(config, candidate, receipt['baseline'])
        after = storage._encoded(migrated)
        next_receipt = dict(receipt, baseline=baseline, config_sha256=controller._sha256_bytes(after))
        staged = controller._stage_and_validate(path, after, mode,
            controller._normalize_validate(validate_config), 'release access')
        try:
            controller._require_idle(check_no_active_run)
            if (controller._load_config(path)[2] != before
                    or controller._load_config(candidate_path)[2] != candidate_raw
                    or controller._load_receipt(sd) != receipt):
                raise SettingsError('access-release-input-changed')
            records = (before, after, storage._encoded(receipt), storage._encoded(next_receipt))
            journal = dict(transactionId=transaction_id, configPath=path,
                configMode=mode, candidateSha=expected_candidate, hashes=[controller._sha256_bytes(raw) for raw in records])
            if existing is not None:
                # A coordinator may lose the prepare reply before any live
                # publication. Retry only the identical validated inputs and
                # private snapshots; never overwrite another pending attempt.
                if existing != journal or any(
                        storage._read_private(os.path.join(sd, name)) != raw
                        for name, raw in zip(FILES, records)):
                    raise SettingsError('access-release-recovery-required')
                return {'beforeSha': expected_current, 'afterSha': next_receipt['config_sha256']}
            for name, raw in zip(FILES, records):
                controller._atomic_write(os.path.join(sd, name), raw, 0o600, durable=True)
            storage._write(sd, JOURNAL, journal)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
        return {'beforeSha': expected_current, 'afterSha': next_receipt['config_sha256']}


def recover(config_path, state_dir, *, transaction_id, outcome,
            validate_config, check_no_active_run):
    """Replay publication or rollback; retain the journal for root validation."""
    _requirements(transaction_id, check_no_active_run)
    if outcome not in ('apply', 'rollback') or not callable(validate_config):
        raise SettingsError('access-release-contract')
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd, exclusive=True):
        path, mode, current, _ = controller._load_config(config_path)
        journal = storage._record(sd, JOURNAL)
        if (type(journal) is not dict or set(journal) != {'transactionId', 'configPath', 'configMode', 'candidateSha', 'hashes'}
                or journal['transactionId'] != transaction_id or journal['configPath'] != path
                or type(journal['configMode']) is not int or journal['configMode'] != mode
                or not storage._hash(journal['candidateSha'])
                or type(journal['hashes']) is not list or len(journal['hashes']) != 4
                or not all(storage._hash(value) for value in journal['hashes'])):
            raise SettingsError('access-release-journal-invalid')
        records = [storage._read_private(os.path.join(sd, name)) for name in FILES]
        if [controller._sha256_bytes(raw) for raw in records] != journal['hashes']:
            raise SettingsError('access-release-backup-changed')
        old_receipt, new_receipt = map(storage._decode, records[2:])
        current_receipt = controller._load_receipt(sd)
        if (current not in records[:2] or current_receipt not in (old_receipt, new_receipt)):
            raise SettingsError('access-release-state-changed')
        index = 0 if outcome == 'rollback' else 1
        desired, receipt = records[index], (old_receipt, new_receipt)[index]
        staged = controller._stage_and_validate(path, desired, mode,
            controller._normalize_validate(validate_config), 'release access recovery')
        try:
            controller._require_idle(check_no_active_run)
            if controller._load_receipt(sd) != current_receipt:
                raise SettingsError('access-release-state-changed')
            controller._checked_replace(path, desired, controller._sha256_bytes(current), staged, durable=True)
            controller._write_receipt(sd, receipt, durable=True)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
        return {'configSha256': controller._sha256_bytes(desired), 'outcome': outcome}


def finish(config_path, state_dir, *, transaction_id, outcome,
           verify_runtime, check_no_active_run):
    """Retire recovery only after the protected caller proves this exact state.

    The callback is supplied by the trusted caller, never from a socket request.
    This function does not release admission or authenticate the caller itself.
    """
    _requirements(transaction_id, check_no_active_run)
    if outcome not in ('apply', 'rollback') or not callable(verify_runtime):
        raise SettingsError('access-release-contract')
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd, exclusive=True):
        path, mode, current, _ = controller._load_config(config_path)
        journal = storage._record(sd, JOURNAL)
        if journal is None:
            # A lost worker reply must be distinguishable from an unfinished
            # migration. A completion receipt is not a runtime attestation:
            # require the protected caller to prove the live state again.
            completed = storage._record(sd, COMPLETED)
            receipt = controller._load_receipt(sd)
            if (type(completed) is not dict
                    or set(completed) != {'transactionId', 'configPath', 'configSha256', 'receiptSha256', 'outcome'}
                    or completed['transactionId'] != transaction_id
                    or completed['configPath'] != path or completed['outcome'] != outcome
                    or not storage._hash(completed['configSha256'])
                    or not storage._hash(completed['receiptSha256'])
                    or controller._sha256_bytes(current) != completed['configSha256']
                    or receipt is None
                    or controller._sha256_bytes(storage._encoded(receipt)) != completed['receiptSha256']):
                raise SettingsError('access-release-completion-invalid')
            controller._reject_pending_settings(sd)
            controller._require_idle(check_no_active_run)
            if verify_runtime(completed['configSha256']) != 'verified':
                raise SettingsError('access-release-runtime-unverified')
            controller._require_idle(check_no_active_run)
            controller._reject_pending_settings(sd)
            if (controller._load_config(path)[:3] != (path, mode, current)
                    or controller._load_receipt(sd) != receipt
                    or storage._record(sd, COMPLETED) != completed):
                raise SettingsError('access-release-state-changed')
            return completed
        if (type(journal) is not dict or set(journal) != {'transactionId', 'configPath', 'configMode', 'candidateSha', 'hashes'}
                or journal['transactionId'] != transaction_id or journal['configPath'] != path
                or type(journal['configMode']) is not int or journal['configMode'] != mode
                or not storage._hash(journal['candidateSha'])
                or type(journal['hashes']) is not list or len(journal['hashes']) != 4
                or not all(storage._hash(value) for value in journal['hashes'])):
            raise SettingsError('access-release-journal-invalid')
        index = 0 if outcome == 'rollback' else 1
        expected = journal['hashes'][index]
        receipt = controller._load_receipt(sd)
        if (controller._sha256_bytes(current) != expected or receipt is None
                or controller._sha256_bytes(storage._encoded(receipt)) != journal['hashes'][index + 2]):
            raise SettingsError('access-release-state-changed')
        controller._require_idle(check_no_active_run)
        if verify_runtime(expected) != 'verified':
            raise SettingsError('access-release-runtime-unverified')
        # The proof may run a subprocess. Do not retire recovery if state moved
        # while it ran, even when that process reported successful verification.
        controller._require_idle(check_no_active_run)
        if (controller._load_config(path)[:3] != (path, mode, current)
                or controller._load_receipt(sd) != receipt
                or storage._record(sd, JOURNAL) != journal):
            raise SettingsError('access-release-state-changed')
        completed = dict(transactionId=transaction_id, configPath=path,
                         configSha256=expected, receiptSha256=journal['hashes'][index + 2], outcome=outcome)
        storage._write(sd, COMPLETED, completed)
        try:
            storage._remove(sd, JOURNAL)
        except OSError:
            storage._write(sd, JOURNAL, journal)
            raise
        return completed
