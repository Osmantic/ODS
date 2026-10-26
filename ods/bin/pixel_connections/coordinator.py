"""Connected apps inside the EXISTING privileged access coordinator: the custodian.

It starts a Google sign-in, completes it through the Source Broker identity,
keeps the owner's record, runs the broker units, and changes Portal's gateway
under the same admission gates and access proof as every other Portal
transition. All gateway authority is the supplied SystemdAccessBridge.

A connection lives in a root journal (``connection.json``) from consent until
Portal's gateway matches the record, so every stuck step is visible in status
and resumable with one operation: ``connection-activate``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any

from pixel_access_bridge import AccessError, atomic_json, remaining, runtime_config_path
from pixel_model_coordinator import _managed_marker, _marker_digest
from pixel_settings.coordinator import _identity, _read, _valid_identity

from . import activity, approvals, catalog, custody, drafts, lifecycle, oauth, planner, state

LIFECYCLE = lifecycle.PATH.name
PATHS = custody.Paths()
RUN = custody.system_run
RECORD = state.PATH
# Accounts that may own the record and the journal: root in production.
OWNERS = (0,)
HEX = re.compile(r"^[a-f0-9]{64}$")
# Scopes that satisfy a capability, as the Source Broker reads them.
GRANTED_BY = {
    "calendar.read": frozenset({catalog.CALENDAR_READ_SCOPE, catalog.CALENDAR_WRITE_SCOPE,
                                "https://www.googleapis.com/auth/calendar.readonly",
                                "https://www.googleapis.com/auth/calendar"}),
    "calendar.write": frozenset({catalog.CALENDAR_WRITE_SCOPE, "https://www.googleapis.com/auth/calendar"}),
    # Private changes need no scope of their own: they are calendar changes the broker bounds.
    "calendar.direct": frozenset({catalog.CALENDAR_WRITE_SCOPE, "https://www.googleapis.com/auth/calendar"}),
    "gmail.read": frozenset({catalog.GMAIL_READ_SCOPE, "https://www.googleapis.com/auth/gmail.modify",
                             "https://mail.google.com/"}),
    "gmail.drafts": frozenset({catalog.GMAIL_COMPOSE_SCOPE, "https://www.googleapis.com/auth/gmail.modify",
                               "https://mail.google.com/"}),
}
CAPABILITY_IDS = tuple(capability["id"] for capability in catalog.GOOGLE["capabilities"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _instant(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- lifecycle

def _lifecycle_path(bridge) -> Path:
    return Path(bridge.state) / LIFECYCLE


def read_lifecycle(path: Path) -> dict[str, Any] | None:
    """The journal; an unusable one is removed, so it can never block the owner's next step.

    Nothing depends on it alone: activation reconciles with the record, and a new
    sign-in replaces every unfinished one.
    """
    try:
        return lifecycle.read(path, owners=OWNERS)
    except lifecycle.LifecycleError:
        path.unlink(missing_ok=True)
        return None


def _write_lifecycle(bridge, value: dict[str, Any]) -> None:
    lifecycle.write(_lifecycle_path(bridge), dict(value, updatedAt=_iso(_now())))


def _clear_lifecycle(bridge) -> None:
    _lifecycle_path(bridge).unlink(missing_ok=True)


def _lifecycle(phase: str, **values: Any) -> dict[str, Any]:
    base = {"kind": "connection", "connector": "google", "phase": phase, "flowId": None, "stateHash": None,
            "redirectUri": None, "expiresAt": None, "request": None, "error": None, "updatedAt": None}
    base.update(values)
    return base


# ---------------------------------------------------------------- the owner's record

def _record() -> dict[str, Any] | None:
    try:
        return state.read(RECORD, owners=OWNERS)
    except state.StateError:
        raise AccessError("connection-record-unusable") from None


def _write_record(record: dict[str, Any] | None) -> None:
    """Atomically replace /etc/ods/portal-connections.json; None removes it."""
    if record is None:
        RECORD.unlink(missing_ok=True)
        return
    data = (json.dumps(state.validate(record), indent=2, sort_keys=True) + "\n").encode("utf-8")
    RECORD.parent.mkdir(mode=0o755, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".portal-connections.", dir=RECORD.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o644)
            os.fsync(handle.fileno())
        os.replace(temporary, RECORD)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def gateway_target(record: dict[str, Any] | None, *, custody_held: bool) -> dict[str, Any]:
    """What Portal's gateway should run for the record, as the installer would render it."""
    google = (record or {}).get("connectors", {}).get("google")
    capabilities = state.effective_capabilities(google) if google and custody_held else None
    return dict(planner.limbs(capabilities), userTimezone=(record or {}).get("ownerTimeZone"))


# ---------------------------------------------------------------- Full Access (Decision 4)

def _full_access(bridge) -> bool:
    """Whether Portal is set to Full Access; anything but a Sandbox ODS can read counts as Full Access."""
    try:
        return bridge.worker()["configured_status"] != "sandboxed"
    except AccessError:
        return True  # Unknown: pause Portal's own changes; the next activation reads the mode again.


def _with_pause(record: dict[str, Any], paused: bool) -> dict[str, Any]:
    google = dict(record["connectors"]["google"], directPaused=paused)
    return dict(record, updatedAt=_iso(_now()), connectors=dict(record["connectors"], google=google))


def _follow_mode(bridge, record: dict[str, Any]) -> dict[str, Any]:
    """Record whether Portal has Full Access now, so its own calendar changes pause or resume with it."""
    paused = _full_access(bridge)
    if record["connectors"]["google"].get("directPaused", False) == paused:
        return record
    record = _with_pause(record, paused)
    _write_record(record)
    return record


def _install_broker_env(record: dict[str, Any], target: dict[str, Any]) -> None:
    """The broker's environment for the record; queued private changes that could not run go first."""
    google = record["connectors"]["google"]
    if target["calendarDirect"] and not custody.direct_enabled(PATHS):
        approvals.discard_unsent_direct(PATHS)
    custody.install_env(PATHS, custody.connection_env(
        PATHS, gateway=target, calendar_id=google["calendarId"], time_zone=record["ownerTimeZone"],
        gmail_window_days=google["gmailWindowDays"]))


def pause_for_full_access(bridge) -> None:
    """Before Portal gets Full Access, stop every calendar change it could make by itself.

    The broker stops at once. Portal's gateway catches up with the activation that
    follows the mode change; until then its private changes are refused, never queued.
    """
    with bridge.bounded(60), bridge.locked():
        try:
            record = state.read(RECORD, owners=OWNERS)
        except state.StateError:
            record = None  # Nothing renders from an unusable record; the broker still stops below.
        google = (record or {"connectors": {}})["connectors"].get("google")
        try:
            if record and google and google["capabilities"]["calendar.direct"] and not google.get("directPaused"):
                _write_record(_with_pause(record, True))
            custody.pause_direct(PATHS, RUN)
        except (custody.CustodyError, OSError):
            raise AccessError("calendar-pause-failed") from None


def follow_access_mode(bridge) -> bool:
    """After an access-mode change, whether Portal's gateway must catch up with a pause or resume.

    The new mode is recorded first, and the step is journaled as activating, so a
    catch-up that cannot run now (Portal busy) waits in Settings behind Finish.
    """
    with bridge.bounded(60), bridge.locked():
        bridge.discover()
        try:
            record = state.read(RECORD, owners=OWNERS)
        except state.StateError:
            return False
        google = (record or {"connectors": {}})["connectors"].get("google")
        if (not record or not google or not google["capabilities"]["calendar.direct"]
                or not custody.holds_sign_in(PATHS)):
            return False
        journal = read_lifecycle(_lifecycle_path(bridge))
        if journal and journal["phase"] == "consent":
            return False  # The open sign-in's own activation renders the mode.
        _follow_mode(bridge, record)
        _write_lifecycle(bridge, _lifecycle("activating"))
        return True


# ---------------------------------------------------------------- requests

def _capabilities(value: Any) -> dict[str, bool]:
    if (not isinstance(value, dict) or set(value) != set(CAPABILITY_IDS)
            or any(type(flag) is not bool for flag in value.values())):
        raise AccessError("invalid-connection-request")
    for capability in catalog.GOOGLE["capabilities"]:
        if value[capability["id"]] and (not capability["available"]
                                        or not all(value[need] for need in capability["requires"])):
            raise AccessError("capability-unavailable")
    if not any(value.values()):
        raise AccessError("invalid-connection-request")
    return value


def _begin_request(request: Any) -> dict[str, Any]:
    keys = {"connector", "client", "capabilities", "cloudModels", "ownerTimeZone", "loginHint", "redirectPort"}
    if not isinstance(request, dict) or set(request) != keys or request["connector"] != "google":
        raise AccessError("invalid-connection-request")
    try:
        google_client = oauth.client(request["client"])
    except oauth.OAuthError as error:
        raise AccessError(error.code) from None
    zone, hint, port = request["ownerTimeZone"], request["loginHint"], request["redirectPort"]
    if (type(request["cloudModels"]) is not bool
            or zone is not None and not state.valid_time_zone(zone)
            or hint is not None and (type(hint) is not str or not oauth.EMAIL.fullmatch(hint))
            or type(port) is not int or not 1024 <= port <= 65535):
        raise AccessError("invalid-connection-request")
    return dict(request, client=google_client, capabilities=_capabilities(request["capabilities"]))


def _granted(requested: dict[str, bool], scopes: list[str]) -> dict[str, bool]:
    """What Google granted of the owner's choices. Google lets the owner untick each
    permission, so a capability whose requirement stayed off stays off too."""
    granted = dict.fromkeys(CAPABILITY_IDS, False)
    # The catalog lists each capability after the ones it needs.
    for capability in catalog.GOOGLE["capabilities"]:
        name = capability["id"]
        granted[name] = (requested[name] and bool(GRANTED_BY[name] & set(scopes))
                         and all(granted[need] for need in capability["requires"]))
    return granted


# ---------------------------------------------------------------- sign-in

def begin(bridge, request: Any) -> dict[str, Any]:
    """Start a consent flow; any unfinished step is replaced.

    An interrupted gateway change is settled by the activation that follows
    consent, which reconciles Portal's gateway with the record as it is then.
    """
    request = _begin_request(request)
    with bridge.bounded(60), bridge.locked():
        bridge.discover()
        # Only one sign-in is ever open, so every earlier handoff goes, journaled or not.
        custody.discard_flows(PATHS)
        _clear_lifecycle(bridge)
        custody.prepare(PATHS, RUN, reader=bridge.owner.pw_name)
        scopes = [*oauth.IDENTITY_SCOPES, *catalog.scopes_for("google", request["capabilities"])]
        try:
            handoff, public = oauth.new_flow(google_client=request["client"], scopes=scopes, now=_now(),
                                             redirect_port=request["redirectPort"], login_hint=request["loginHint"])
        except oauth.OAuthError as error:
            raise AccessError(error.code) from None
        custody.write_handoff(PATHS, f"{public['flowId']}.flow.json", handoff)
        _write_lifecycle(bridge, _lifecycle(
            "consent", flowId=public["flowId"], stateHash=public["stateHash"], redirectUri=public["redirectUri"],
            expiresAt=public["expiresAt"], request={"capabilities": request["capabilities"],
                                                    "cloudModels": request["cloudModels"],
                                                    "ownerTimeZone": request["ownerTimeZone"]}))
        return {"flowId": public["flowId"], "authUrl": public["authUrl"], "redirectUri": public["redirectUri"],
                "expiresAt": public["expiresAt"]}


def _complete_request(request: Any) -> dict[str, Any]:
    if (not isinstance(request, dict) or set(request) not in ({"flowId", "state", "code"}, {"flowId", "state", "error"})
            or type(request["flowId"]) is not str or not oauth.FLOW_ID.fullmatch(request["flowId"])
            or type(request["state"]) is not str or not oauth.STATE.fullmatch(request["state"])):
        raise AccessError("invalid-connection-request")
    if "code" in request and (type(request["code"]) is not str or not oauth.CODE.fullmatch(request["code"])):
        raise AccessError("invalid-connection-request")
    if "error" in request and request["error"] not in ("access-denied", "consent-failed"):
        raise AccessError("invalid-connection-request")
    return request


def _failed(error_class: str) -> dict[str, Any]:
    return {"outcome": "failed", "errorClass": error_class, "account": None, "capabilities": None, "missing": []}


def _sign_in_failed(bridge, error_class: str) -> dict[str, Any]:
    """Keep why the sign-in did not connect, for Settings; the next sign-in replaces it."""
    _write_lifecycle(bridge, _lifecycle("failed", error=error_class))
    return _failed(error_class)


def complete(bridge, request: Any) -> dict[str, Any]:
    """Finish consent: the broker exchanges the code, and the owner's record names what Google granted."""
    request = _complete_request(request)
    with bridge.bounded(150), bridge.locked():
        bridge.discover()
        journal = read_lifecycle(_lifecycle_path(bridge))
        if not journal or journal["phase"] != "consent" or journal["flowId"] != request["flowId"]:
            raise AccessError("sign-in-flow-unknown")
        if _now() >= _instant(journal["expiresAt"]):
            custody.discard_flow(PATHS, journal["flowId"])
            _sign_in_failed(bridge, "sign-in-flow-expired")
            raise AccessError("sign-in-flow-expired")
        # A forged or stale callback leaves the owner's real flow open.
        if not hmac.compare_digest(oauth.state_hash(request["state"]), journal["stateHash"]):
            raise AccessError("sign-in-state-mismatch")
        if "error" in request:
            custody.discard_flow(PATHS, journal["flowId"])
            return _sign_in_failed(bridge, request["error"])
        custody.write_handoff(PATHS, f"{journal['flowId']}.code.json", {
            "schemaVersion": 1, "kind": "pixel-source-oauth-code", "flowId": journal["flowId"],
            "state": request["state"], "code": request["code"]})
        try:
            result = custody.exchange(PATHS, RUN, journal["flowId"])
        except custody.CustodyError as error:
            _sign_in_failed(bridge, error.code)
            raise
        if result["outcome"] != "connected":
            return _sign_in_failed(bridge, result["errorClass"] or "unknown")
        wanted = journal["request"]
        granted = _granted(wanted["capabilities"], result["grantedScopes"])
        missing = [name for name in CAPABILITY_IDS if wanted["capabilities"][name] and not granted[name]]
        if result["account"] is None or not any(granted.values()):
            # Nothing usable came back, so nothing of it may stay on this box.
            custody.revoke(PATHS, RUN)
            return _sign_in_failed(bridge, "account-unverified" if result["account"] is None else "missing-scope")
        record = _record() or {"schemaVersion": state.SCHEMA_VERSION, "kind": state.KIND, "updatedAt": None,
                               "ownerTimeZone": None, "connectors": {}}
        data = catalog.GOOGLE["data"]
        now = _iso(_now())
        record = dict(record, updatedAt=now, ownerTimeZone=wanted["ownerTimeZone"] or record["ownerTimeZone"],
                      connectors=dict(record["connectors"], google={
                          "status": "connected", "account": result["account"], "calendarId": data["calendarId"],
                          "gmailWindowDays": data["gmailWindowDays"], "capabilities": granted,
                          "cloudModels": wanted["cloudModels"], "connectedAt": now,
                          "directPaused": _full_access(bridge)}))
        _write_record(record)
        target = gateway_target(record, custody_held=True)
        _install_broker_env(record, target)
        custody.start(RUN, direct=target["calendarDirect"])
        _write_lifecycle(bridge, _lifecycle("activating"))
        return {"outcome": "connected", "errorClass": None, "account": result["account"], "capabilities": granted,
                "missing": missing}


def cancel(bridge, request: Any) -> dict[str, Any]:
    if (not isinstance(request, dict) or set(request) != {"flowId"} or type(request["flowId"]) is not str
            or not oauth.FLOW_ID.fullmatch(request["flowId"])):
        raise AccessError("invalid-connection-request")
    with bridge.bounded(30), bridge.locked():
        journal = read_lifecycle(_lifecycle_path(bridge))
        if journal and journal["phase"] == "consent" and journal["flowId"] == request["flowId"]:
            custody.discard_flow(PATHS, journal["flowId"])
            _clear_lifecycle(bridge)
        return {"outcome": "cancelled"}


def disconnect(bridge, request: Any) -> dict[str, Any]:
    """Stop syncing, revoke at Google, delete everything derived, then let Portal forget the tools.

    Running it again is safe: every step accepts the state the previous run left.
    """
    if request != {"connector": "google"}:
        raise AccessError("invalid-connection-request")
    with bridge.bounded(150), bridge.locked():
        bridge.discover()
        journal = read_lifecycle(_lifecycle_path(bridge))
        if journal and journal["phase"] == "consent":
            custody.discard_flow(PATHS, journal["flowId"])
        custody.stop(PATHS, RUN)
        revoked = custody.revoke(PATHS, RUN)
        custody.erase(PATHS)
        custody.remove_env(PATHS)
        try:
            record = state.read(RECORD, owners=OWNERS)
        except state.StateError:
            # An unreadable record is how an owner gets here; disconnecting replaces it.
            _write_record(None)
            record = None
        if record is not None:
            connectors = {name: value for name, value in record["connectors"].items() if name != "google"}
            keep = connectors or record["ownerTimeZone"]
            _write_record(dict(record, updatedAt=_iso(_now()), connectors=connectors) if keep else None)
        _write_lifecycle(bridge, _lifecycle("deactivating"))
        return {"outcome": "disconnected", "revoked": revoked == "revoked"}


def sync(bridge, request: Any) -> dict[str, Any]:
    if request != {}:
        raise AccessError("invalid-connection-request")
    with bridge.bounded(30), bridge.locked():
        record = _record()
        if not record or "google" not in record["connectors"] or not custody.holds_sign_in(PATHS):
            raise AccessError("connection-not-connected")
        custody.sync(RUN)
        return {"outcome": "started"}


# ---------------------------------------------------------------- calendar changes and Gmail drafts Portal suggests

def _approval_ids(bridge) -> approvals.Ids | None:
    """None before the broker identity exists, when Portal cannot have suggested anything."""
    try:
        broker_uid, _gid = custody.broker_ids()
    except KeyError:
        return None
    return approvals.Ids(reader_uid=bridge.owner.pw_uid, reader_gid=bridge.owner.pw_gid, broker_uid=broker_uid)


def _change_request(request: Any, keys: set, *, drafts_too: bool = False) -> dict[str, Any]:
    """A calendar change, or with ``drafts_too`` a Gmail draft, named exactly."""
    if (not isinstance(request, dict) or set(request) != keys or type(request["proposalId"]) is not str
            or not (approvals.PROPOSAL_ID.fullmatch(request["proposalId"])
                    or drafts_too and drafts.DRAFT_ID.fullmatch(request["proposalId"]))
            or "sha256" in keys and (type(request["sha256"]) is not str
                                     or not approvals.SHA256.fullmatch(request["sha256"]))):
        raise AccessError("invalid-connection-request")
    return request


def _is_draft(request: dict[str, Any]) -> bool:
    return drafts.DRAFT_ID.fullmatch(request["proposalId"]) is not None


def _connected_google() -> dict[str, Any]:
    google = (_record() or {"connectors": {}})["connectors"].get("google")
    if not google or not custody.holds_sign_in(PATHS):
        raise AccessError("connection-not-connected")
    return google


def proposals(bridge, request: Any) -> dict[str, Any]:
    """The calendar changes Portal suggested, oldest first, each with the hash that approves it."""
    if request != {}:
        raise AccessError("invalid-connection-request")
    with bridge.bounded(30), bridge.locked():
        bridge.discover()
        ids = _approval_ids(bridge)
        return approvals.listing(PATHS, ids) if ids else {"proposals": [], "more": 0}


def approve(bridge, request: Any) -> dict[str, Any]:
    """Make the one change, or create the one draft, the owner approved, exactly as they saw it."""
    request = _change_request(request, {"proposalId", "sha256"}, drafts_too=True)
    draft = _is_draft(request)
    name = "gmail-draft" if draft else "calendar-change"
    with bridge.bounded(150), bridge.locked():
        bridge.discover()
        capabilities = _connected_google()["capabilities"]
        if draft and not capabilities["gmail.drafts"]:
            raise AccessError("gmail-drafts-off")
        if not draft and not capabilities["calendar.write"]:
            raise AccessError("calendar-changes-off")
        ids = _approval_ids(bridge)
        if ids is None:
            raise AccessError(f"{name}-unknown")
        try:
            result = (drafts.approve if draft else approvals.approve)(PATHS, RUN, ids, request)
        except custody.CustodyError:
            # systemctl did not answer in time. The broker's journal keeps it from happening twice.
            raise AccessError(f"{name}-unconfirmed") from None
        if result["outcome"] == "applied" and not draft:
            try:
                custody.sync(RUN)
            except custody.CustodyError:
                pass  # Only a faster refresh is lost: the timer refreshes within a minute.
        return result


def reconcile(bridge, request: Any) -> dict[str, Any]:
    """Ask Google whether an unconfirmed new event or draft was made; nothing is sent again."""
    request = _change_request(request, {"proposalId"}, drafts_too=True)
    draft = _is_draft(request)
    name = "gmail-draft" if draft else "calendar-change"
    with bridge.bounded(150), bridge.locked():
        bridge.discover()
        _connected_google()
        ids = _approval_ids(bridge)
        if ids is None:
            raise AccessError(f"{name}-unknown")
        try:
            return (drafts.reconcile if draft else approvals.reconcile)(PATHS, RUN, ids, request)
        except custody.CustodyError:
            raise AccessError(f"{name}-unconfirmed") from None


def drafts_waiting(bridge, request: Any) -> dict[str, Any]:
    """The Gmail drafts Portal wrote, oldest first, each with the hash that approves it."""
    if request != {}:
        raise AccessError("invalid-connection-request")
    with bridge.bounded(30), bridge.locked():
        bridge.discover()
        ids = _approval_ids(bridge)
        return drafts.listing(PATHS, ids) if ids else {"drafts": [], "more": 0}


def _undo_ledger(bridge) -> Path:
    return Path(bridge.state) / "calendar-undo.json"


def changes_made(bridge, request: Any) -> dict[str, Any]:
    """The private changes Portal made by itself this week, each with whether it can be undone."""
    if request != {}:
        raise AccessError("invalid-connection-request")
    with bridge.bounded(30), bridge.locked():
        bridge.discover()
        ids = _approval_ids(bridge)
        return activity.listing(PATHS, ids, _undo_ledger(bridge)) if ids else {"changes": [], "more": 0}


def undo(bridge, request: Any) -> dict[str, Any]:
    """Reverse one change Portal made by itself; the owner's click is the approval."""
    request = _change_request(request, {"proposalId"})
    with bridge.bounded(150), bridge.locked():
        bridge.discover()
        if not _connected_google()["capabilities"]["calendar.write"]:
            raise AccessError("calendar-changes-off")
        ids = _approval_ids(bridge)
        if ids is None:
            raise AccessError("calendar-change-unknown")
        try:
            result = activity.undo(PATHS, RUN, ids, _undo_ledger(bridge), request)
        except custody.CustodyError:
            # systemctl did not answer in time. The broker's journal keeps the reversal from happening twice.
            raise AccessError("calendar-change-unconfirmed") from None
        if result["outcome"] == "applied":
            try:
                custody.sync(RUN)
            except custody.CustodyError:
                pass  # Only a faster refresh is lost: the timer refreshes within a minute.
        return result


def decline(bridge, request: Any) -> dict[str, Any]:
    """Take a suggested change or draft off the list, connected or not."""
    request = _change_request(request, {"proposalId"}, drafts_too=True)
    with bridge.bounded(30), bridge.locked():
        return approvals.decline(PATHS, request)


# ---------------------------------------------------------------- Portal's gateway

def _journal(value: Any) -> dict[str, Any]:
    required = {"kind", "phase", "token", "transactionId", "edge_revision", "beforeSha", "boundary", "mode",
                "beforeIdentity", "target", "markerBeforeSha"}
    optional = {"error", "outcome", "restartIdentity", "noOwnerWrite"}
    if (not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional
            or value["kind"] != "connection"
            or value["phase"] not in ("acquiring", "invoking", "restarting", "releasing")
            or any(type(value[key]) is not str or not HEX.fullmatch(value[key])
                   for key in ("token", "transactionId", "edge_revision", "beforeSha"))
            or value["markerBeforeSha"] is not None and not HEX.fullmatch(str(value["markerBeforeSha"]))
            or value["mode"] not in ("sandboxed", "full-access")
            or type(value["boundary"]) is not str or not 1 <= len(value["boundary"]) <= 4096
            or not _valid_identity(value["beforeIdentity"])
            or "restartIdentity" in value and not _valid_identity(value["restartIdentity"])):
        raise AccessError("invalid-connection-transition")
    planner.target(value["target"])
    return value


def _write(bridge, journal: dict[str, Any]) -> None:
    atomic_json(Path(bridge.state) / "transition.json", _journal(journal))


def _acquire(bridge, journal: dict[str, Any]) -> None:
    edge = bridge.edge()
    revision = edge["revision"] if edge.get("phase") == "idle" else journal["edge_revision"]
    edge = bridge.edge("recover" if edge.get("phase") == "interrupted" else "acquire", journal["token"], revision)
    journal["edge_revision"] = edge["revision"]
    _write(bridge, journal)
    bridge.native("acquire", journal["token"])


def _busy(bridge, journal: dict[str, Any]) -> bool:
    native = bridge.native("acquire", journal["token"])
    edge = bridge.edge("acquire", journal["token"], journal["edge_revision"])
    return (native.get("phase") != "held" or edge.get("phase") != "held"
            or bool(native.get("active") or edge.get("streams")))


def _release(bridge, journal: dict[str, Any]) -> None:
    bridge.edge("release", journal["token"], journal["edge_revision"])
    bridge.native("release", journal["token"])
    (Path(bridge.state) / "transition.json").unlink()
    fd = os.open(bridge.state, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _verify(bridge, journal: dict[str, Any]) -> dict[str, Any]:
    """Re-prove the access mode on the restarted gateway; the proof binds this exact config."""
    if _busy(bridge, journal):
        raise AccessError("runtime-busy")
    identity = _identity(bridge)
    _current, config_hash = _read(runtime_config_path(bridge), bridge.owner.pw_uid)
    native = bridge.native()
    if native.get("pid") != identity["pid"]:
        raise AccessError("connection-process-changed")
    if bridge.unit_boundary() != journal["boundary"]:
        raise AccessError("connection-access-boundary-changed")
    bridge.provision_probe()
    proof = bridge.native("probe", journal["token"])
    if (proof.get("pid") != identity["pid"] or proof.get("proof", {}).get("mode") != journal["mode"]
            or proof.get("proof", {}).get("executed") is not True or _identity(bridge) != identity
            or _read(runtime_config_path(bridge), bridge.owner.pw_uid)[1] != config_hash
            or bridge.unit_boundary() != journal["boundary"] or _busy(bridge, journal)):
        raise AccessError("connection-runtime-proof-failed")
    atomic_json(Path(bridge.state) / "verified.json", {"pid": proof["pid"], "proof": proof["proof"],
                "config_sha256": config_hash, "boundary": journal["boundary"]})
    return identity


def _restart(bridge, journal: dict[str, Any]) -> str:
    """The owner worker's connection-activate hook: restart the fixed unit and re-prove access."""
    if _busy(bridge, journal):
        return "unavailable"
    try:
        before = _identity(bridge)
    except AccessError:
        # Only a crash of our already-journaled restart can be resumed.
        if journal["phase"] != "restarting" or "restartIdentity" not in journal:
            return "unavailable"
        if bridge.stopped_native(journal["token"]).get("stopped") is not True:
            return "unavailable"
        before = journal["restartIdentity"]
    if bridge.unit_boundary() != journal["boundary"]:
        return "unavailable"
    journal.update(phase="restarting", restartIdentity=before)
    _write(bridge, journal)
    bridge.gateway_service.restart(timeout=60)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            now = _identity(bridge)
            if now["boot"] == before["boot"] and now["pid"] != before["pid"] and now["started"] > before["started"]:
                bridge.native("acquire", journal["token"], timeout=3)
                if bridge.http(bridge.native_origin, "/health", bridge.native_key, timeout=3).get("ok") is True:
                    _verify(bridge, journal)
                    return "verified"
        except AccessError as error:
            if error.code == "operation-deadline-exceeded":
                raise
        time.sleep(remaining(1))
    return "unavailable"


def _bind_marker(bridge, journal: dict[str, Any]) -> None:
    """Point the ODS-managed marker at the configuration this transaction left live."""
    path, marker = _managed_marker(bridge)
    if marker is None:
        return
    config, _config_hash = _read(runtime_config_path(bridge), bridge.owner.pw_uid)
    current = _marker_digest(config)
    if marker["configuration_sha256"] == current:
        return
    if marker["configuration_sha256"] != journal["markerBeforeSha"]:
        raise AccessError("connection-marker-drifted")
    original = path.lstat()
    marker["configuration_sha256"] = current
    descriptor, temporary = tempfile.mkstemp(prefix=".pixel-managed.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            os.fchown(handle.fileno(), bridge.owner.pw_uid, bridge.owner.pw_gid)
            os.fchmod(handle.fileno(), 0o600)
            json.dump(marker, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        observed = path.lstat()
        if ((observed.st_dev, observed.st_ino, observed.st_mtime_ns, observed.st_size)
                != (original.st_dev, original.st_ino, original.st_mtime_ns, original.st_size)):
            raise AccessError("connection-marker-drifted")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _finish(bridge, journal: dict[str, Any], outcome: str) -> str:
    _verify(bridge, journal)
    _bind_marker(bridge, journal)
    journal.update(phase="releasing", outcome=outcome)
    _write(bridge, journal)
    _release(bridge, journal)
    return outcome


def _owner_outcome(bridge, journal: dict[str, Any], result: dict[str, Any]) -> str:
    owner = bridge.worker("connection-owner-status")
    _current, config_hash = _read(runtime_config_path(bridge), bridge.owner.pw_uid)
    outcome = {"runtime-verified": "applied", "rolled-back": "rolled-back"}.get(result.get("status"))
    if (outcome is None or owner["pending"] or owner["configSha256"] != config_hash
            or result.get("configSha256") != config_hash
            or outcome == "rolled-back" and config_hash != journal["beforeSha"]):
        raise AccessError("connection-completion-mismatch")
    if not journal.get("noOwnerWrite") and owner["completion"] != {
            "transactionId": journal["transactionId"], "outcome": outcome, "configSha256": config_hash}:
        raise AccessError("connection-completion-mismatch")
    return outcome


def _resume(bridge, journal: dict[str, Any]) -> str:
    """Settle a gateway change a crash or lost reply interrupted; never apply it twice."""
    _journal(journal)
    _acquire(bridge, journal)
    owner = bridge.worker("connection-owner-status")
    hooks = dict(busy=lambda: _busy(bridge, journal), activate_connection=lambda: _restart(bridge, journal))
    if owner["pending"]:
        result = bridge.worker("connection-recover", config_hash=owner["configSha256"],
                               transaction_id=journal["transactionId"], **hooks)
    elif owner["completion"] and owner["completion"]["transactionId"] == journal["transactionId"]:
        result = {"status": "runtime-verified" if owner["completion"]["outcome"] == "applied" else "rolled-back",
                  "configSha256": owner["completion"]["configSha256"]}
    elif owner["configSha256"] == journal["beforeSha"] and journal["phase"] in ("acquiring", "invoking"):
        journal["noOwnerWrite"] = True
        result = {"status": "rolled-back", "configSha256": journal["beforeSha"]}
    else:
        raise AccessError("connection-recovery-conflict")
    return _finish(bridge, journal, _owner_outcome(bridge, journal, result))


def _change_gateway(bridge, target: dict[str, Any], answers: dict[str, Any]) -> str:
    """One gated gateway transaction toward ``target``: applied, rolled-back or unchanged.

    ``answers`` keeps ODS's onboarding contract in step, so a later model change
    renders the same connection that is live.
    """
    pending = bridge.pending()
    if pending and pending.get("kind") == "connection":
        _resume(bridge, pending)
        pending = None
    if pending:
        raise AccessError("transition-recovery-required")
    snapshot = bridge.inspect()
    if snapshot["busy"]:
        raise AccessError("runtime-busy")
    if snapshot["configured_mode"] not in ("sandboxed", "full-access"):
        raise AccessError("connection-access-mode-unknown")
    owner = bridge.worker("connection-owner-status")
    _config, config_hash = _read(runtime_config_path(bridge), bridge.owner.pw_uid)
    if owner["pending"] or owner["configSha256"] != config_hash:
        raise AccessError("connection-owner-state-changed")
    if owner["live"] == target and owner["answers"] == answers:
        return "unchanged"
    _path, marker = _managed_marker(bridge)
    journal = {"kind": "connection", "phase": "acquiring", "token": os.urandom(32).hex(),
               "transactionId": os.urandom(32).hex(), "edge_revision": snapshot["_edge"]["revision"],
               "beforeSha": config_hash, "boundary": bridge.unit_boundary(), "mode": snapshot["configured_mode"],
               "beforeIdentity": _identity(bridge), "target": target,
               "markerBeforeSha": marker["configuration_sha256"] if marker else None}
    _write(bridge, journal)
    try:
        _acquire(bridge, journal)
        if _busy(bridge, journal):
            # Nothing was written yet: let Portal keep working and try again later.
            _release(bridge, journal)
            raise AccessError("runtime-busy")
        journal["phase"] = "invoking"
        _write(bridge, journal)
        result = bridge.worker("connection-apply", config_hash=journal["beforeSha"],
                               transaction_id=journal["transactionId"], connection_target=target,
                               connection_answers=answers, busy=lambda: _busy(bridge, journal),
                               activate_connection=lambda: _restart(bridge, journal))
        return _finish(bridge, journal, _owner_outcome(bridge, journal, result))
    except AccessError as error:
        if (Path(bridge.state) / "transition.json").exists():
            journal["error"] = error.code if re.fullmatch(r"[a-z0-9-]{1,96}", error.code) else "connection-transition-failed"
            _write(bridge, journal)  # Keep the held gates and phase for explicit recovery.
        raise


def activate(bridge, request: Any) -> dict[str, Any]:
    """Bring Portal's gateway in line with the owner's record; also how a stuck step finishes."""
    if request != {}:
        raise AccessError("invalid-connection-request")
    with bridge.bounded(300), bridge.locked():
        bridge.discover()
        lifecycle = read_lifecycle(_lifecycle_path(bridge))
        if lifecycle and lifecycle["phase"] == "consent":
            raise AccessError("sign-in-in-progress")
        if lifecycle and lifecycle["phase"] == "failed":
            # A sign-in that did not connect leaves Portal nothing to finish.
            lifecycle = None
        record, held = _record(), custody.holds_sign_in(PATHS)
        try:
            if held and record and "google" in record["connectors"]:
                # The broker follows the record first: a gateway still on the old tools is refused, never obeyed.
                record = _follow_mode(bridge, record)
                target = gateway_target(record, custody_held=True)
                _install_broker_env(record, target)
                custody.watch_direct(PATHS, RUN, enabled=target["calendarDirect"])
            outcome = _change_gateway(bridge, gateway_target(record, custody_held=held),
                                      state.connection_answers(record, google_custody=held))
        except (AccessError, custody.CustodyError) as error:
            if lifecycle:
                _write_lifecycle(bridge, dict(lifecycle, error=error.code))
            raise
        if outcome == "rolled-back":
            if lifecycle:
                _write_lifecycle(bridge, dict(lifecycle, error="gateway-change-rolled-back"))
            raise AccessError("gateway-change-rolled-back")
        _clear_lifecycle(bridge)
        return {"outcome": outcome}


OPERATIONS = {"connection-begin": begin, "connection-complete": complete, "connection-activate": activate,
              "connection-cancel": cancel, "connection-disconnect": disconnect, "connection-sync": sync,
              "connection-proposals": proposals, "connection-approve": approve, "connection-decline": decline,
              "connection-reconcile": reconcile, "connection-activity": changes_made, "connection-undo": undo,
              "connection-drafts": drafts_waiting}


def control(bridge, operation: str, request: Any) -> dict[str, Any]:
    handler = OPERATIONS.get(operation)
    if handler is None:
        raise AccessError("invalid-connection-operation")
    try:
        return handler(bridge, request)
    except (custody.CustodyError, approvals.ApprovalError) as error:
        raise AccessError(error.code) from None
