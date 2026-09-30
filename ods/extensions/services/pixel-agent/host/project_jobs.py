"""Private durable job bookkeeping; not an authorization or execution API.

An authorized controller creates and claims jobs. Observing an existing job
never retries it; interrupted running jobs require external reconciliation.
"""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

from project_snapshot import _component


class ProjectJobs:
    def __init__(self, root):
        root = Path(root)
        root.mkdir(mode=0o700, parents=False, exist_ok=True)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("private controller state directory required")
        self.path = root / "jobs.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("unsafe controller database")
        finally:
            os.close(fd)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                request_hash TEXT NOT NULL, request TEXT NOT NULL,
                state TEXT NOT NULL, steps TEXT NOT NULL DEFAULT '[]',
                output TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                updated REAL NOT NULL)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _json(value):
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > 1024 * 1024:
            raise ValueError("job record too large")
        return encoded

    def create(self, request_key, request):
        if not isinstance(request_key, str) or not re.fullmatch(r"[a-f0-9]{64}", request_key):
            raise ValueError("controller request key required")
        if not isinstance(request, dict) or set(request) != {"project", "sourceSha256", "image", "outputDirectory"}:
            raise ValueError("exact project execution request required")
        project = request["project"]
        if (not isinstance(project, str) or len(project) > 1024 or len(project.split("/")) > 8
                or not all(_component(part) for part in project.split("/"))):
            raise ValueError("invalid project")
        for field, pattern in (("sourceSha256", r"[a-f0-9]{64}"),
                               ("image", r"sha256:[a-f0-9]{64}"),
                               ("outputDirectory", r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")):
            if not isinstance(request[field], str) or not re.fullmatch(pattern, request[field]):
                raise ValueError("invalid execution binding")
        encoded = self._json(request)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT id,request_hash FROM jobs WHERE request_key=?", (request_key,)).fetchone()
            if previous:
                if previous["request_hash"] != digest:
                    raise ValueError("request key reused for different input")
                return previous["id"], False
            job = "ods-project-" + secrets.token_hex(12)
            db.execute("INSERT INTO jobs(id,request_key,request_hash,request,state,updated) VALUES(?,?,?,?,?,?)",
                       (job, request_key, digest, encoded, "queued", time.time()))
            return job, True

    def observe(self, job):
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if row is None:
            raise KeyError(job)
        value = dict(row)
        for key in ("request", "steps", "output"):
            value[key] = json.loads(value[key]) if value[key] is not None else None
        value["cancel_requested"] = bool(value["cancel_requested"])
        return value

    def claim(self, job):
        with self._connect() as db:
            return db.execute("UPDATE jobs SET state='running',updated=? WHERE id=? AND state='queued' AND cancel_requested=0",
                              (time.time(), job)).rowcount == 1

    def record_stage(self, job, stage, result):
        if stage not in ("acquire", "test", "build") or not isinstance(result, dict):
            raise ValueError("invalid stage result")
        status = result.get("status")
        if status not in ("succeeded", "failed", "cancelled", "timed_out", "unconfirmed"):
            raise ValueError("invalid stage status")
        if status == "succeeded" and (type(result.get("exitCode")) is not int or result["exitCode"] != 0):
            raise ValueError("success requires zero exit status")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,steps FROM jobs WHERE id=?", (job,)).fetchone()
            if row is None or row["state"] != "running":
                raise ValueError("job is not running")
            steps = json.loads(row["steps"])
            expected = ("acquire", "test", "build")
            if len(steps) >= len(expected) or expected[len(steps)] != stage:
                raise ValueError("stage order or duplicate result")
            steps.append({**result, "stage": stage})
            state = "running" if status == "succeeded" else ("failed" if status == "timed_out" else status)
            db.execute("UPDATE jobs SET steps=?,state=?,updated=? WHERE id=?",
                       (self._json(steps), state, time.time(), job))

    def complete(self, job, output):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,steps,cancel_requested,request FROM jobs WHERE id=?", (job,)).fetchone()
            if row is None or row["state"] != "running" or row["cancel_requested"]:
                raise ValueError("job cannot complete")
            steps = json.loads(row["steps"])
            if ([s["stage"] for s in steps] != ["acquire", "test", "build"]
                    or any(s["status"] != "succeeded" for s in steps)):
                raise ValueError("execution evidence incomplete")
            if (not isinstance(output, dict) or not isinstance(output.get("sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", output["sha256"])
                    or type(output.get("files")) is not int or not 1 <= output["files"] <= 128
                    or output.get("relativeDirectory") != json.loads(row["request"])["project"]
                    + "/ods-builds/" + job.removeprefix("ods-project-") + "/site"):
                raise ValueError("artifact evidence missing")
            db.execute("UPDATE jobs SET state='succeeded',output=?,updated=? WHERE id=?",
                       (self._json(output), time.time(), job))

    def request_cancel(self, job):
        with self._connect() as db:
            db.execute("UPDATE jobs SET cancel_requested=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,updated=? WHERE id=? AND state IN ('queued','running')",
                       (time.time(), job))
        return self.observe(job)

    def controller_failure(self, job, reason, *, state="unconfirmed"):
        # A controller exception alone cannot establish that Docker stopped or
        # that artifact import had no effect. Callers must prove a narrower state.
        if state not in ("unconfirmed", "failed", "cancelled"):
            raise ValueError("invalid controller outcome")
        with self._connect() as db:
            db.execute("UPDATE jobs SET state=?,output=?,updated=? WHERE id=? AND state='running'",
                       (state, self._json({"error": str(reason)[:1024]}), time.time(), job))

    def cleanup_warnings(self, job, warnings):
        if not warnings:
            return
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT output FROM jobs WHERE id=?", (job,)).fetchone()
            output = json.loads(row["output"]) if row["output"] else {}
            output["cleanupWarnings"] = warnings[:8]
            db.execute("UPDATE jobs SET output=?,updated=? WHERE id=?", (self._json(output), time.time(), job))

    def reconcile(self, job, observer=None):
        """Recover a stage's exit evidence only; do not schedule any execution."""
        if observer is None:
            from project_runtime import observe_stage
            observer = observe_stage
        row = self.observe(job)
        if row["state"] != "running":
            return {"job": row, "runtime": None}
        stages = ("acquire", "test", "build")
        if len(row["steps"]) >= len(stages):
            return {"job": row, "runtime": {"status": "awaiting-artifact-import"}}
        stage = stages[len(row["steps"])]
        evidence = observer(row["request"]["image"], job, stage)
        if evidence.get("evidence") == "docker-state" and evidence.get("status") in ("succeeded", "failed"):
            try:
                self.record_stage(job, stage, evidence)
            except ValueError:
                # Another observer/controller may have recorded it first.
                current = self.observe(job)
                if not any(s["stage"] == stage and s.get("exitCode") == evidence.get("exitCode")
                           and s["status"] == evidence["status"] for s in current["steps"]):
                    raise
            row = self.observe(job)
        return {"job": row, "runtime": evidence}
