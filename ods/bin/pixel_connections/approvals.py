"""Calendar changes Portal suggests, made only after the owner approves them in the dashboard.

This is Pixel's operator path (scripts/show-source-action.sh, approve-source-action.sh
and reconcile-source-action.sh) run by root for the signed-in dashboard. Root
freezes the proposal Portal wrote into a snapshot that only the Source Broker
owns, the owner approves that snapshot's exact SHA-256, and the broker's action
unit applies it. A result counts only when it is bound to that hash. Nothing
here talks to Google, and nothing here imports a Linux-only module, so status
can count waiting changes on any host.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Callable, NamedTuple

PROPOSAL_ID = re.compile(r"^calendar-[0-9]{13}-[a-f0-9]{8}$")
PROPOSAL_FILE = re.compile(r"^(calendar-[0-9]{13}-[a-f0-9]{8})\.json$")
STALE_SNAPSHOT = re.compile(r"^\.calendar-[0-9]{13}-[a-f0-9]{8}\.snapshot\.")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
OPERATOR = "pending-operator-approval"
DIRECT = "pending-bounded-direct"
BINDING = "sha256-protected-proposal-snapshot"
# The broker's own limits and patterns (vendor/pixel/deploy/source-broker/broker.py).
MAX_PROPOSAL_BYTES = 256 * 1024
MAX_RESULT_BYTES = 256 * 1024
MAX_PROJECTION_BYTES = 32 * 1024 * 1024
BOUNDARY = "This file is a proposal only. No Calendar mutation has occurred."
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ETAG = re.compile(r'^(?:W/)?"(?:[^"\\\r\n]|\\.){1,200}"$')
EMAIL = re.compile(r"^[^\s@<>\x00-\x1f\x7f]+@[^\s@<>\x00-\x1f\x7f]+$")
PLACEHOLDER = re.compile(r"^\[(?:quarantined|withheld)[^\]]*\]$", re.I)
TIME_ZONE = re.compile(r"^[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*$")
COMMON = frozenset({"summary", "start", "end", "timeZone", "allDay", "description", "location", "attendees",
                    "sendUpdates"})
ALLOWED = {"create": COMMON, "update": COMMON | {"eventId", "expectedEtag"},
           "delete": frozenset({"eventId", "expectedEtag", "sendUpdates"})}
# How a bound result settles a change; the last two come from Pixel's legacy update recovery.
OUTCOMES = {"applied": "applied", "rejected": "rejected", "desired-state-observed": "applied",
            "not-applied": "not-applied"}
SETTLED = frozenset(OUTCOMES.values())
# Only the newest proposals are read, so a gateway cannot make a listing unbounded.
CONSIDERED = 50
LISTED = 20
# The reply crosses three 64 KiB hops. One change may use most of it; later ones wait their turn.
REPLY_BUDGET = 56 * 1024
ENTRY_BUDGET = 48 * 1024

Run = Callable[[list, int], int]


class ApprovalError(RuntimeError):
    """A calendar change cannot be shown or made; ``code`` is a fixed, content-free reason."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Ids(NamedTuple):
    """Who owns what: the gateway owner writes proposals, the broker owns snapshots and results."""

    reader_uid: int
    reader_gid: int
    broker_uid: int


class Dirs(NamedTuple):
    """The broker's proposal, result and summary directories; custody.Paths names them the same."""

    proposals: Path
    results: Path
    projection: Path

    @classmethod
    def under(cls, state: Path) -> "Dirs":
        return cls(state / "proposals", state / "results", state / "projection")


# ---------------------------------------------------------------- files

def _read(path: Path, *, owner: int | None, limit: int) -> bytes | None:
    """One bounded, single-link regular file nobody else can write; None when it does not exist."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError:
        # A symbolic link, or anything else that cannot be opened plainly.
        raise ApprovalError("calendar-change-unsafe") from None
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022
                or owner is not None and info.st_uid != owner or info.st_size > limit):
            raise ApprovalError("calendar-change-unsafe")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ApprovalError("calendar-change-unsafe")
    return raw


def _json(raw: bytes) -> Any:
    """Strict JSON as the broker reads it: no repeated keys and no NaN or Infinity."""
    def unique(pairs: list) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("repeated key")
            value[key] = item
        return value

    def constant(name: str) -> None:
        raise ValueError(name)

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=constant)


def _status(raw: bytes) -> str | None:
    try:
        value = _json(raw)
    except (UnicodeError, ValueError, RecursionError):
        return None
    status = value.get("status") if type(value) is dict else None
    return status if type(status) is str else None


# ---------------------------------------------------------------- the broker's proposal rules

def _text(fields: dict[str, Any], key: str, limit: int, *, required: bool = False) -> str | None:
    value = fields.get(key)
    if value is None and not required:
        return None
    if type(value) is not str or required and not value.strip() or len(value) > limit or CONTROL.search(value):
        raise ValueError(key)
    return value


def _times(fields: dict[str, Any], *, required: bool) -> dict[str, Any]:
    start = _text(fields, "start", 64, required=required)
    end = _text(fields, "end", 64, required=required)
    if bool(start) != bool(end):
        raise ValueError("start")
    all_day = fields.get("allDay", False)
    if type(all_day) is not bool:
        raise ValueError("allDay")
    zone = _text(fields, "timeZone", 100)
    if zone and not TIME_ZONE.fullmatch(zone):
        raise ValueError("timeZone")
    if not start or not end:
        return {}
    if all_day:
        first: Any = datetime.strptime(start, "%Y-%m-%d").date()
        last: Any = datetime.strptime(end, "%Y-%m-%d").date()
    else:
        first = datetime.fromisoformat(start.replace("Z", "+00:00"))
        last = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if first.tzinfo is None or last.tzinfo is None:
            raise ValueError("start")
    if last <= first:
        raise ValueError("end")
    return {"start": start, "end": end, "allDay": all_day, "timeZone": zone}


def _attendees(fields: dict[str, Any]) -> list[str] | None:
    attendees = fields.get("attendees")
    if attendees is None:
        return None
    if type(attendees) is not list or len(attendees) > 100:
        raise ValueError("attendees")
    seen: set[str] = set()
    emails = []
    for item in attendees:
        if type(item) is not dict or set(item) != {"email"}:
            raise ValueError("attendees")
        email = _text(item, "email", 320, required=True)
        if email is None or not EMAIL.fullmatch(email) or email.casefold() in seen:
            raise ValueError("attendees")
        seen.add(email.casefold())
        emails.append(email)
    return emails


def _placeholder(value: Any) -> bool:
    if type(value) is str:
        return PLACEHOLDER.fullmatch(value.strip()) is not None
    if type(value) is list:
        return any(_placeholder(item) for item in value)
    if type(value) is dict:
        return any(_placeholder(item) for item in value.values())
    return False


def validate(value: Any, proposal_id: str, *, status: str = OPERATOR) -> tuple[str, dict[str, Any]]:
    """The broker's validate_proposal for a proposal in ``status``; ValueError where the broker refuses.

    The broker also refuses event text that looks like a vendor credential. That
    rule stays in the broker, so such a change fails when approved and is never made.
    """
    if (type(value) is not dict or set(value) != {"schemaVersion", "proposalId", "source", "action", "status",
                                                  "createdAt", "values", "boundary"}
            or value["schemaVersion"] != 1 or value["proposalId"] != proposal_id
            or value["source"] != "pixel-owner-conversation" or value["status"] != status
            or value["boundary"] != BOUNDARY or type(value["createdAt"]) is not str
            or type(value["action"]) is not str or value["action"] not in ALLOWED
            or type(value["values"]) is not dict):
        raise ValueError("proposal")
    action, fields = value["action"], value["values"]
    if not set(fields) <= ALLOWED[action]:
        raise ValueError("values")
    send = fields.get("sendUpdates", "none")
    if send not in ("none", "all"):
        raise ValueError("sendUpdates")
    cleaned: dict[str, Any] = {"sendUpdates": send}
    if action != "create":
        cleaned["eventId"] = _text(fields, "eventId", 1024, required=True)
        etag = _text(fields, "expectedEtag", 256, required=True)
        if etag is None or not ETAG.fullmatch(etag):
            raise ValueError("expectedEtag")
        cleaned["expectedEtag"] = etag
    if action != "delete":
        for key, limit in (("summary", 1000), ("description", 20_000), ("location", 1000)):
            text = _text(fields, key, limit, required=key == "summary" and action == "create")
            if text is not None:
                cleaned[key] = text
        attendees = _attendees(fields)
        if attendees is not None:
            cleaned["attendees"] = attendees
        cleaned.update(_times(fields, required=action == "create"))
        if action == "update" and set(cleaned) <= {"eventId", "expectedEtag", "sendUpdates"}:
            raise ValueError("values")
    if _placeholder(cleaned):
        raise ValueError("values")
    return action, cleaned


# ---------------------------------------------------------------- snapshots and results

def _approved(paths, proposal_id: str) -> Path:
    return paths.results / f"{proposal_id}.approved.json"


def _protect(paths, ids: Ids, proposal_id: str, raw: bytes) -> None:
    """Freeze these exact bytes where only the broker can change them; the first snapshot stays."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{proposal_id}.snapshot.", dir=paths.results)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fchown(handle.fileno(), ids.broker_uid, ids.reader_gid)
            os.fchmod(handle.fileno(), 0o640)
            os.fsync(handle.fileno())
        try:
            os.link(temporary, _approved(paths, proposal_id))
        except FileExistsError:
            pass  # An earlier snapshot of this proposal is the one shown and approved.
    finally:
        os.unlink(temporary)


def _frozen(paths, ids: Ids, proposal_id: str) -> bytes | None:
    """The protected snapshot of one proposal, made on first sight like Pixel's source-show.

    None for a change Pixel makes by itself (bounded direct) and for one that is
    gone. A proposal that is not for owner approval comes back unprotected, so it
    is shown as unreadable and can only be dismissed.
    """
    raw = _read(_approved(paths, proposal_id), owner=ids.broker_uid, limit=MAX_PROPOSAL_BYTES)
    fresh = raw is None
    if raw is None:
        raw = _read(paths.proposals / f"{proposal_id}.json", owner=ids.reader_uid, limit=MAX_PROPOSAL_BYTES)
        if raw is None:
            return None
    status = _status(raw)
    if status == DIRECT:
        return None
    if fresh and status == OPERATOR:
        _protect(paths, ids, proposal_id, raw)
        return _read(_approved(paths, proposal_id), owner=ids.broker_uid, limit=MAX_PROPOSAL_BYTES)
    return raw


def _result(paths, proposal_id: str, owner: int | None) -> dict[str, Any] | None:
    raw = _read(paths.results / f"{proposal_id}.json", owner=owner, limit=MAX_RESULT_BYTES)
    if raw is None:
        return None
    try:
        value = _json(raw)
    except (UnicodeError, ValueError, RecursionError):
        raise ApprovalError("calendar-change-result-invalid") from None
    if type(value) is not dict:
        raise ApprovalError("calendar-change-result-invalid")
    return value


def _state(paths, proposal_id: str, sha256: str, owner: int) -> str:
    """waiting, unconfirmed, or how a result bound to exactly this snapshot settled it."""
    result = _result(paths, proposal_id, owner)
    if result is None:
        # The broker claims a change just before calling Google and removes the
        # claim only after writing its result, so a claim alone means Google may have it.
        return "unconfirmed" if os.path.lexists(paths.results / f"{proposal_id}.processing.json") else "waiting"
    status = result.get("status")
    if (result.get("proposalId") != proposal_id or result.get("proposalSha256") != sha256
            or result.get("approvalBinding") != BINDING or type(status) is not str or status not in OUTCOMES):
        raise ApprovalError("calendar-change-result-invalid")
    return OUTCOMES[status]


def _forget(paths, proposal_id: str) -> None:
    """Take a settled change off the owner's list; the snapshot and result stay as evidence."""
    (paths.proposals / f"{proposal_id}.json").unlink(missing_ok=True)


# ---------------------------------------------------------------- what the owner sees

def _change(fields: dict[str, Any]) -> dict[str, Any]:
    """Every value the broker would send to Google. None means the event keeps what it has."""
    return {"summary": fields.get("summary"), "start": fields.get("start"), "end": fields.get("end"),
            "allDay": fields.get("allDay"), "timeZone": fields.get("timeZone"), "location": fields.get("location"),
            "description": fields.get("description"), "attendees": fields.get("attendees"),
            "sendUpdates": fields["sendUpdates"]}


def _when(value: Any) -> tuple[str | None, bool]:
    if type(value) is dict and type(value.get("dateTime")) is str:
        return CONTROL.sub("", value["dateTime"])[:64], False
    if type(value) is dict and type(value.get("date")) is str:
        return CONTROL.sub("", value["date"])[:64], True
    return None, False


def projected(paths, owner: int, wanted: set[str]) -> tuple[dict[str, dict[str, Any]], str | None]:
    """The broker's calendar summary records of these events, and when that summary was made.

    An unreadable summary names no event.
    """
    if not wanted:
        return {}, None
    try:
        raw = _read(paths.projection / "calendar.json", owner=owner, limit=MAX_PROJECTION_BYTES)
        value = _json(raw) if raw is not None else None
    except (ApprovalError, UnicodeError, ValueError, RecursionError):
        return {}, None
    records = value.get("records") if type(value) is dict else None
    found: dict[str, dict[str, Any]] = {}
    for record in records if type(records) is list else []:
        event_id = record.get("id") if type(record) is dict else None
        if type(event_id) is str and event_id in wanted and event_id not in found:
            found[event_id] = record
    generated = value.get("generatedAt") if type(value) is dict else None
    return found, generated if type(generated) is str else None


def event_view(record: dict[str, Any]) -> dict[str, Any]:
    """An event's title and time as the owner reads them."""
    start, all_day = _when(record.get("start"))
    end, _end_all_day = _when(record.get("end"))
    title = record.get("title")
    return {"title": CONTROL.sub("", title)[:1000] if type(title) is str else None,
            "start": start, "end": end, "allDay": all_day}


def _events(paths, owner: int, wanted: set[str]) -> dict[str, dict[str, Any]]:
    """The current title and time of the events these changes touch; for display only."""
    return {event_id: event_view(record) for event_id, record in projected(paths, owner, wanted)[0].items()}


def _unreadable(proposal_id: str) -> dict[str, Any]:
    return {"proposalId": proposal_id, "sha256": None, "state": "invalid", "action": None, "createdAt": None,
            "change": None, "event": None, "tooLarge": False}


def _entry(paths, ids: Ids, proposal_id: str) -> tuple[dict[str, Any], str | None] | None:
    """One change as the owner sees it, and the event it touches; None when it is not the owner's to approve."""
    try:
        raw = _frozen(paths, ids, proposal_id)
    except ApprovalError:
        return _unreadable(proposal_id), None
    if raw is None:
        return None
    try:
        value = _json(raw)
        action, fields = validate(value, proposal_id)
    except (UnicodeError, ValueError, RecursionError):
        return _unreadable(proposal_id), None
    sha256 = hashlib.sha256(raw).hexdigest()
    try:
        state = _state(paths, proposal_id, sha256, ids.broker_uid)
    except ApprovalError:
        return _unreadable(proposal_id), None
    created = value["createdAt"] if len(value["createdAt"]) <= 64 and not CONTROL.search(value["createdAt"]) else None
    return ({"proposalId": proposal_id, "sha256": sha256, "state": state, "action": action, "createdAt": created,
             "change": _change(fields), "event": None, "tooLarge": False}, fields.get("eventId"))


def _names(paths) -> list[str]:
    """Proposal IDs, newest first; none when the broker has no proposal directory to list."""
    if paths.proposals.is_symlink() or not paths.proposals.is_dir():
        return []
    try:
        with os.scandir(paths.proposals) as entries:
            names = [match.group(1) for match in (PROPOSAL_FILE.fullmatch(entry.name) for entry in entries) if match]
    except OSError:
        return []  # Replaced while listing; the next refresh sees what is there.
    return sorted(names, reverse=True)


def _clear_stale_snapshots(paths) -> None:
    """A snapshot interrupted before its link was removed leaves a temporary file; it is never used."""
    if paths.results.is_symlink() or not paths.results.is_dir():
        return
    for entry in os.scandir(paths.results):
        if STALE_SNAPSHOT.match(entry.name) and entry.is_file(follow_symlinks=False):
            os.unlink(entry.path)


def listing(paths, ids: Ids) -> dict[str, Any]:
    """The changes that wait for the owner, oldest first, each with the hash that approves it."""
    names = _names(paths)
    if not names:
        return {"proposals": [], "more": 0}
    _clear_stale_snapshots(paths)
    built = [entry for entry in (_entry(paths, ids, name) for name in reversed(names[:CONSIDERED])) if entry]
    events = _events(paths, ids.broker_uid, {event_id for _entry_value, event_id in built if event_id})
    shown: list[dict[str, Any]] = []
    more, used = max(0, len(names) - CONSIDERED), 0
    for entry, event_id in built:
        if event_id:
            entry["event"] = events.get(event_id)
        size = len(json.dumps(entry))
        if size > ENTRY_BUDGET:
            # It cannot be shown whole, so it cannot be approved here; it can still be declined.
            entry = dict(entry, sha256=None, change=None, event=None, tooLarge=True)
            size = len(json.dumps(entry))
        if len(shown) >= LISTED or used + size > REPLY_BUDGET:
            more += 1
            continue
        shown.append(entry)
        used += size
    return {"proposals": shown, "more": more}


def pending_count(paths) -> int:
    """How many changes wait for the owner. Read-only, for status; ownership is checked when they are shown."""
    count = 0
    for name in _names(paths)[:CONSIDERED]:
        try:
            if _result(paths, name, None) is not None:
                continue
            raw = _read(_approved(paths, name), owner=None, limit=MAX_PROPOSAL_BYTES)
            if raw is None:
                raw = _read(paths.proposals / f"{name}.json", owner=None, limit=MAX_PROPOSAL_BYTES)
        except ApprovalError:
            raw = b""  # Unreadable: the owner still has to dismiss it.
        if raw is not None and _status(raw) != DIRECT:
            count += 1
    return count


def discard_unsent_direct(paths) -> int:
    """Remove private changes queued while they could not run, and return how many.

    Portal was told each was not made, so none may happen late. Call this only
    before the broker may run queued changes again.
    """
    removed = 0
    for name in _names(paths):
        if (os.path.lexists(paths.results / f"{name}.json")
                or os.path.lexists(paths.results / f"{name}.processing.json")):
            continue
        try:
            raw = _read(paths.proposals / f"{name}.json", owner=None, limit=MAX_PROPOSAL_BYTES)
        except ApprovalError:
            continue  # Not readable as a proposal; the owner's list shows it and can decline it.
        if raw is not None and _status(raw) == DIRECT:
            (paths.proposals / f"{name}.json").unlink(missing_ok=True)
            removed += 1
    return removed


# ---------------------------------------------------------------- the owner's actions

def _snapshot_for(paths, ids: Ids, proposal_id: str, sha256: str | None) -> tuple[bytes, str]:
    raw = _read(_approved(paths, proposal_id), owner=ids.broker_uid, limit=MAX_PROPOSAL_BYTES)
    if raw is None:
        raise ApprovalError("calendar-change-unknown")
    digest = hashlib.sha256(raw).hexdigest()
    if sha256 is not None and digest != sha256:
        raise ApprovalError("calendar-change-mismatch")
    return raw, digest


def approve(paths, run: Run, ids: Ids, request: dict[str, Any]) -> dict[str, Any]:
    """Make exactly the change the owner approved, once.

    Approving again after a lost reply reports the first outcome without calling Google.
    """
    proposal_id = request["proposalId"]
    raw, sha256 = _snapshot_for(paths, ids, proposal_id, request["sha256"])
    state = _state(paths, proposal_id, sha256, ids.broker_uid)
    if state == "unconfirmed":
        raise ApprovalError("calendar-change-unconfirmed")
    if state == "waiting":
        try:
            validate(_json(raw), proposal_id)
        except (UnicodeError, ValueError, RecursionError):
            raise ApprovalError("calendar-change-invalid") from None
        # The unit fails when the broker refuses; only a bound result says what happened.
        run(["systemctl", "start", f"pixel-source-action@{proposal_id}.service"], 120)
        state = _state(paths, proposal_id, sha256, ids.broker_uid)
        if state == "waiting":
            raise ApprovalError("calendar-change-not-made")
    if state in SETTLED:
        _forget(paths, proposal_id)
    return {"proposalId": proposal_id, "outcome": state}


def reconcile(paths, run: Run, ids: Ids, request: dict[str, Any]) -> dict[str, Any]:
    """Ask Google whether an unconfirmed new event exists; nothing is ever sent twice.

    Pixel can check only a new event, whose ID it fixed before calling Google.
    """
    proposal_id = request["proposalId"]
    raw, sha256 = _snapshot_for(paths, ids, proposal_id, None)
    state = _state(paths, proposal_id, sha256, ids.broker_uid)
    if state == "unconfirmed":
        if _status(raw) != OPERATOR or _json(raw).get("action") != "create":
            raise ApprovalError("calendar-change-not-checkable")
        # Exit status 3 means Google cannot show the event yet; the result decides either way.
        run(["systemctl", "start", f"pixel-source-reconcile@{proposal_id}.service"], 120)
        state = _state(paths, proposal_id, sha256, ids.broker_uid)
    if state in SETTLED:
        _forget(paths, proposal_id)
    return {"proposalId": proposal_id, "outcome": state}


def decline(paths, request: dict[str, Any]) -> dict[str, Any]:
    """Take a change off the owner's list. Evidence of anything sent to Google stays."""
    proposal_id = request["proposalId"]
    attempted = (os.path.lexists(paths.results / f"{proposal_id}.json")
                 or os.path.lexists(paths.results / f"{proposal_id}.processing.json"))
    (paths.proposals / f"{proposal_id}.json").unlink(missing_ok=True)
    if not attempted:
        # Nothing reached Google, so the frozen copy goes too.
        _approved(paths, proposal_id).unlink(missing_ok=True)
    return {"proposalId": proposal_id, "outcome": "declined"}
