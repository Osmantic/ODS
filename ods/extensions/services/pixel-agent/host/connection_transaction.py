"""Owner-side connection transaction, called only under the root coordinator's hold.

Writes the Source Broker's gateway entries (openclaw.json), its limb flags
(gateway.env) and the connection keys of ODS's onboarding contract with the
access controller's SAME apply lock, staging, validator and replacement
primitives, then has OpenClaw prove the plugin resolves from the active release.
Root restarts the gateway and re-proves access through the connection-activate
hook; nothing here restarts anything.
"""
import hashlib
import json
import os
import stat
import subprocess
import tempfile

import pixel_access_mode as controller
from pixel_connections import planner, plugins as portal_plugins, state

JOURNAL = "connection-journal.json"
BACKUP = "connection-before.json"
COMPLETED = "connection-completed.json"
MAX_BYTES = 2 * 1024 * 1024
SIDE_FILES = (("gateway", "connection-gateway-env-before"), ("answers", "connection-answers-before"))
JOURNAL_KEYS = {"schemaVersion", "kind", "configPath", "configMode", "transactionId", "beforeSha", "afterSha",
                "files", "target"}


class ConnectionTransactionError(ValueError):
    pass


def _target(value):
    try:
        return planner.target(value)
    except planner.PlanError:
        raise ConnectionTransactionError("invalid-connection-target") from None


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _checksum(value):
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _read_private(path, maximum=MAX_BYTES):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise ConnectionTransactionError("unsafe-connection-state") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > maximum):
            raise ConnectionTransactionError("unsafe-connection-state")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(maximum + 1)
        if len(raw) > maximum:
            raise ConnectionTransactionError("unsafe-connection-state")
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
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise ConnectionTransactionError("invalid-connection-state") from None


def _encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _record(sd, name):
    path = os.path.join(sd, name)
    return _decode(_read_private(path)) if os.path.lexists(path) else None


def _write(sd, name, value):
    controller._atomic_write(os.path.join(sd, name), _encoded(value), 0o600, durable=True)


def _remove(sd, name):
    path = os.path.join(sd, name)
    if os.path.lexists(path):
        _read_private(path)
        os.unlink(path)
        _fsync_directory(sd)


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _journal(sd, path):
    value = _record(sd, JOURNAL)
    if value is None:
        return None
    files = value.get("files") if isinstance(value, dict) else None
    if (type(value) is not dict or set(value) != JOURNAL_KEYS or value["schemaVersion"] != 1
            or value["kind"] != "connection" or value["configPath"] != path
            or type(value["configMode"]) is not int
            or not all(_checksum(value[key]) for key in ("transactionId", "beforeSha", "afterSha"))
            or type(files) is not dict or set(files) != {name for name, _ in SIDE_FILES}
            or any(type(item) is not dict or set(item) != {"path", "beforeSha", "afterSha"}
                   or type(item["path"]) is not str or not os.path.isabs(item["path"])
                   or not _checksum(item["beforeSha"]) or not _checksum(item["afterSha"])
                   for item in files.values())):
        raise ConnectionTransactionError("invalid-connection-state")
    _target(value["target"])
    return value


def _no_pending_other(sd):
    for name, code in (("access-release-journal.json", "access-release-recovery-required"),
                       ("model-journal.json", "model-recovery-required"),
                       ("settings-journal.json", "settings-recovery-required"),
                       ("provider-journal.json", "provider-recovery-required")):
        if os.path.lexists(os.path.join(sd, name)):
            raise ConnectionTransactionError(code)
    access = controller._load_receipt(sd)
    if access is not None and access.get("status") == controller.STATUS_PENDING:
        raise ConnectionTransactionError("access-recovery-required")


def _replace(path, data, expected_hash):
    """Replace one owner file atomically, only if it still has the bytes the plan read."""
    if _hash(_read_private(path)) != expected_hash:
        raise ConnectionTransactionError("connection-file-changed")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(os.path.dirname(path))
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _answers(raw, connection_answers):
    """The onboarding contract with exactly its connection keys replaced; None removes one."""
    answers = _decode(raw)
    if type(answers) is not dict:
        raise ConnectionTransactionError("invalid-onboarding-contract")
    for key, value in connection_answers.items():
        if value is None:
            answers.pop(key, None)
        else:
            answers[key] = value
    return _encoded(answers)


def _plugin_ready(openclaw, install_dir, *, enabled):
    """OpenClaw refreshes its plugin registry and, when enabled, resolves the plugin from the release."""
    try:
        refreshed = subprocess.run([openclaw, "plugins", "registry", "--refresh", "--json"], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        if refreshed.returncode != 0:
            return False
        if not enabled:
            return True
        inspected = subprocess.run([openclaw, "plugins", "inspect", planner.PLUGIN_ID, "--json"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=120)
        if inspected.returncode != 0 or len(inspected.stdout) > 1024 * 1024:
            return False
        plugin = json.loads(inspected.stdout).get("plugin")
        expected = os.path.realpath(os.path.join(install_dir, "current", "plugin"))
        return (isinstance(plugin, dict) and plugin.get("status") == "loaded"
                and plugin.get("rootDir") == expected)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def _catalog_ready(openclaw, chosen):
    """Every reviewed plugin the owner turned on resolves from the release as loaded."""
    try:
        for plugin_id in chosen:
            inspected = subprocess.run([openclaw, "plugins", "inspect", plugin_id, "--json"],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       timeout=120)
            if inspected.returncode != 0 or len(inspected.stdout) > 1024 * 1024:
                return False
            value = json.loads(inspected.stdout)
            plugin = value.get("plugin") if isinstance(value, dict) else None
            if not isinstance(plugin, dict) or plugin.get("status") != "loaded":
                return False
        return True
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def _activate(activate):
    # Unknown transport/process state is NOT a proven mismatch. Leave the
    # durable journal and host gates held; only explicit recovery may retry it.
    result = activate()
    if result not in ("verified", "rejected", "unavailable") or type(result) is not str:
        raise ConnectionTransactionError("connection-activation-contract")
    if result == "unavailable":
        raise ConnectionTransactionError("connection-activation-unavailable")
    return result == "verified"


def _finish(sd, journal, outcome):
    completed = {"schemaVersion": 1, "kind": "connection", "configPath": journal["configPath"],
                 "transactionId": journal["transactionId"], "outcome": outcome,
                 "configSha256": journal["afterSha" if outcome == "applied" else "beforeSha"]}
    _write(sd, COMPLETED, completed)
    try:
        _remove(sd, JOURNAL)
    except OSError:
        # An indeterminate unlink must keep recovery possible; never claim success.
        _write(sd, JOURNAL, journal)
        raise


def _check_result(path, journal, outcome):
    side = "afterSha" if outcome == "applied" else "beforeSha"
    _abs, mode, data, _config = controller._load_config(path)
    if (mode != journal["configMode"] or _hash(data) != journal[side]
            or any(_hash(_read_private(item["path"])) != item[side] for item in journal["files"].values())):
        raise ConnectionTransactionError("connection-config-changed")


def _rollback(path, sd, journal, validate, idle, activate, openclaw, install_dir):
    backup = _read_private(os.path.join(sd, BACKUP))
    side_backups = {name: _read_private(os.path.join(sd, backup_name)) for name, backup_name in SIDE_FILES}
    if _hash(backup) != journal["beforeSha"] or any(
            _hash(side_backups[name]) != item["beforeSha"] for name, item in journal["files"].items()):
        raise ConnectionTransactionError("connection-backup-mismatch")
    _abs, mode, current, _config = controller._load_config(path)
    current_hash = _hash(current)
    if current_hash not in (journal["beforeSha"], journal["afterSha"]) or mode != journal["configMode"]:
        raise ConnectionTransactionError("connection-rollback-conflict")
    if current_hash != journal["beforeSha"]:
        staged = controller._stage_and_validate(path, backup, mode, validate, "connection rollback")
        try:
            controller._require_idle(idle, staged)
            controller._checked_replace(path, backup, journal["afterSha"], staged, durable=True)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
    for name, item in journal["files"].items():
        side_hash = _hash(_read_private(item["path"]))
        if side_hash not in (item["beforeSha"], item["afterSha"]):
            raise ConnectionTransactionError("connection-rollback-conflict")
        if side_hash != item["beforeSha"]:
            _replace(item["path"], side_backups[name], side_hash)
    if not _plugin_ready(openclaw, install_dir, enabled=False):
        raise ConnectionTransactionError("connection-rollback-unverified")
    controller._require_idle(idle)
    if not _activate(activate):
        raise ConnectionTransactionError("connection-rollback-unverified")
    controller._require_idle(idle)
    _check_result(path, journal, "rolled-back")
    _finish(sd, journal, "rolled-back")
    return {"status": "rolled-back", "configSha256": journal["beforeSha"]}


def _requirements(expected_hash, transaction_id, validate_config, check_no_active_run, activate):
    if (not _checksum(expected_hash) or not _checksum(transaction_id)
            or not all(callable(hook) for hook in (validate_config, check_no_active_run, activate))):
        raise ConnectionTransactionError("connection-transaction-contract")


def apply_connection(config_path, gateway_target, connection_answers, *, state_dir, env_path, answers_path,
                     install_dir, openclaw, transaction_id, expected_config_sha256, validate_config,
                     check_no_active_run, activate):
    """Write the planned gateway change, prove the plugin, and let root restart and verify it.

    activate() is a trusted host-only hook returning verified/rejected/unavailable.
    A rejected activation restores every file and has root restart the old setup.
    """
    _requirements(expected_config_sha256, transaction_id, validate_config, check_no_active_run, activate)
    gateway_target = _target(gateway_target)
    if not state.valid_connection_answers(connection_answers):
        raise ConnectionTransactionError("invalid-connection-answers")
    sd = controller._prepare_state_dir(state_dir)
    validate = controller._normalize_validate(validate_config)
    with controller._Lock(sd):
        if os.path.lexists(os.path.join(sd, JOURNAL)):
            raise ConnectionTransactionError("connection-recovery-required")
        _no_pending_other(sd)
        path, mode, before, _config = controller._load_config(config_path)
        if len(before) > MAX_BYTES or _hash(before) != expected_config_sha256:
            raise ConnectionTransactionError("connection-config-changed")
        completed = _record(sd, COMPLETED)
        if isinstance(completed, dict) and completed.get("transactionId") == transaction_id:
            raise ConnectionTransactionError("connection-transaction-already-completed")
        side_before = {"gateway": _read_private(env_path), "answers": _read_private(answers_path)}
        try:
            planned = planner.plan_config(_decode(before), gateway_target, install_dir=install_dir)
            side_after = {"gateway": planner.plan_gateway_env(side_before["gateway"].decode("utf-8"),
                                                              gateway_target).encode("utf-8"),
                          "answers": _answers(side_before["answers"], connection_answers)}
        except (planner.PlanError, UnicodeError):
            raise ConnectionTransactionError("connection-config-unrecognized") from None
        after = _encoded(planned)
        paths = {"gateway": env_path, "answers": answers_path}
        journal = {"schemaVersion": 1, "kind": "connection", "configPath": path, "configMode": mode,
                   "transactionId": transaction_id, "beforeSha": expected_config_sha256, "afterSha": _hash(after),
                   "files": {name: {"path": paths[name], "beforeSha": _hash(side_before[name]),
                                    "afterSha": _hash(side_after[name])} for name, _ in SIDE_FILES},
                   "target": gateway_target}
        controller._require_idle(check_no_active_run)
        staged = controller._stage_and_validate(path, after, mode, validate, "connection apply")
        try:
            controller._require_idle(check_no_active_run, staged)
            controller._atomic_write(os.path.join(sd, BACKUP), before, 0o600, durable=True)
            for name, backup_name in SIDE_FILES:
                controller._atomic_write(os.path.join(sd, backup_name), side_before[name], 0o600, durable=True)
            _write(sd, JOURNAL, journal)
            controller._require_idle(check_no_active_run, staged)
            controller._checked_replace(path, after, expected_config_sha256, staged, durable=True)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
        for name, _backup in SIDE_FILES:
            _replace(paths[name], side_after[name], journal["files"][name]["beforeSha"])
        enabled = bool(planner.active_tools(planned, gateway_target))
        if (not _plugin_ready(openclaw, install_dir, enabled=enabled)
                or not _catalog_ready(openclaw, gateway_target["plugins"]) or not _activate(activate)):
            return _rollback(path, sd, journal, validate, check_no_active_run, activate, openclaw, install_dir)
        controller._require_idle(check_no_active_run)
        _check_result(path, journal, "applied")
        _finish(sd, journal, "applied")
        return {"status": "runtime-verified", "configSha256": journal["afterSha"]}


def recover_connection(config_path, *, state_dir, install_dir, openclaw, transaction_id, expected_config_sha256,
                       validate_config, check_no_active_run, activate):
    """Roll an interrupted transaction back to its exact before-image; never overwrite drift."""
    _requirements(expected_config_sha256, transaction_id, validate_config, check_no_active_run, activate)
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd):
        _no_pending_other(sd)
        path, _mode, data, _config = controller._load_config(config_path)
        if _hash(data) != expected_config_sha256:
            raise ConnectionTransactionError("connection-config-changed")
        journal = _journal(sd, path)
        if journal is None or journal["transactionId"] != transaction_id:
            raise ConnectionTransactionError("connection-recovery-unavailable")
        return _rollback(path, sd, journal, controller._normalize_validate(validate_config),
                         check_no_active_run, activate, openclaw, install_dir)


def connection_status(config_path, *, state_dir, env_path, answers_path):
    """Private owner-pipe status: what the live files say, never a live runtime claim."""
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd, exclusive=False):
        path, _mode, data, _config = controller._load_config(config_path)
        env = _read_private(env_path)
        contract = _decode(_read_private(answers_path))
        if type(contract) is not dict:
            raise ConnectionTransactionError("invalid-onboarding-contract")
        answers = {key: contract.get(key) for key in state.ANSWER_KEYS}
        pending = _journal(sd, path) is not None
        completed = _record(sd, COMPLETED)
        try:
            config = _decode(data)
            live = dict(planner.gateway_limbs(env.decode("utf-8")),
                        userTimezone=config.get("agents", {}).get("defaults", {}).get("userTimezone"),
                        plugins=portal_plugins.enabled_in(config))
            planner.target(live)
        except (planner.PlanError, UnicodeError, AttributeError):
            raise ConnectionTransactionError("connection-config-unrecognized") from None
        return {"configSha256": _hash(data), "envSha256": _hash(env), "pending": pending, "live": live,
                "answers": answers,
                "completion": ({key: completed[key] for key in ("transactionId", "outcome", "configSha256")}
                               if isinstance(completed, dict) and not pending else None)}
