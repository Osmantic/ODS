"""Settings operations inside the EXISTING privileged access coordinator.

All lifecycle authority is the supplied SystemdAccessBridge. No independent
service, config writer, lock name, unit or executable selector exists here.
"""
import contextlib
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import time

from pixel_access_bridge import AccessError, UNIT, atomic_json, digest, remaining
from pixel_access_protocol import decode_frame, HEX
from .contract import SettingsError, preview_preferences
from .runtime import compare_readback, declared_capabilities, saved_document


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _private(path, uid, *, directory=False):
    info = path.lstat()
    _private_info(info, uid, directory=directory)
    return info


def _private_info(info, uid, *, directory=False):
    if (stat.S_ISLNK(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
        raise AccessError("unsafe-settings-source")


def _read(path, uid, maximum=1024 * 1024):
    before = _private(path, uid)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        _private_info(opened, uid)
        if (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev) or opened.st_size > maximum:
            raise AccessError("unsafe-settings-source")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(maximum + 1)
        if len(raw) > maximum: raise AccessError("unsafe-settings-source")
        after = os.fstat(fd)
        _private_info(after, uid)
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise AccessError("settings-source-changed")
        return decode_frame(raw.decode("utf-8") + "\n", maximum + 1), _hash(raw)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _store(bridge, *, exclusive):
    if bridge.settings_data_dir is None:
        raise AccessError("settings-data-directory-unqualified")
    directory = Path(bridge.settings_data_dir) / "pixel-providers"
    if not directory.is_absolute() or directory.resolve() != directory:
        raise AccessError("unsafe-settings-source")
    try:
        _private(directory, bridge.owner.pw_uid, directory=True)
    except FileNotFoundError:
        # Inspect must not create owner storage as root on a fresh installation.
        # The normal owner-facing Save operation initializes this private store.
        raise AccessError("settings-store-not-initialized") from None
    lock = directory / ".provider-config.lock"
    info = _private(lock, bridge.owner.pw_uid)
    fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)  # Never create as root.
    try:
        actual = os.fstat(fd)
        _private_info(actual, bridge.owner.pw_uid)
        if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
            raise AccessError("settings-store-changed")
        try: fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError: raise AccessError("settings-store-busy") from None
        current = _private(lock, bridge.owner.pw_uid)
        if (current.st_dev, current.st_ino) != (actual.st_dev, actual.st_ino):
            raise AccessError("settings-store-changed")
        yield directory
    finally:
        os.close(fd)


def _inputs(bridge, directory):
    bridge.settings_source()
    saved, saved_hash = _read(directory / "pixel-settings.json", bridge.owner.pw_uid, 256 * 1024)
    saved = saved_document(saved)
    config, config_hash = _read(bridge.home / ".openclaw/openclaw.json", bridge.owner.pw_uid)
    provider_file = directory / "provider-config.json"
    providers, provider_hash = _read(provider_file, bridge.owner.pw_uid, 256 * 1024) if provider_file.exists() else (None, None)
    caps = declared_capabilities(config, providers)
    return saved, config, config_hash, caps, digest([saved_hash, provider_hash])


def _identity(bridge):
    raw = bridge.command(["systemctl", "show", UNIT, "--property=MainPID,ActiveState,ExecMainStartTimestampMonotonic"])
    fields = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    try: pid, started = int(fields["MainPID"]), int(fields["ExecMainStartTimestampMonotonic"])
    except (KeyError, ValueError): raise AccessError("settings-process-unavailable") from None
    if fields.get("ActiveState") != "active" or pid <= 0 or started <= 0:
        raise AccessError("settings-process-not-active")
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", boot):
        raise AccessError("settings-process-unavailable")
    return {"pid": pid, "started": started, "boot": boot}


def _valid_identity(value):
    return (type(value) is dict and set(value) == {"pid", "started", "boot"}
            and all(type(value[key]) is int and value[key] > 0 for key in ("pid", "started"))
            and type(value["boot"]) is str
            and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value["boot"]) is not None)


def _revision(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def _journal(value):
    required = {"kind", "phase", "token", "transactionId", "edge_revision", "beforeSha", "settingsRevision",
                "sourceHash", "boundary", "mode", "beforeIdentity", "previousManagedRevision"}
    optional = {"error", "outcome", "appliedRevision", "restartIdentity", "noOwnerWrite"}
    if (type(value) is not dict or not required <= set(value) or set(value) - required - optional
            or value["kind"] != "settings" or value["phase"] not in ("acquiring", "invoking", "restarting", "releasing")
            or any(type(value[key]) is not str or not HEX.fullmatch(value[key])
                   for key in ("token", "transactionId", "edge_revision", "beforeSha", "sourceHash"))
            or not _revision(value["settingsRevision"]) or not _valid_identity(value["beforeIdentity"])
            or value["previousManagedRevision"] is not None and not _revision(value["previousManagedRevision"])
            or value["mode"] not in ("sandboxed", "full-access")
            or type(value["boundary"]) is not str or not 1 <= len(value["boundary"]) <= 4096
            or "restartIdentity" in value and not _valid_identity(value["restartIdentity"])
            or "noOwnerWrite" in value and value["noOwnerWrite"] is not True
            or "error" in value and (type(value["error"]) is not str or not re.fullmatch(r"[a-z0-9-]{1,96}", value["error"]))):
        raise AccessError("invalid-settings-transition")
    if value["phase"] == "releasing":
        if (value.get("outcome") not in ("applied", "rolled-back") or "appliedRevision" not in value
                or value["appliedRevision"] is not None and not _revision(value["appliedRevision"])):
            raise AccessError("invalid-settings-transition")
    elif "outcome" in value or "appliedRevision" in value:
        raise AccessError("invalid-settings-transition")
    return value


def _inspection_revision(snapshot, source_hash, journal):
    # Bind recovery to the exact transaction and its phase, not merely the saved
    # revision. Recovery remains possible if saved preferences become unreadable.
    return digest([snapshot["revision"], journal if journal else source_hash])


def _write(bridge, journal):
    _journal(journal)
    atomic_json(bridge.state / "transition.json", journal)


def _acquire(bridge, journal):
    edge = bridge.edge()
    revision = edge["revision"] if edge.get("phase") == "idle" else journal["edge_revision"]
    edge = bridge.edge("recover" if edge.get("phase") == "interrupted" else "acquire", journal["token"], revision)
    journal["edge_revision"] = edge["revision"]
    _write(bridge, journal)
    bridge.native("acquire", journal["token"])


def _busy(bridge, journal):
    native = bridge.native("acquire", journal["token"])
    edge = bridge.edge("acquire", journal["token"], journal["edge_revision"])
    return (native.get("phase") != "held" or edge.get("phase") != "held"
            or bool(native.get("active") or edge.get("streams")))


def _verify(bridge, journal):
    bridge.settings_source()
    if _busy(bridge, journal): raise AccessError("runtime-busy")
    identity = _identity(bridge)
    current, config_hash = _read(bridge.home / ".openclaw/openclaw.json", bridge.owner.pw_uid)
    native = bridge.native()
    if native.get("pid") != identity["pid"]: raise AccessError("settings-process-changed")
    envelope = bridge.http(bridge.native_origin, "/pixel-ods/access-runtime", bridge.native_key,
        {"operation": "settings-readback", "token": journal["token"], "revision": native["revision"]})
    if not compare_readback(current, envelope, pid=identity["pid"], revision=native["revision"]):
        raise SettingsError("settings-runtime-mismatch")
    if bridge.unit_boundary() != journal["boundary"]: raise AccessError("settings-access-boundary-changed")
    bridge.provision_probe()
    proof = bridge.native("probe", journal["token"])
    if (proof.get("pid") != identity["pid"] or proof.get("proof", {}).get("mode") != journal["mode"]
            or proof.get("proof", {}).get("executed") is not True or _identity(bridge) != identity
            or _read(bridge.home / ".openclaw/openclaw.json", bridge.owner.pw_uid)[1] != config_hash
            or bridge.unit_boundary() != journal["boundary"] or _busy(bridge, journal)):
        raise AccessError("settings-runtime-proof-failed")
    atomic_json(bridge.state / "verified.json", {"pid": proof["pid"], "proof": proof["proof"],
        "config_sha256": config_hash, "boundary": journal["boundary"]})
    bridge.settings_source()
    return dict(identity, configSha256=config_hash, observedAt=envelope["observedAt"])


def _activate(bridge, journal):
    if _busy(bridge, journal): return "unavailable"
    try:
        before = _identity(bridge)
    except AccessError:
        # Only a crash of our already-journaled restart can be resumed. A dead
        # endpoint or an unrelated stopped service is not permission to start it.
        if journal["phase"] != "restarting" or "restartIdentity" not in journal:
            return "unavailable"
        if bridge.stopped_native(journal["token"]).get("stopped") is not True:
            return "unavailable"
        before = journal["restartIdentity"]
    if bridge.unit_boundary() != journal["boundary"]: return "unavailable"
    journal["phase"] = "restarting"
    journal["restartIdentity"] = before
    _write(bridge, journal)
    # Do not call dropin_for or daemon-reload: this operation changes no access.
    bridge.command(["systemctl", "restart", UNIT], timeout=60)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            now = _identity(bridge)
            if now["boot"] == before["boot"] and now["pid"] != before["pid"] and now["started"] > before["started"]:
                bridge.native("acquire", journal["token"], timeout=3)
                if bridge.http(bridge.native_origin, "/health", bridge.native_key, timeout=3).get("ok") is True:
                    _verify(bridge, journal)
                    return "verified"
        except SettingsError as error:
            return "rejected" if str(error) == "settings-runtime-mismatch" else "unavailable"
        except AccessError as error:
            if error.code == "operation-deadline-exceeded": raise
            pass  # Observe the same restarted unit until this fixed deadline.
        time.sleep(remaining(1))
    return "unavailable"


def _finish(bridge, journal, outcome, revision):
    proof = _verify(bridge, journal)
    journal["phase"] = "releasing"
    journal["outcome"] = outcome
    journal["appliedRevision"] = revision
    _write(bridge, journal)
    atomic_json(bridge.state / "settings-verified.json", dict(proof, settingsRevision=revision,
        transactionId=journal["transactionId"], outcome=outcome, boundary=journal["boundary"]))
    bridge.edge("release", journal["token"], journal["edge_revision"])
    bridge.native("release", journal["token"])
    (bridge.state / "transition.json").unlink()
    fd = os.open(bridge.state, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    except OSError:
        _write(bridge, journal)
        raise
    finally: os.close(fd)
    return {"outcome": outcome, "appliedRevision": revision}


def change(bridge, request):
    if (type(request) is not dict or set(request) != {"operation", "revision", "settingsRevision"}
            or request["operation"] not in ("apply", "recover") or type(request["revision"]) is not str
            or not HEX.fullmatch(request["revision"]) or not _revision(request["settingsRevision"])):
        raise AccessError("invalid-settings-request")
    with bridge.locked():
        snapshot = bridge.inspect()
        with _store(bridge, exclusive=True) as directory:
            journal = bridge.pending()
            if request["operation"] == "apply":
                saved, _config, config_hash, caps, source_hash = _inputs(bridge, directory)
                if (request["revision"] != _inspection_revision(snapshot, source_hash, journal)
                        or request["settingsRevision"] != saved["revision"]):
                    raise AccessError("settings-inspection-changed")
                if journal: raise AccessError("transition-recovery-required")
                if snapshot["busy"]: raise AccessError("runtime-busy")
                if snapshot["configured_mode"] not in ("sandboxed", "full-access"):
                    raise AccessError("settings-access-mode-unknown")
                preview_preferences(saved["preferences"], caps)
                identity = _identity(bridge)  # Never start a previously stopped service.
                owner = bridge.worker("settings-status")
                if owner["pending"] or owner["configSha256"] != config_hash:
                    raise AccessError("settings-owner-state-changed")
                journal = {"kind": "settings", "phase": "acquiring", "token": os.urandom(32).hex(),
                    "transactionId": os.urandom(32).hex(), "edge_revision": snapshot["_edge"]["revision"],
                    "beforeSha": config_hash, "settingsRevision": saved["revision"], "sourceHash": source_hash,
                    "boundary": bridge.unit_boundary(), "mode": snapshot["configured_mode"], "beforeIdentity": identity,
                    "previousManagedRevision": owner["managedRevision"]}
                _write(bridge, journal)
            elif not journal or journal.get("kind") != "settings":
                raise AccessError("settings-recovery-unavailable")
            else:
                _journal(journal)
                if (request["revision"] != _inspection_revision(snapshot, None, journal)
                        or request["settingsRevision"] != journal["settingsRevision"]):
                    raise AccessError("settings-inspection-changed")
            try:
                _acquire(bridge, journal)
                if _busy(bridge, journal): raise AccessError("runtime-busy")
                if request["operation"] == "apply":
                    if _inputs(bridge, directory)[4] != journal["sourceHash"]:
                        raise AccessError("settings-source-changed")
                    journal["phase"] = "invoking"
                    _write(bridge, journal)
                    result = bridge.worker("settings-apply", config_hash=journal["beforeSha"], transaction_id=journal["transactionId"],
                        settings_revision=saved["revision"], preferences=saved["preferences"], capabilities=caps,
                        busy=lambda: _busy(bridge, journal), activate_settings=lambda: _activate(bridge, journal))
                else:
                    state = bridge.worker("settings-status")
                    if state["pending"]:
                        result = bridge.worker("settings-recover", config_hash=state["configSha256"], transaction_id=journal["transactionId"],
                            busy=lambda: _busy(bridge, journal), activate_settings=lambda: _activate(bridge, journal))
                    else:
                        completed = state["completion"]
                        if completed and completed["transactionId"] == journal["transactionId"]:
                            result = {"status": "runtime-verified" if completed["outcome"] == "applied" else "rolled-back",
                                      "configSha256": completed["configSha256"]}
                        elif (state["configSha256"] == journal["beforeSha"]
                              and (journal["phase"] in ("acquiring", "invoking") or journal.get("noOwnerWrite") is True)):
                            journal["noOwnerWrite"] = True
                            result = {"status": "rolled-back", "configSha256": journal["beforeSha"]}
                        else: raise AccessError("settings-recovery-conflict")
                state = bridge.worker("settings-status")
                outcome = _check_completion(bridge, journal, result, state)
                return _finish(bridge, journal, outcome, state["managedRevision"])
            except Exception as error:
                code = str(error) if isinstance(error, (AccessError, SettingsError)) else "settings-transition-failed"
                journal["error"] = code if re.fullmatch(r"[a-z0-9-]{1,96}", code) else "settings-transition-failed"
                _write(bridge, journal)  # Retain original phase and held authority.
                raise


def _check_completion(bridge, journal, result, state):
    outcome = {"runtime-verified": "applied", "rolled-back": "rolled-back"}.get(result.get("status"))
    current_hash = _read(bridge.home / ".openclaw/openclaw.json", bridge.owner.pw_uid)[1]
    revision = journal["settingsRevision"] if outcome == "applied" else journal["previousManagedRevision"]
    if (outcome is None or state["pending"] or state["configSha256"] != current_hash
            or result.get("configSha256") != current_hash or state["managedRevision"] != revision
            or outcome == "rolled-back" and current_hash != journal["beforeSha"]):
        raise AccessError("settings-completion-mismatch")
    if journal.get("noOwnerWrite") is True:
        if outcome != "rolled-back": raise AccessError("settings-completion-mismatch")
    elif state["completion"] != {"transactionId": journal["transactionId"], "settingsRevision": journal["settingsRevision"],
                                  "outcome": outcome, "configSha256": current_hash}:
        raise AccessError("settings-completion-mismatch")
    return outcome


def status(bridge):
    snapshot = bridge.inspect()
    journal = bridge.pending()
    if journal:
        _journal(journal)
        return {"status": "pending", "revision": _inspection_revision(snapshot, None, journal),
                "settingsRevision": journal["settingsRevision"], "appliedRevision": None, "capabilities": None,
                "pending": True, "lastVerifiedAt": None}
    with _store(bridge, exclusive=False) as directory:
        saved, _config, config_hash, caps, source_hash = _inputs(bridge, directory)
        owner = bridge.worker("settings-status")
    if owner["pending"]:
        raise AccessError("settings-recovery-journal-missing")
    proof_file = bridge.state / "settings-verified.json"
    proof = _read(proof_file, 0, 8192)[0] if proof_file.exists() else {}
    applied = (not owner["pending"] and snapshot["runtime_verified"] is True
               and proof.get("configSha256") == config_hash and proof.get("pid") == snapshot["_native"]["pid"]
               and (_revision(proof.get("settingsRevision"))
                    or proof.get("settingsRevision") is None and proof.get("outcome") == "rolled-back")
               and proof.get("settingsRevision") == owner["managedRevision"]
               and all(proof.get(key) == value for key, value in _identity(bridge).items())
               and proof.get("boundary") == bridge.unit_boundary())
    state = "pending" if owner["pending"] else "not-applied"
    if applied:
        state = ("restored" if owner["managedRevision"] is None else
                 "applied" if owner["managedRevision"] == saved["revision"] else "saved-changes")
    return {"status": state,
            "revision": _inspection_revision(snapshot, source_hash, None), "settingsRevision": saved["revision"],
            "appliedRevision": owner["managedRevision"] if applied else None, "capabilities": caps,
            "pending": bool(journal or owner["pending"]), "lastVerifiedAt": proof.get("observedAt") if applied else None}
