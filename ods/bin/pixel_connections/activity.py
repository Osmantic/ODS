"""Calendar changes Portal made by itself, and undoing one at the owner's request.

Portal's private changes (events it added without guests, events it moved to a
new time) are listed for a week from the Source Broker's own results. Undo is a
new change the owner asks for with one click. Root builds it only from what the
broker recorded: the event it created, or the times it moved the event from. It
checks the event is still exactly as Portal left it, then makes the reversal
through the same hash-bound action unit as an approval, with the event's
current ETag, so Google refuses it if the event changed since. A ledger keeps
each reversal, so a change is never undone twice. Nothing here imports a
Linux-only module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from .approvals import (BOUNDARY, CONTROL, DIRECT, ETAG, MAX_PROPOSAL_BYTES, OPERATOR, PROPOSAL_ID, ApprovalError,
                        Ids, Run, _approved, _json, _protect, _read, _result, _state, _when, event_view, projected,
                        validate)

RESULT_FILE = re.compile(r"^(calendar-([0-9]{13})-[a-f0-9]{8})\.json$")
DIRECT_BINDING = "bounded-direct-policy"
WINDOW = timedelta(days=7)
LISTED = 20
LEDGER_KEEP = 200
# How an undo ended, as the owner reads it beside the change it reversed.
UNDO_STATES = {"applied": "done", "rejected": "refused", "not-applied": "refused", "unconfirmed": "unconfirmed"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _instant(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


# ---------------------------------------------------------------- what Portal did

def _recent(paths, now: datetime) -> list[str]:
    """Results of the last week, newest first, by the moment each proposal was made."""
    cutoff = int((now - WINDOW).timestamp() * 1000)
    if paths.results.is_symlink() or not paths.results.is_dir():
        return []
    try:
        with os.scandir(paths.results) as entries:
            found = [(int(match.group(2)), match.group(1))
                     for match in (RESULT_FILE.fullmatch(entry.name) for entry in entries) if match]
    except OSError:
        return []  # Replaced while listing; the next look sees what is there.
    return [name for stamp, name in sorted(found, reverse=True) if stamp >= cutoff]


def _made_by_portal(paths, ids: Ids, name: str) -> tuple[dict[str, Any], str, dict[str, Any]] | None:
    """The broker's result, action and values of one change Portal made by itself; None for anything else."""
    try:
        result = _result(paths, name, ids.broker_uid)
        raw = _read(_approved(paths, name), owner=ids.broker_uid, limit=MAX_PROPOSAL_BYTES)
        if result is None or raw is None:
            return None
        action, fields = validate(_json(raw), name, status=DIRECT)
    except (ApprovalError, UnicodeError, ValueError, RecursionError):
        return None
    affected = result.get("affectedEventId")
    if (action not in ("create", "update") or result.get("proposalId") != name
            or result.get("approvalBinding") != DIRECT_BINDING or result.get("status") != "applied"
            or result.get("proposalSha256") != hashlib.sha256(raw).hexdigest()
            or type(affected) is not str or not affected or CONTROL.search(affected)):
        return None
    return result, action, fields


def _same(left: str | None, right: str | None, all_day: bool) -> bool:
    if not left or not right:
        return False
    if all_day:
        return left[:10] == right[:10]
    first, second = _instant(left), _instant(right)
    return first is not None and second is not None and first == second


def _as_left(action: str, fields: dict[str, Any], event: dict[str, Any] | None) -> bool:
    """Whether the event is still exactly as Portal's change left it, so undoing reverses only that change."""
    if event is None:
        return False
    view = event_view(event)
    all_day = bool(fields.get("allDay"))
    if view["allDay"] != all_day or not _same(view["start"], fields["start"], all_day) \
            or not _same(view["end"], fields["end"], all_day):
        return False
    # Deleting an event the owner has since shared would reach the people on it.
    return action != "create" or not event.get("attendees")


# ---------------------------------------------------------------- the ledger

def _ledger(path: Path) -> dict[str, str]:
    """Which reversal undid each change. An unreadable ledger is empty: the calendar check still stops a second undo."""
    try:
        value = _json(path.read_bytes())
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError, RecursionError):
        return {}
    undone = value.get("undone") if type(value) is dict and value.get("schemaVersion") == 1 else None
    if type(undone) is not dict:
        return {}
    return {key: item for key, item in undone.items()
            if type(key) is str and PROPOSAL_ID.fullmatch(key) and type(item) is str and PROPOSAL_ID.fullmatch(item)}


def _save_ledger(path: Path, ledger: dict[str, str]) -> None:
    keep = dict(sorted(ledger.items(), reverse=True)[:LEDGER_KEEP])
    data = (json.dumps({"schemaVersion": 1, "undone": keep}, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".calendar-undo.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _undone(paths, ids: Ids, undo_id: str | None) -> str | None:
    """How a recorded reversal ended; None when there is none or it never reached Google."""
    if undo_id is None:
        return None
    try:
        raw = _read(_approved(paths, undo_id), owner=ids.broker_uid, limit=MAX_PROPOSAL_BYTES)
        state = _state(paths, undo_id, hashlib.sha256(raw).hexdigest(), ids.broker_uid) if raw else "waiting"
    except ApprovalError:
        return "unconfirmed"
    return UNDO_STATES.get(state)


# ---------------------------------------------------------------- the owner's list

def _clean(value: Any, limit: int) -> str | None:
    return CONTROL.sub("", value)[:limit] if type(value) is str else None


def _entry(name: str, made: tuple, undo: str | None, event: dict[str, Any] | None, synced: str | None) -> dict:
    result, action, fields = made
    rollback = result.get("rollback") if action == "update" else None
    previous = None
    if type(rollback) is dict:
        start, all_day = _when(rollback.get("start"))
        end, _end_all_day = _when(rollback.get("end"))
        previous = {"start": start, "end": end, "allDay": all_day}
    applied = _clean(result.get("appliedAt"), 64)
    if undo is None:
        synced_at, applied_at = _instant(synced or ""), _instant(applied or "")
        if synced_at is not None and applied_at is not None and synced_at < applied_at:
            undo = "syncing"  # The calendar summary predates the change; the next sync, within a minute, shows it.
        elif event is None:
            undo = "gone"
        else:
            undo = "available" if _as_left(action, fields, event) else "changed"
    title = fields.get("summary") if action == "create" else event_view(event)["title"] if event else None
    return {"proposalId": name, "action": action, "title": _clean(title, 1000), "start": fields["start"],
            "end": fields["end"], "allDay": bool(fields.get("allDay")), "previous": previous, "appliedAt": applied,
            "undo": undo}


def listing(paths, ids: Ids, ledger_path: Path, now: datetime | None = None) -> dict[str, Any]:
    """The private changes Portal made this week, newest first, each with whether it can be undone."""
    made = [(name, found) for name, found in ((name, _made_by_portal(paths, ids, name))
                                              for name in _recent(paths, now or _now())) if found]
    shown = made[:LISTED]
    events, synced = projected(paths, ids.broker_uid, {found[0]["affectedEventId"] for _name, found in shown})
    ledger = _ledger(ledger_path)
    return {"changes": [_entry(name, found, _undone(paths, ids, ledger.get(name)),
                               events.get(found[0]["affectedEventId"]), synced) for name, found in shown],
            "more": len(made) - len(shown)}


# ---------------------------------------------------------------- undo

def _reversal(action: str, result: dict[str, Any], event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """The change that reverses Portal's, from the broker's own record of it; None when there is none."""
    etag = event.get("etag")
    if type(etag) is not str or not ETAG.fullmatch(etag):
        return None
    base = {"eventId": result["affectedEventId"], "expectedEtag": etag, "sendUpdates": "none"}
    if action == "create":
        return "delete", base
    rollback = result.get("rollback")
    if type(rollback) is not dict or rollback.get("kind") != "time-only" or type(rollback.get("start")) is not dict:
        return None
    start, all_day = _when(rollback.get("start"))
    end, end_all_day = _when(rollback.get("end"))
    if not start or not end or all_day != end_all_day:
        return None
    values = dict(base, start=start, end=end, allDay=all_day)
    zone = rollback["start"].get("timeZone")
    if not all_day and type(zone) is str and zone:
        values["timeZone"] = zone
    return "update", values


def _proposal(undo_id: str, action: str, values: dict[str, Any], now: datetime) -> bytes:
    """The reversal, in the exact form the broker approves; the owner's Undo is its approval."""
    return (json.dumps({"schemaVersion": 1, "proposalId": undo_id, "source": "pixel-owner-conversation",
                        "action": action, "status": OPERATOR,
                        "createdAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "values": values,
                        "boundary": BOUNDARY}, indent=2) + "\n").encode("utf-8")


def undo(paths, run: Run, ids: Ids, ledger_path: Path, request: dict[str, Any], now: datetime | None = None) -> dict:
    """Reverse one change Portal made by itself, once."""
    name = request["proposalId"]
    found = _made_by_portal(paths, ids, name)
    if found is None:
        raise ApprovalError("calendar-change-unknown")
    result, action, fields = found
    ledger = _ledger(ledger_path)
    undo_id = ledger.get(name)
    raw = _read(_approved(paths, undo_id), owner=ids.broker_uid, limit=MAX_PROPOSAL_BYTES) if undo_id else None
    if undo_id and raw is not None:
        state = _state(paths, undo_id, hashlib.sha256(raw).hexdigest(), ids.broker_uid)
        if state != "waiting":
            return {"proposalId": name, "outcome": state}
        # The first attempt never reached Google; it runs again exactly as built.
    else:
        event = projected(paths, ids.broker_uid, {result["affectedEventId"]})[0].get(result["affectedEventId"])
        reversal = _reversal(action, result, event) if _as_left(action, fields, event) else None
        if reversal is None:
            raise ApprovalError("calendar-undo-unavailable")
        moment = now or _now()
        undo_id = f"calendar-{int(moment.timestamp() * 1000)}-{os.urandom(4).hex()}"
        raw = _proposal(undo_id, *reversal, moment)
        try:
            validate(_json(raw), undo_id)  # The broker's own rules, before anything is kept.
        except ValueError:
            raise ApprovalError("calendar-undo-unavailable") from None
        ledger[name] = undo_id
        _save_ledger(ledger_path, ledger)
        _protect(paths, ids, undo_id, raw)
    run(["systemctl", "start", f"pixel-source-action@{undo_id}.service"], 120)
    state = _state(paths, undo_id, hashlib.sha256(raw).hexdigest(), ids.broker_uid)
    if state == "waiting":
        raise ApprovalError("calendar-change-not-made")
    return {"proposalId": name, "outcome": state}
