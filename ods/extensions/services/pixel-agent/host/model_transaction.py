"""Owner writes under the existing apply lock; no root or restart authority."""
import os
import pixel_access_mode as controller
import settings_transaction as storage
from pixel_model_contract import ModelError, checksum, plan, projection, target

JOURNAL = "model-journal.json"
BACKUP = "model-before.json"
COMPLETED = "model-completed.json"


def _guards(sd):
    for name in (storage.JOURNAL, "provider-journal.json"):
        if os.path.lexists(os.path.join(sd, name)): raise ModelError("other-transition-pending")
    receipt = controller._load_receipt(sd)
    if receipt and receipt.get("status") == controller.STATUS_PENDING: raise ModelError("other-transition-pending")


def _journal(sd, path):
    value = storage._record(sd, JOURNAL)
    if value is None: return None
    if (type(value) is not dict or set(value) != {"transactionId", "configPath", "configMode", "beforeSha", "afterSha", "target"}
            or value["configPath"] != path or type(value["configMode"]) is not int
            or any(not checksum(value[key]) for key in ("transactionId", "beforeSha"))
            or value["afterSha"] is not None and not checksum(value["afterSha"])
            or (value["target"] is None) != (value["afterSha"] is None)):
        raise ModelError("invalid-model-journal")
    if value["target"] is not None: target(value["target"])
    before = storage._read_private(os.path.join(sd, BACKUP))
    if controller._sha256_bytes(before) != value["beforeSha"]: raise ModelError("model-backup-mismatch")
    if value["target"] is not None and controller._sha256_bytes(storage._encoded(plan(storage._decode(before), value["target"]))) != value["afterSha"]:
        raise ModelError("model-journal-mismatch")
    return value


def operate(config_path, *, state_dir, operation, transaction_id=None, proposed=None,
            expected_config_sha256=None, outcome=None, validate_config=None, check_no_active_run=None):
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd, exclusive=operation != "model-status"):
        path, mode, raw, config = controller._load_config(config_path)
        raw_hash = controller._sha256_bytes(raw)
        journal = _journal(sd, path)
        if operation == "model-status":
            completed = storage._record(sd, COMPLETED)
            return {"configSha256": raw_hash, **projection(config), "pending": journal is not None,
                    "transactionId": journal["transactionId"] if journal else None,
                    "completion": completed}
        _guards(sd)
        if not checksum(transaction_id) or raw_hash != expected_config_sha256:
            raise ModelError("model-config-changed")
        controller._require_idle(check_no_active_run)
        if operation == "model-begin":
            if journal:
                if journal["transactionId"] != transaction_id: raise ModelError("model-transaction-conflict")
            else:
                completed = storage._record(sd, COMPLETED)
                if completed and completed.get("transactionId") == transaction_id: raise ModelError("model-transaction-completed")
                projection(config)
                controller._atomic_write(os.path.join(sd, BACKUP), raw, 0o600, durable=True)
                journal = dict(transactionId=transaction_id, configPath=path, configMode=mode,
                               beforeSha=raw_hash, afterSha=None, target=None)
                storage._write(sd, JOURNAL, journal)
        elif not journal or journal["transactionId"] != transaction_id or journal["configMode"] != mode:
            raise ModelError("model-transaction-conflict")
        elif operation in ("model-apply", "model-rollback"):
            if raw_hash not in (journal["beforeSha"], journal["afterSha"]): raise ModelError("model-config-changed")
            before = storage._read_private(os.path.join(sd, BACKUP))
            if operation == "model-apply":
                proposed = target(proposed)
                if journal["target"] is not None and journal["target"] != proposed: raise ModelError("model-target-changed")
                after = storage._encoded(plan(storage._decode(before), proposed))
                desired_hash = controller._sha256_bytes(after)
                journal.update(target=proposed, afterSha=desired_hash)
            else:
                after, desired_hash = before, journal["beforeSha"]
            staged = controller._stage_and_validate(path, after, mode, controller._normalize_validate(validate_config), "model contract")
            try:
                controller._require_idle(check_no_active_run, staged)
                storage._write(sd, JOURNAL, journal)
                if raw_hash != desired_hash:
                    controller._checked_replace(path, after, raw_hash, staged, durable=True)
            finally:
                if os.path.exists(staged): os.unlink(staged)
            raw_hash = desired_hash
        elif operation == "model-finish":
            expected = journal["afterSha"] if outcome == "commit" else journal["beforeSha"] if outcome == "rollback" else None
            if raw_hash != expected: raise ModelError("model-completion-mismatch")
            storage._write(sd, COMPLETED, {"transactionId": transaction_id, "outcome": outcome, "configSha256": raw_hash})
            try: storage._remove(sd, JOURNAL)
            except OSError:
                storage._write(sd, JOURNAL, journal)
                raise
        else: raise ModelError("invalid-model-operation")
        return {"configSha256": raw_hash}
