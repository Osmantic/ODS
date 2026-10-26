"""Owner-side native task operations; root selects the owner, gateway and code.

The private creation receipt is written before cron.add. An unconfirmed add is
looked up by its unique native description, never sent again. Removing a task
keeps its reviewed metadata so native run summaries stay reachable afterwards.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time

import pixel_access_mode as controller
from pixel_schedules import (
    AGENT, OPENCLAW_VERSION, PAGE_SIZE, ScheduleError, checksum, identifier,
    native_task, owned_task, project_task, request_id, task_fingerprint, task_revision, validate_public_task,
    validate_request, validate_response,
)

MAX_LEDGER = 16 * 1024 * 1024
MAX_RECEIPTS = 2000


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    def constant(_value):
        raise ValueError()
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ScheduleError("invalid-schedule-state") from None


class TaskStore:
    def __init__(self, directory):
        self.directory = Path(controller._prepare_state_dir(str(directory)))
        self.path = self.directory / "tasks.json"

    def read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {"version": 1, "requests": {}, "saved": {}}
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                    or info.st_mode & 0o077 or info.st_size > MAX_LEDGER):
                raise ScheduleError("unsafe-schedule-state")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_LEDGER + 1)
            if len(raw) > MAX_LEDGER:
                raise ScheduleError("unsafe-schedule-state")
        finally:
            os.close(fd)
        value = _decode(raw)
        if (type(value) is not dict or set(value) != {"version", "requests", "saved"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["requests"]) is not dict or type(value["saved"]) is not dict
                or len(value["requests"]) > MAX_RECEIPTS or len(value["saved"]) > MAX_RECEIPTS):
            raise ScheduleError("invalid-schedule-state")
        for receipt in value["requests"].values():
            if (type(receipt) is not dict or set(receipt) != {"hash", "nativeHash", "name", "createdAt", "state", "jobId"}
                    or receipt["state"] not in ("pending", "recorded", "dismissed")
                    or any(type(receipt[key]) is not str or len(receipt[key]) != 64
                           or any(char not in "0123456789abcdef" for char in receipt[key]) for key in ("hash", "nativeHash"))
                    or type(receipt["name"]) is not str or type(receipt["createdAt"]) is not int or receipt["createdAt"] < 0
                    or (receipt["jobId"] is not None and type(receipt["jobId"]) is not str)):
                raise ScheduleError("invalid-schedule-state")
        try:
            for key in value["requests"]:
                request_id(key)
            for key, task in value["saved"].items():
                validate_public_task(task)
                if task["id"] != key:
                    raise ScheduleError("invalid-schedule-state")
            for receipt in value["requests"].values():
                if receipt["state"] == "recorded":
                    identifier(receipt["jobId"])
                    if receipt["jobId"] not in value["saved"]:
                        raise ScheduleError("invalid-schedule-state")
                elif receipt["jobId"] is not None:
                    raise ScheduleError("invalid-schedule-state")
        except ScheduleError:
            raise ScheduleError("invalid-schedule-state") from None
        return value

    def write(self, value):
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()
        if len(raw) > MAX_LEDGER:
            raise ScheduleError("task-history-full")
        controller._atomic_write(str(self.path), raw, 0o600, durable=True)


def gateway_call(openclaw, port, token):
    """Bind the public SDK to the exact OpenClaw executable chosen by ODS."""
    package = Path(openclaw).resolve(strict=True).parent
    metadata = _decode((package / "package.json").read_bytes())
    if type(metadata) is not dict or metadata.get("name") != "openclaw" or metadata.get("version") != OPENCLAW_VERSION:
        raise ScheduleError("scheduler-runtime-unsupported")
    node = shutil.which("node")
    if not node:
        raise ScheduleError("scheduler-runtime-unavailable")
    program = Path(__file__).with_name("scheduled_tasks_gateway.mjs")

    def call(method, params):
        frame = {"packageDir": str(package), "port": port, "token": token, "method": method, "params": params}
        try:
            result = subprocess.run([node, str(program)], input=json.dumps(frame).encode(),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ScheduleError("scheduler-unavailable") from None
        if len(result.stdout) > 1024 * 1024:
            raise ScheduleError("invalid-scheduler-response")
        value = _decode(result.stdout)
        if (type(value) is not dict or type(value.get("ok")) is not bool
                or set(value) != ({"ok", "result"} if value["ok"] else {"ok", "code"})):
            raise ScheduleError("invalid-scheduler-response")
        if not value["ok"]:
            code = value["code"] if value["code"] == "task-not-found" else "scheduler-unavailable"
            raise ScheduleError(code)
        if result.returncode != 0:
            raise ScheduleError("scheduler-unavailable")
        return value["result"]
    return call


class NativeTasks:
    def __init__(self, state_dir, call, *, clock=time.time):
        self.store, self.call, self.clock = TaskStore(state_dir), call, clock

    def operate(self, request):
        validate_request(request)
        with controller._Lock(str(self.store.directory)):
            saved = self.store.read()
            result = self._operate(request, saved)
            return validate_response(result)

    def _get(self, job_id):
        value = self.call("cron.get", {"id": job_id})
        if not owned_task(value) or value.get("id") != job_id:
            raise ScheduleError("task-not-found")
        project_task(value)
        return value

    def _change(self, outcome, task=None):
        return {"kind": "change", "outcome": outcome, "task": task}

    def _create(self, request, saved):
        key = request["requestId"]
        receipt = saved["requests"].get(key)
        if receipt is not None:
            if receipt["hash"] != checksum(request):
                raise ScheduleError("task-request-changed")
            return self._check_create(key, saved)
        if len(saved["requests"]) >= MAX_RECEIPTS or len(saved["saved"]) >= MAX_RECEIPTS:
            raise ScheduleError("task-history-full")
        proposal = native_task(request)
        if proposal["schedule"]["kind"] == "at":
            instant = datetime.fromisoformat(proposal["schedule"]["at"].replace("Z", "+00:00"))
            if instant <= datetime.fromtimestamp(self.clock(), timezone.utc):
                raise ScheduleError("task-time-passed")
        saved["requests"][key] = {"hash": checksum(request), "nativeHash": task_fingerprint(proposal), "name": proposal["name"],
            "createdAt": int(self.clock() * 1000), "state": "pending", "jobId": None}
        self.store.write(saved)
        try:
            job = self.call("cron.add", proposal)
            public = project_task(job)
            if (job.get("description") != proposal["description"]
                    or task_fingerprint(job) != saved["requests"][key]["nativeHash"]):
                raise ScheduleError("invalid-scheduler-response")
        except ScheduleError:
            return self._change("unconfirmed")
        receipt = saved["requests"][key]
        receipt.update(state="recorded", jobId=public["id"])
        saved["saved"][public["id"]] = public
        self.store.write(saved)
        return self._change("applied", public)

    def _check_create(self, key, saved):
        receipt = saved["requests"].get(key)
        if receipt is None:
            raise ScheduleError("task-request-not-found")
        if receipt["state"] == "recorded":
            return self._change("unchanged", saved["saved"].get(receipt["jobId"]))
        marker = f"Created in Portal (request {key})."
        matches, offset, complete = [], 0, False
        for _page in range(10):
            page = self.call("cron.list", {"agentId": AGENT, "includeDisabled": True, "limit": 200, "offset": offset})
            if type(page) is not dict or type(page.get("jobs")) is not list or type(page.get("hasMore")) is not bool:
                raise ScheduleError("invalid-scheduler-response")
            matches.extend(job for job in page["jobs"] if owned_task(job) and job.get("description") == marker)
            if not page["hasMore"]:
                complete = True
                break
            next_offset = page.get("nextOffset")
            if type(next_offset) is not int or next_offset <= offset:
                raise ScheduleError("invalid-scheduler-response")
            offset = next_offset
        if (not complete or len(matches) != 1
                or task_fingerprint(matches[0]) != receipt["nativeHash"]):
            return self._change("unconfirmed")
        public = project_task(matches[0])
        receipt.update(state="recorded", jobId=public["id"])
        saved["saved"][public["id"]] = public
        self.store.write(saved)
        return self._change("unchanged", public)

    def _operate(self, request, saved):
        action = request["action"]
        if action == "create":
            return self._create(request, saved)
        if action == "check-create":
            return self._check_create(request["requestId"], saved)
        if action == "dismiss-create":
            receipt = saved["requests"].get(request["requestId"])
            if receipt is None:
                # A lost HTTP request may never have reached this controller.
                # Dismissing its UI notice is still harmless; it is not a
                # cancellation and must never issue a native mutation.
                return self._change("unchanged")
            if receipt["state"] == "pending":
                receipt["state"] = "dismissed"
                self.store.write(saved)
            return self._change("unchanged")
        if action in ("list", "archived"):
            return self._list(request, saved)
        if action == "runs":
            return self._runs(request, saved)
        return self._mutate(request, saved)

    def _list(self, request, saved):
        offset = request["offset"]
        if request["action"] == "archived":
            entries = sorted((task for task in saved["saved"].values() if task["removed"]),
                             key=lambda item: item["updatedAt"] or 0, reverse=True)
            items = entries[offset:offset + PAGE_SIZE]
            more = offset + len(items) < len(entries)
            following = offset + len(items) if more else None
        else:
            page = self.call("cron.list", {"agentId": AGENT, "includeDisabled": True, "limit": PAGE_SIZE,
                                          "offset": offset, "sortBy": "name", "sortDir": "asc"})
            if type(page) is not dict or type(page.get("jobs")) is not list or type(page.get("hasMore")) is not bool:
                raise ScheduleError("invalid-scheduler-response")
            items = [project_task(job) for job in page["jobs"] if owned_task(job)]
            more, following = page["hasMore"], page.get("nextOffset")
            if more and (type(following) is not int or following <= offset):
                raise ScheduleError("invalid-scheduler-response")
        pending = [{"requestId": key, "name": value["name"], "createdAt": value["createdAt"]}
                   for key, value in saved["requests"].items() if value["state"] == "pending"]
        return {"kind": "tasks", "items": items, "offset": offset, "hasMore": more,
                "nextOffset": following if more else None, "pendingCreates": pending[:10], "pendingCount": len(pending),
                "pendingRemovals": [task for task in saved["saved"].values() if task["removalPending"]][:10]}

    def _mutate(self, request, saved):
        job_id = request["id"]
        prior = saved["saved"].get(job_id)
        try:
            job = self._get(job_id)
        except ScheduleError as error:
            if error.code != "task-not-found" or prior is None or not (prior["removed"] or prior["removalPending"]):
                raise
            prior.update(removed=True, removalPending=False)
            self.store.write(saved)
            return self._change("unchanged", prior)
        public = project_task(job)
        if request["action"] == "set-enabled" and job["enabled"] is request["enabled"]:
            return self._change("unchanged", public)
        if task_revision(job) != request["revision"]:
            raise ScheduleError("task-changed")
        if request["action"] == "set-enabled":
            updated = self.call("cron.update", {"id": job_id, "patch": {"enabled": request["enabled"]}})
            if (not owned_task(updated) or updated.get("id") != job_id
                    or updated.get("enabled") is not request["enabled"]):
                raise ScheduleError("invalid-scheduler-response")
            return self._change("applied", project_task(updated))
        public["removalPending"] = True
        if job_id not in saved["saved"] and len(saved["saved"]) >= MAX_RECEIPTS:
            raise ScheduleError("task-history-full")
        saved["saved"][job_id] = public
        self.store.write(saved)
        try:
            result = self.call("cron.remove", {"id": job_id})
            if type(result) is not dict or result.get("ok") is not True or type(result.get("removed")) is not bool:
                raise ScheduleError("invalid-scheduler-response")
        except ScheduleError:
            return self._change("unconfirmed", public)
        public.update(removed=True, removalPending=False)
        self.store.write(saved)
        return self._change("applied", public)

    def _runs(self, request, saved):
        job_id = request["id"]
        prior = saved["saved"].get(job_id)
        if prior is None or not prior["removed"]:
            self._get(job_id)
        page = self.call("cron.runs", {"id": identifier(job_id), "scope": "job", "limit": 1,
                                       "offset": request["offset"], "sortDir": "desc"})
        if type(page) is not dict or type(page.get("entries")) is not list or type(page.get("hasMore")) is not bool:
            raise ScheduleError("invalid-scheduler-response")
        items = []
        for entry in page["entries"]:
            if type(entry) is not dict or entry.get("jobId") != job_id:
                raise ScheduleError("invalid-scheduler-response")
            summary = entry.get("summary") if entry.get("summary") is not None else ""
            if type(summary) is not str:
                raise ScheduleError("invalid-scheduler-response")
            items.append({"timestamp": entry.get("ts"), "status": entry.get("status")
                          if entry.get("status") in ("ok", "error", "skipped") else "unknown",
                          "summary": summary[:65536], "summaryTruncated": len(summary) > 65536,
                          "durationMs": entry.get("durationMs")})
        return {"kind": "runs", "taskId": job_id, "items": items, "offset": request["offset"],
                "hasMore": page["hasMore"], "nextOffset": page.get("nextOffset") if page["hasMore"] else None}
