"""Portal's task contract and projection of the native OpenClaw scheduler.

This module does not implement a scheduler. The owner adapter calls only the
fixed native cron methods, and public replies contain only Pixel agent tasks.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_REQUEST = 16384
MAX_RESPONSE = 524288
PAGE_SIZE = 10
TASK_BYTES = 4000
AGENT = "pixel"
OPENCLAW_VERSION = "2026.6.33"
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")
_REVISION = re.compile(r"[a-f0-9]{64}\Z")


class ScheduleError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _text(value, maximum, *, empty=False):
    if (type(value) is not str or (not value.strip() and not empty)
            or len(value.encode("utf-8")) > maximum or "\x00" in value):
        raise ScheduleError("invalid-task")
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ScheduleError("invalid-task")
    return value


def identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ScheduleError("invalid-task")
    return value


def request_id(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise ScheduleError("invalid-task") from None
    return value


def schedule(value):
    """Compile familiar frequency/time inputs to native at/cron definitions."""
    if type(value) is not dict:
        raise ScheduleError("invalid-task")
    if set(value) == {"kind", "at"} and value["kind"] == "at":
        raw = _text(value["at"], 40)
        try:
            instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError()
            result = instant.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except ValueError:
            raise ScheduleError("invalid-task-time") from None
        return {"kind": "at", "at": result}
    if set(value) != {"kind", "frequency", "time", "timeZone", "weekday"} or value["kind"] != "recurring":
        raise ScheduleError("invalid-task")
    frequency = value["frequency"]
    if frequency not in ("daily", "weekdays", "weekly") or type(frequency) is not str:
        raise ScheduleError("invalid-task")
    clock = value["time"]
    if type(clock) is not str or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", clock):
        raise ScheduleError("invalid-task-time")
    zone = _text(value["timeZone"], 100)
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError("invalid-task-timezone") from None
    day = value["weekday"]
    if frequency == "weekly":
        _integer(day, 0, 6)
    elif day is not None:
        raise ScheduleError("invalid-task")
    hour, minute = map(int, clock.split(":"))
    days = str(day) if frequency == "weekly" else "1-5" if frequency == "weekdays" else "*"
    return {"kind": "cron", "expr": f"{minute} {hour} * * {days}", "tz": zone, "staggerMs": 0}


def validate_request(value):
    if type(value) is not dict or type(value.get("action")) is not str:
        raise ScheduleError("invalid-task")
    action = value["action"]
    keys = {"list": {"action", "offset"}, "archived": {"action", "offset"},
            "create": {"action", "requestId", "name", "task", "schedule"},
            "set-enabled": {"action", "id", "revision", "enabled"}, "remove": {"action", "id", "revision"},
            "runs": {"action", "id", "offset"}, "check-create": {"action", "requestId"},
            "dismiss-create": {"action", "requestId"}}
    if action not in keys or set(value) != keys[action]:
        raise ScheduleError("invalid-task")
    if action in ("list", "archived", "runs"):
        _integer(value["offset"], 0, 1_000_000)
    if "id" in value:
        identifier(value["id"])
    if "requestId" in value:
        request_id(value["requestId"])
    if "revision" in value:
        if type(value["revision"]) is not str or not _REVISION.fullmatch(value["revision"]):
            raise ScheduleError("invalid-task")
    if action == "set-enabled" and type(value["enabled"]) is not bool:
        raise ScheduleError("invalid-task")
    if action == "create":
        _text(value["name"], 160)
        if any(ord(char) < 32 for char in value["name"]):
            raise ScheduleError("invalid-task")
        _text(value["task"], TASK_BYTES)
        schedule(value["schedule"])
    if len(json.dumps(value).encode("utf-8")) > MAX_REQUEST:
        raise ScheduleError("task-too-large")
    return value


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def native_task(request):
    """No command payload, outbound delivery, model override or arbitrary agent."""
    validate_request(request)
    if request["action"] != "create":
        raise ScheduleError("invalid-task")
    return {"name": request["name"].strip(), "agentId": AGENT, "enabled": True,
            "description": f"Created in Portal (request {request['requestId']}).",
            "schedule": schedule(request["schedule"]), "sessionTarget": "isolated", "wakeMode": "now",
            "payload": {"kind": "agentTurn", "message": request["task"]},
            "delivery": {"mode": "none"}, "deleteAfterRun": False}


def owned_task(value):
    return (type(value) is dict and value.get("agentId") == AGENT and type(value.get("payload")) is dict
            and value["payload"].get("kind") == "agentTurn")


def task_revision(value):
    return checksum({key: value.get(key) for key in (
        "id", "agentId", "name", "payload", "schedule", "delivery", "enabled", "updatedAtMs")})


def task_fingerprint(value):
    """Bind creation to its content; native timestamps and enabled state may change as it runs."""
    if not owned_task(value):
        raise ScheduleError("invalid-scheduler-response")
    native_schedule = public_schedule(value.get("schedule"))
    if native_schedule["kind"] == "at":
        native_schedule = schedule(native_schedule)
    elif native_schedule["kind"] == "cron":
        native_schedule["staggerMs"] = value["schedule"].get("staggerMs", 0)
    return checksum({"schedule": native_schedule, **{key: value.get(key) for key in (
        "agentId", "name", "payload", "sessionTarget", "delivery", "deleteAfterRun")}})


def _timestamp(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def project_task(value):
    if not owned_task(value):
        raise ScheduleError("task-not-found")
    state = value.get("state") if type(value.get("state")) is dict else {}
    status = state.get("lastRunStatus", state.get("lastStatus"))
    if type(value.get("enabled")) is not bool:
        raise ScheduleError("invalid-scheduler-response")
    return {"id": identifier(value.get("id")), "name": _text(value.get("name"), 1024),
            "task": _text(value["payload"].get("message"), 65536), "revision": task_revision(value),
            "enabled": value["enabled"], "schedule": public_schedule(value.get("schedule")),
            "createdAt": _timestamp(value.get("createdAtMs")), "updatedAt": _timestamp(value.get("updatedAtMs")),
            "nextRunAt": _timestamp(state.get("nextRunAtMs")), "lastRunAt": _timestamp(state.get("lastRunAtMs")),
            "runningSince": _timestamp(state.get("runningAtMs")),
            "lastStatus": status if status in ("ok", "error", "skipped") else None,
            "externalDelivery": type(value.get("delivery")) is dict and value["delivery"].get("mode") in ("announce", "webhook"),
            "removed": False, "removalPending": False}


def public_schedule(value):
    if type(value) is not dict:
        raise ScheduleError("invalid-scheduler-response")
    kind = value.get("kind")
    if type(kind) is not str:
        raise ScheduleError("invalid-scheduler-response")
    fields = {"at": ("at",), "cron": ("expr", "tz"), "every": ("everyMs", "anchorMs")}.get(kind)
    if fields is None:
        raise ScheduleError("invalid-scheduler-response")
    result = {"kind": kind}
    for key in fields:
        item = value.get(key)
        if item is None:
            if key in ("at", "expr", "everyMs"):
                raise ScheduleError("invalid-scheduler-response")
            result[key] = None
        elif key in ("everyMs", "anchorMs"):
            result[key] = _integer(item, 0, 2**53 - 1)
        else:
            result[key] = _text(item, 256)
    return result


def validate_public_task(value):
    keys = {"id", "name", "task", "revision", "enabled", "schedule", "createdAt", "updatedAt", "nextRunAt",
            "lastRunAt", "runningSince", "lastStatus", "externalDelivery", "removed", "removalPending"}
    if type(value) is not dict or set(value) != keys:
        raise ScheduleError("invalid-scheduler-response")
    identifier(value["id"])
    _text(value["name"], 1024)
    _text(value["task"], 65536)
    if type(value["revision"]) is not str or not _REVISION.fullmatch(value["revision"]):
        raise ScheduleError("invalid-scheduler-response")
    for key in ("enabled", "externalDelivery", "removed", "removalPending"):
        if type(value[key]) is not bool:
            raise ScheduleError("invalid-scheduler-response")
    if value["removed"] and value["removalPending"]:
        raise ScheduleError("invalid-scheduler-response")
    for key in ("createdAt", "updatedAt", "nextRunAt", "lastRunAt", "runningSince"):
        if value[key] is not None:
            _integer(value[key], 0, 2**53 - 1)
    if value["lastStatus"] not in (None, "ok", "error", "skipped"):
        raise ScheduleError("invalid-scheduler-response")
    normalized = public_schedule(value["schedule"])
    if normalized != value["schedule"]:
        raise ScheduleError("invalid-scheduler-response")
    return value


def validate_response(value):
    if type(value) is not dict or type(value.get("kind")) is not str:
        raise ScheduleError("invalid-scheduler-response")
    kind = value["kind"]
    if kind == "change":
        if (set(value) != {"kind", "outcome", "task"}
                or value["outcome"] not in ("applied", "unchanged", "unconfirmed")):
            raise ScheduleError("invalid-scheduler-response")
        if value["task"] is not None:
            validate_public_task(value["task"])
        elif value["outcome"] == "applied":
            raise ScheduleError("invalid-scheduler-response")
    elif kind in ("tasks", "runs"):
        keys = {"kind", "items", "offset", "hasMore", "nextOffset"}
        keys |= {"pendingCreates", "pendingCount", "pendingRemovals"} if kind == "tasks" else {"taskId"}
        if set(value) != keys or type(value["items"]) is not list or type(value["hasMore"]) is not bool:
            raise ScheduleError("invalid-scheduler-response")
        _integer(value["offset"], 0, 1_000_000)
        if value["hasMore"]:
            _integer(value["nextOffset"], value["offset"] + 1, 1_000_000)
        elif value["nextOffset"] is not None:
            raise ScheduleError("invalid-scheduler-response")
        if kind == "tasks":
            if (len(value["items"]) > PAGE_SIZE or type(value["pendingCreates"]) is not list
                    or type(value["pendingRemovals"]) is not list or len(value["pendingCreates"]) > PAGE_SIZE
                    or len(value["pendingRemovals"]) > PAGE_SIZE):
                raise ScheduleError("invalid-scheduler-response")
            _integer(value["pendingCount"], len(value["pendingCreates"]), 2000)
            for item in value["items"] + value["pendingRemovals"]:
                validate_public_task(item)
            for item in value["pendingCreates"]:
                if type(item) is not dict or set(item) != {"requestId", "name", "createdAt"}:
                    raise ScheduleError("invalid-scheduler-response")
                request_id(item["requestId"])
                _text(item["name"], 160)
                _integer(item["createdAt"], 0, 2**53 - 1)
        else:
            identifier(value["taskId"])
            if len(value["items"]) > 1:
                raise ScheduleError("invalid-scheduler-response")
            for item in value["items"]:
                if (type(item) is not dict or set(item) != {"timestamp", "status", "summary", "summaryTruncated", "durationMs"}
                        or item["status"] not in ("ok", "error", "skipped", "unknown")
                        or type(item["summaryTruncated"]) is not bool):
                    raise ScheduleError("invalid-scheduler-response")
                _integer(item["timestamp"], 0, 2**53 - 1)
                _text(item["summary"], 262144, empty=True)
                if item["durationMs"] is not None:
                    _integer(item["durationMs"], 0, 2**53 - 1)
    else:
        raise ScheduleError("invalid-scheduler-response")
    if len(json.dumps(value, allow_nan=False).encode()) > MAX_RESPONSE:
        raise ScheduleError("task-result-too-large")
    return value
