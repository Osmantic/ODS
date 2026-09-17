"""Model contracts through the existing owner-bound lifecycle coordinator."""
import hashlib
import json
import os
import time
from pixel_access_bridge import AccessError, UNIT, atomic_json, private_json, digest, remaining
from pixel_settings.coordinator import _read, _identity, _valid_identity
from pixel_model_contract import ModelError, checksum, target, plan, projection


def _sha(value):
    return hashlib.sha256((json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")).hexdigest()


def _journal(value):
    required = {"kind", "phase", "token", "transactionId", "edge_revision", "edgeHeld", "beforeSha", "afterSha", "target", "boundary", "mode", "beforeIdentity"}
    if (type(value) is not dict or set(value) - required - {"outcome"} or not required <= set(value)
            or value["kind"] != "model" or value["phase"] not in ("acquiring", "held", "applying", "applied", "restoring", "releasing")
            or any(not checksum(value[key]) for key in ("token", "transactionId", "edge_revision", "beforeSha"))
            or value["afterSha"] is not None and not checksum(value["afterSha"])
            or value["mode"] not in ("sandboxed", "full-access")
            or type(value["boundary"]) is not str or len(value["boundary"]) > 4096
            or not _valid_identity(value["beforeIdentity"])
            or type(value["edgeHeld"]) is not bool
            or "outcome" in value and value["outcome"] not in ("commit", "rollback")):
        raise AccessError("invalid-model-transition")
    if value["target"] is not None: target(value["target"])
    return value


def _write(bridge, journal):
    atomic_json(bridge.state / "transition.json", _journal(journal))


def _config(bridge):
    return _read(bridge.home / ".openclaw/openclaw.json", bridge.owner.pw_uid)


def _readback(bridge, journal=None):
    native = bridge.native()
    if native.get("stopped"): raise AccessError("model-runtime-unavailable")
    body = {"operation": "model-readback", "token": journal["token"], "revision": native["revision"]} if journal else {"operation": "model-status"}
    result = bridge.http(bridge.native_origin, "/pixel-ods/access-runtime", bridge.native_key, body)
    identity = _identity(bridge)
    if (type(result) is not dict or result.get("schemaVersion") != 1 or result.get("pid") != identity["pid"]
            or result.get("revision") != native["revision"] or result.get("source") != "current-model-contract"
            or type(result.get("observedAt")) is not str):
        raise AccessError("model-runtime-unavailable")
    target(result.get("contract"))
    return result, identity


def _hold(bridge, journal):
    edge = bridge.edge()
    revision = edge["revision"] if edge.get("phase") == "idle" else journal["edge_revision"]
    edge = bridge.edge("recover" if edge.get("phase") == "interrupted" else "acquire", journal["token"], revision)
    journal["edge_revision"] = edge["revision"]
    journal["edgeHeld"] = True
    _write(bridge, journal)
    native = bridge.native("acquire", journal["token"])
    if native.get("phase") != "held" or edge.get("phase") != "held" or native.get("active") or edge.get("streams"):
        raise AccessError("runtime-busy")


def _busy(bridge, journal):
    try:
        _hold(bridge, journal)
        return False
    except AccessError:
        return True


def _verify(bridge, journal, expected_sha):
    _hold(bridge, journal)
    config, config_sha = _config(bridge)
    if config_sha != expected_sha or bridge.unit_boundary() != journal["boundary"]:
        raise AccessError("model-config-changed")
    expected = projection(config)
    observed, identity = _readback(bridge, journal)
    if any(observed.get(key) != value for key, value in expected.items()):
        raise AccessError("model-runtime-mismatch")
    if _config(bridge)[1] != expected_sha or _identity(bridge) != identity:
        raise AccessError("model-process-changed")
    return observed, identity


def _activate(bridge, journal, expected_sha):
    # Avoid an unnecessary second restart after a lost verified reply.
    try:
        return _verify(bridge, journal, expected_sha)
    except AccessError as error:
        if error.code not in ("model-runtime-mismatch", "model-runtime-unavailable", "gateway-unavailable", "host-command-failed", "settings-process-not-active"):
            raise
    _hold(bridge, journal)
    if bridge.unit_boundary() != journal["boundary"]: raise AccessError("model-access-boundary-changed")
    try:
        before = _identity(bridge)
    except AccessError:
        # A fixed-unit restart may have stopped before loading the candidate.
        # Only its persisted exact held token authorizes restoring that unit.
        if journal["phase"] not in ("applying", "restoring") or bridge.stopped_native(journal["token"]).get("stopped") is not True:
            raise
        before = journal["beforeIdentity"]
    bridge.command(["systemctl", "restart", UNIT], timeout=60)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            current = _identity(bridge)
            if current["boot"] == before["boot"] and current["started"] > before["started"] and current["pid"] != before["pid"]:
                return _verify(bridge, journal, expected_sha)
        except AccessError as error:
            if error.code == "operation-deadline-exceeded": raise
        time.sleep(remaining(0.25))
    raise AccessError("model-restart-unconfirmed")


def _status(bridge):
    journal = bridge.pending()
    if journal: _journal(journal)
    config, config_sha = _config(bridge)
    owner = bridge.worker("model-status")
    if owner["configSha256"] != config_sha: raise AccessError("model-config-changed")
    if owner["pending"] and (not journal or owner["transactionId"] != journal["transactionId"]):
        raise AccessError("model-recovery-journal-missing")
    observed, identity = _readback(bridge)
    native = bridge.native()
    public_phase = "held"
    if journal:
        edge = bridge.edge()
        if (not journal["edgeHeld"] or edge.get("capability") != "available" or edge.get("phase") != "held"
                or edge.get("revision") != journal["edge_revision"] or edge.get("streams") != 0
                or not bridge.owns_native_hold(native, journal["token"])):
            raise AccessError("model-hold-unconfirmed")
        completed = owner.get("completion")
        if not owner["pending"] and not (journal["phase"] == "releasing" and completed and
                completed == {"transactionId": journal["transactionId"], "outcome": journal.get("outcome"), "configSha256": config_sha}):
            raise AccessError("model-begin-unconfirmed")
        if journal["target"] is not None and config_sha == journal["afterSha"]:
            expected = projection(plan(private_json(bridge.state / "model-before.json", 0), journal["target"]))
            if all(observed.get(key) == value for key, value in expected.items()): public_phase = "applied"
    if not journal and any(observed.get(key) != value for key, value in projection(config).items()):
        raise AccessError("model-runtime-mismatch")
    if (_config(bridge)[1] != config_sha or _identity(bridge) != identity or native.get("revision") != observed["revision"]):
        raise AccessError("model-inspection-changed")
    done_path = bridge.state / "model-completed.json"
    done = private_json(done_path, 0, 8192) if done_path.exists() else None
    if done and (not checksum(done.get("transactionId")) or done.get("outcome") not in ("commit", "rollback")
                 or not checksum(done.get("configSha256"))): raise AccessError("invalid-model-completion")
    done = done if done and done["configSha256"] == config_sha else None
    return {"schemaVersion": 1, "status": public_phase if journal else "completed" if done else "ready",
            "revision": digest([config_sha, observed["revision"], identity, journal]), "contract": observed["contract"],
            "pending": bool(journal), "transactionId": journal["transactionId"] if journal else done["transactionId"] if done else None,
            "outcome": None if journal else done["outcome"] if done else None}


def control(bridge, operation, request=None):
    if operation not in ("model-status", "model-begin", "model-apply", "model-finish"):
        raise AccessError("invalid-model-operation")
    expected_keys = {"model-begin": {"revision", "transactionId"}, "model-apply": {"transactionId", "target"}, "model-finish": {"transactionId", "outcome"}}
    if operation != "model-status" and (type(request) is not dict or set(request) != expected_keys[operation] or not checksum(request["transactionId"])):
        raise AccessError("invalid-model-request")
    if operation == "model-begin" and not checksum(request["revision"]): raise AccessError("invalid-model-request")
    if operation == "model-apply": target(request["target"])
    if operation == "model-finish" and request["outcome"] not in ("commit", "rollback"): raise AccessError("invalid-model-request")
    with bridge.bounded(300), bridge.locked():
        bridge.discover()
        if operation == "model-status": return _status(bridge)
        journal = bridge.pending()
        if journal:
            _journal(journal)
            if journal["transactionId"] != request["transactionId"]: raise AccessError("model-transaction-conflict")
        else:
            snapshot = _status(bridge)
            if snapshot["transactionId"] == request["transactionId"]:
                if operation == "model-finish" and snapshot["outcome"] == request["outcome"]: return snapshot
                raise AccessError("model-transaction-completed")
            if operation != "model-begin": raise AccessError("model-transaction-missing")
            if snapshot["revision"] != request["revision"]: raise AccessError("model-inspection-changed")
            access = bridge.inspect()
            if access["busy"]: raise AccessError("runtime-busy")
            if access["configured_mode"] not in ("sandboxed", "full-access"): raise AccessError("model-access-mode-unknown")
            config, config_sha = _config(bridge)
            journal = dict(kind="model", phase="acquiring", token=os.urandom(32).hex(), transactionId=request["transactionId"],
                           edge_revision=access["_edge"]["revision"], edgeHeld=False, beforeSha=config_sha, afterSha=None, target=None,
                           boundary=bridge.unit_boundary(), mode=access["configured_mode"], beforeIdentity=_identity(bridge))
            atomic_json(bridge.state / "model-before.json", config)
            _write(bridge, journal)
        _hold(bridge, journal)
        config, config_sha = _config(bridge)
        worker = lambda action, **kwargs: bridge.worker(action, config_hash=_config(bridge)[1], transaction_id=journal["transactionId"],
                                                         busy=lambda: _busy(bridge, journal), **kwargs)
        if operation == "model-begin":
            if journal["phase"] == "acquiring":
                if config_sha != journal["beforeSha"]: raise AccessError("model-config-changed")
                worker("model-begin")
                journal["phase"] = "held"; _write(bridge, journal)
            return _status(bridge)
        if operation == "model-apply":
            if journal["phase"] in ("restoring", "releasing"): raise AccessError("model-transaction-finishing")
            if journal["target"] is not None and journal["target"] != request["target"]: raise AccessError("model-target-changed")
            before = private_json(bridge.state / "model-before.json", 0)
            proposed = plan(before, request["target"])
            journal.update(target=request["target"], afterSha=_sha(proposed), phase="applying")
            _write(bridge, journal)
            result = worker("model-apply", model_target=request["target"])
            if result["configSha256"] != journal["afterSha"]: raise AccessError("model-projection-mismatch")
            _activate(bridge, journal, journal["afterSha"])
            journal["phase"] = "applied"; _write(bridge, journal)
            return _status(bridge)
        outcome = request["outcome"]
        if "outcome" in journal and journal["outcome"] != outcome: raise AccessError("model-outcome-conflict")
        expected_sha = journal["afterSha"] if outcome == "commit" else journal["beforeSha"]
        if outcome == "commit" and journal["phase"] == "applying" and expected_sha:
            # The process can have accepted the target before its HTTP reply or
            # phase checkpoint was lost. Prove it, without dispatching apply again.
            _verify(bridge, journal, expected_sha)
            journal["phase"] = "applied"; _write(bridge, journal)
        if outcome == "commit" and journal["phase"] not in ("applied", "releasing"):
            raise AccessError("model-apply-unverified")
        owner = bridge.worker("model-status")
        if outcome == "rollback" and owner["pending"]:
            journal["phase"] = "restoring"; _write(bridge, journal)
            worker("model-rollback")
        elif outcome == "rollback" and config_sha != journal["beforeSha"]:
            raise AccessError("model-rollback-conflict")
        observed, identity = _activate(bridge, journal, expected_sha)
        # Re-qualify execution without changing sandbox/full-access policy.
        bridge.provision_probe()
        proof = bridge.native("probe", journal["token"])
        if proof.get("pid") != identity["pid"] or proof.get("proof", {}).get("mode") != journal["mode"]:
            raise AccessError("model-access-proof-failed")
        atomic_json(bridge.state / "verified.json", {"pid": proof["pid"], "proof": proof["proof"],
                    "config_sha256": expected_sha, "boundary": journal["boundary"]})
        _verify(bridge, journal, expected_sha)
        if owner["pending"]: worker("model-finish", model_outcome=outcome)
        journal.update(phase="releasing", outcome=outcome); _write(bridge, journal)
        atomic_json(bridge.state / "model-completed.json", {"transactionId": journal["transactionId"], "outcome": outcome, "configSha256": expected_sha})
        bridge.edge("release", journal["token"], journal["edge_revision"])
        bridge.native("release", journal["token"])
        try:
            (bridge.state / "transition.json").unlink()
            fd = os.open(bridge.state, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        except OSError:
            _write(bridge, journal)
            raise
        return _status(bridge)
