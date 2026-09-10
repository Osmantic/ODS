"""Owner-side settings transaction, called only under the existing host hold.

Uses the access controller's SAME apply.lock, staging, validator and replacement
primitives. No service restart or path is selected here. The trusted host callback
must restart, reacquire its hold, and compare current-process settings; fixtures
cannot establish installed acceptance. Same-UID hostile writers are not isolated.
"""
import json
import os
import stat

import pixel_access_mode as controller
from pixel_settings.contract import SettingsError
from pixel_settings.projection import plan_preferences, canonical, _validate_state

JOURNAL = "settings-journal.json"
MANAGED = "settings-managed.json"
BACKUP = "settings-before.json"
COMPLETED = "settings-completed.json"
MAX_BYTES = 1024 * 1024


def _read_private(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise SettingsError("unsafe-settings-state") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > MAX_BYTES):
            raise SettingsError("unsafe-settings-state")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise SettingsError("unsafe-settings-state")
        return raw
    finally:
        os.close(fd)


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        canonical(value)
    except (ValueError, UnicodeError, RecursionError):
        raise SettingsError("invalid-settings-state") from None
    return value


def _header(value, keys, path):
    if (type(value) is not dict or set(value) != keys
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or value["kind"] != "settings" or value["configPath"] != path):
        raise SettingsError("invalid-settings-state")


def _managed(value, path):
    if value is not None:
        _header(value, {"schemaVersion", "kind", "configPath", "state", "settingsRevision"}, path)
        if type(value["settingsRevision"]) is not int or not 0 <= value["settingsRevision"] <= 2**53 - 1:
            raise SettingsError("invalid-settings-state")
        _validate_state(value["state"])
    return value


def _record(sd, name):
    path = os.path.join(sd, name)
    return _decode(_read_private(path)) if os.path.lexists(path) else None


def _encoded(value):
    raw = canonical(value).encode("ascii") + b"\n"
    if len(raw) > MAX_BYTES:
        raise SettingsError("invalid-settings-state")
    return raw


def _write(sd, name, value):
    controller._atomic_write(os.path.join(sd, name), _encoded(value), 0o600, durable=True)


def _remove(sd, name):
    path = os.path.join(sd, name)
    if os.path.lexists(path):
        _read_private(path)
        os.unlink(path)
        fd = os.open(sd, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _hash(value):
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _requirements(expected_hash, validate_config, check_no_active_run, activate):
    if (not _hash(expected_hash)
            or not all(callable(hook) for hook in (validate_config, check_no_active_run, activate))):
        raise SettingsError("settings-transaction-contract")


def _no_pending_access(sd):
    if os.path.lexists(os.path.join(sd, "provider-journal.json")):
        raise SettingsError("provider-recovery-required")
    access = controller._load_receipt(sd)
    if access is not None and access.get("status") == controller.STATUS_PENDING:
        raise SettingsError("access-recovery-required")


def _activate(activate):
    # Unknown transport/process state is NOT a proven mismatch. Leave the
    # durable journal and host gates held; only explicit recovery may retry it.
    result = activate()
    if result not in ("verified", "rejected", "unavailable") or type(result) is not str:
        raise SettingsError("settings-activation-contract")
    if result == "unavailable":
        raise SettingsError("settings-activation-unavailable")
    return result == "verified"


def _completion(value, path):
    if value is None:
        return None
    _header(value, {"schemaVersion", "kind", "configPath", "transactionId", "settingsRevision", "outcome", "configSha256"}, path)
    if (not _hash(value["transactionId"]) or not _hash(value["configSha256"])
            or type(value["settingsRevision"]) is not int or not 0 <= value["settingsRevision"] <= 2**53 - 1
            or value["outcome"] not in ("applied", "rolled-back")):
        raise SettingsError("invalid-settings-state")
    return value


def _finish(sd, managed, journal, outcome):
    if managed is None:
        _remove(sd, MANAGED)
    else:
        _write(sd, MANAGED, managed)
    # Root can lose the worker reply after this owner transaction completes.
    # A bounded durable receipt disambiguates that case, but never attests the
    # current runtime. A pending journal always takes precedence over it.
    completed = {"schemaVersion": 1, "kind": "settings", "configPath": journal["configPath"],
                 "transactionId": journal["transactionId"], "settingsRevision": journal["next"]["settingsRevision"],
                 "outcome": outcome, "configSha256": journal["afterSha" if outcome == "applied" else "beforeSha"]}
    _write(sd, COMPLETED, completed)
    # Keep the exact private before-image until a subsequent transaction has
    # safely captured its own baseline. Recovery never infers bytes from JSON.
    try:
        _remove(sd, JOURNAL)
    except OSError:
        # A failed unlink directory-fsync is indeterminate. Re-establish the
        # recovery journal before surfacing failure; never return success.
        # Persistent storage failure still requires the caller's root hold.
        _write(sd, JOURNAL, journal)
        raise


def _check_mode(path, expected):
    if controller._check_config_security(path)[1] != expected:
        raise SettingsError("settings-config-changed")


def _check_result(path, expected_hash, mode, code):
    _abs, actual_mode, data, _config = controller._load_config(path)
    if actual_mode != mode or controller._sha256_bytes(data) != expected_hash:
        raise SettingsError(code)


def _rollback(path, sd, journal, validate, idle, activate):
    backup = _read_private(os.path.join(sd, BACKUP))
    if controller._sha256_bytes(backup) != journal["beforeSha"]:
        raise SettingsError("settings-backup-mismatch")
    _abs, mode, current, _config = controller._load_config(path)
    current_hash = controller._sha256_bytes(current)
    if current_hash not in (journal["beforeSha"], journal["afterSha"]) or mode != journal["configMode"]:
        raise SettingsError("settings-rollback-conflict")
    if current_hash != journal["beforeSha"]:
        staged = controller._stage_and_validate(path, backup, mode, validate, "settings rollback")
        try:
            controller._require_idle(idle, staged)
            _check_mode(path, mode)
            controller._checked_replace(path, backup, journal["afterSha"], staged, durable=True)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
    controller._require_idle(idle)
    if not _activate(activate):
        raise SettingsError("settings-rollback-unverified")
    controller._require_idle(idle)
    _check_result(path, journal["beforeSha"], journal["configMode"], "settings-rollback-conflict")
    _finish(sd, journal["previous"], journal, "rolled-back")
    return {"status": "rolled-back", "configSha256": journal["beforeSha"]}


def apply_settings(config_path, preferences, capabilities, *, state_dir, settings_revision, transaction_id,
                   expected_config_sha256, validate_config, check_no_active_run, activate):
    """Persist and verify a plan using the existing owner transaction lock.

    activate() is a trusted host-only hook returning verified/rejected/unavailable,
    never an HTTP-supplied assertion. Root must preserve its own journal/holds.
    The caller must qualify capabilities, settings revision and source custody.
    """
    _requirements(expected_config_sha256, validate_config, check_no_active_run, activate)
    if not _hash(transaction_id):
        raise SettingsError("invalid-settings-transaction-id")
    if type(settings_revision) is not int or not 0 <= settings_revision <= 2**53 - 1:
        raise SettingsError("invalid-settings-revision")
    sd = controller._prepare_state_dir(state_dir)
    validate = controller._normalize_validate(validate_config)
    with controller._Lock(sd):
        if os.path.lexists(os.path.join(sd, JOURNAL)):
            raise SettingsError("settings-recovery-required")
        _no_pending_access(sd)
        path, mode, before, config = controller._load_config(config_path)
        if len(before) > MAX_BYTES or controller._sha256_bytes(before) != expected_config_sha256:
            raise SettingsError("settings-config-changed")
        # Reject duplicate/invalid JSON rather than silently dropping owner data.
        config = _decode(before)
        completed = _completion(_record(sd, COMPLETED), path)
        if completed is not None and completed["transactionId"] == transaction_id:
            raise SettingsError("settings-transaction-already-completed")
        previous = _managed(_record(sd, MANAGED), path)
        plan = plan_preferences(config, preferences, capabilities,
                                previous=previous["state"] if previous else None)
        after = _encoded(plan["document"])
        next_record = {"schemaVersion": 1, "kind": "settings", "configPath": path,
                       "settingsRevision": settings_revision, "state": plan["state"]}
        journal = {"schemaVersion": 1, "kind": "settings", "configPath": path, "configMode": mode, "transactionId": transaction_id,
                   "beforeSha": expected_config_sha256, "afterSha": controller._sha256_bytes(after),
                   "previous": previous, "next": next_record}
        _encoded(journal)  # Check bounds before writing any recovery material.
        controller._require_idle(check_no_active_run)
        staged = controller._stage_and_validate(path, after, mode, validate, "settings apply")
        try:
            controller._require_idle(check_no_active_run, staged)
            _check_mode(path, mode)
            controller._atomic_write(os.path.join(sd, BACKUP), before, 0o600, durable=True)
            _write(sd, JOURNAL, journal)
            controller._require_idle(check_no_active_run, staged)
            _check_mode(path, mode)
            controller._checked_replace(path, after, expected_config_sha256, staged, durable=True)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
        if not _activate(activate):
            return _rollback(path, sd, journal, validate, check_no_active_run, activate)
        controller._require_idle(check_no_active_run)
        _check_result(path, journal["afterSha"], mode, "settings-config-changed")
        _finish(sd, next_record, journal, "applied")
        return {"status": "runtime-verified", "settingsRevision": settings_revision,
                "configSha256": journal["afterSha"]}


def recover_settings(config_path, *, state_dir, transaction_id, expected_config_sha256, validate_config,
                     check_no_active_run, activate):
    """Explicitly roll back an interrupted transaction; never overwrite drift."""
    _requirements(expected_config_sha256, validate_config, check_no_active_run, activate)
    if not _hash(transaction_id):
        raise SettingsError("invalid-settings-transaction-id")
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd):
        _no_pending_access(sd)
        path, mode, data, _config = controller._load_config(config_path)
        if controller._sha256_bytes(data) != expected_config_sha256:
            raise SettingsError("settings-config-changed")
        journal = _record(sd, JOURNAL)
        _header(journal, {"schemaVersion", "kind", "configPath", "configMode", "transactionId", "beforeSha", "afterSha", "previous", "next"}, path)
        if (not _hash(journal["beforeSha"]) or not _hash(journal["afterSha"])
                or type(journal["configMode"]) is not int or journal["configMode"] != mode
                or journal["transactionId"] != transaction_id
                or journal["next"] is None):
            raise SettingsError("invalid-settings-state")
        _managed(journal["previous"], path)
        _managed(journal["next"], path)
        return _rollback(path, sd, journal, controller._normalize_validate(validate_config),
                         check_no_active_run, activate)


def settings_status(config_path, *, state_dir):
    """Private owner-pipe status; completion is NOT a live runtime claim."""
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd, exclusive=False):
        path, _mode, data, _config = controller._load_config(config_path)
        managed = _managed(_record(sd, MANAGED), path)
        completed = _completion(_record(sd, COMPLETED), path)
        # Do not interpret an interrupted journal as a completed transaction,
        # even if writing its completion receipt preceded the crash.
        pending = os.path.lexists(os.path.join(sd, JOURNAL))
        return {"configSha256": controller._sha256_bytes(data),
                "managedRevision": managed["settingsRevision"] if managed else None,
                "pending": pending,
                "completion": ({key: completed[key] for key in ("transactionId", "settingsRevision", "outcome", "configSha256")}
                               if completed is not None and not pending else None),
                "runtimeVerified": False}
