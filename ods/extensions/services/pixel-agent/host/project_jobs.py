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


class ProjectRecoveryRequired(ValueError):
    def __init__(self, job):
        super().__init__('recover uncertain work for this project before resubmitting')
        self.job = job


class ProjectJobs:
    @staticmethod
    def _blocking(db, request):
        # One authenticated owner per controller. Isolate by mutable project;
        # fixed diagnostics have no project target and are isolated by runtime.
        rows = db.execute("""SELECT * FROM jobs WHERE state='unconfirmed'
            AND json_extract(request,'$.project')=?
            AND COALESCE(json_extract(request,'$.kind'),'build')=?
            AND (?='build' OR json_extract(request,'$.runtime')=?)
            ORDER BY updated,id""", (request['project'], request.get('kind', 'build'),
                request.get('kind', 'build'), request.get('runtime'))).fetchall()
        for row in rows:
            from project_owner_resolution import read_resolution
            state_root = str(Path(db.execute('PRAGMA database_list').fetchone()[2]).parent)
            resolution = read_resolution(row['id'], state_root, ProjectJobs.receipt_hash(row))
            if resolution is None or not ProjectJobs._valid_resolution(row, resolution):
                return row['id']
        return None

    @staticmethod
    def receipt_hash(row):
        """Bind every original persisted field; later receipt changes reclose the fence."""
        return hashlib.sha256(ProjectJobs._json(dict(row)).encode()).hexdigest()

    @staticmethod
    def _valid_resolution(row, resolution):
        try:
            record = json.loads(resolution['record'])
            return (resolution['receipt_hash'] == ProjectJobs.receipt_hash(row)
                    and hashlib.sha256(resolution['record'].encode()).hexdigest() == resolution['record_hash']
                    and record['kind'] == 'owner-attested-retry-resolution'
                    and record['job'] == row['id']
                    and record['receiptSha256'] == resolution['receipt_hash']
                    and record['historicalOutcome'] == 'unknown'
                    and record['sameEngineAttested'] is True
                    and record['currentResourcesAbsent'] is True
                    and record['engineBootTime'] > row['updated'])
        except (KeyError, TypeError, ValueError):
            return False
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

    def original_receipt(self, job):
        with self._connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
        if row is None:
            raise KeyError(job)
        return dict(row)

    def record_owner_resolution(self, job, expected_hash, record):
        """Offline owner CLI only. Never changes the historical job or replays it."""
        encoded = self._json(record)
        resolution = {'receipt_hash': expected_hash, 'record': encoded,
                      'record_hash': hashlib.sha256(encoded.encode()).hexdigest()}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
            if row is None or row['state'] != 'unconfirmed' or not self._valid_resolution(row, resolution):
                raise ValueError('original receipt changed or resolution invalid')
            from project_owner_resolution import write_resolution
            write_resolution(job, resolution)

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
        fields = {"project", "sourceSha256", "image", "outputDirectory"}
        diagnostic = (isinstance(request, dict) and set(request) == fields | {'kind', 'runtime'}
                      and request['kind'] == 'diagnostic' and request['runtime'] in ('npm', 'python')
                      and request['project'] == 'ods-diagnostic' and request['outputDirectory'] == 'diagnostic')
        if diagnostic:
            from project_diagnostics import diagnostic_digest
            if request['sourceSha256'] != diagnostic_digest(request['runtime']):
                raise ValueError('diagnostic program binding mismatch')
        if not isinstance(request, dict) or not (
                diagnostic or set(request) == fields or
                (set(request) == fields | {"runtime"} and request["runtime"] == "python")):
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
            blocker = self._blocking(db, request)
            if blocker:
                raise ProjectRecoveryRequired(blocker)
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
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT request,state FROM jobs WHERE id=?", (job,)).fetchone()
            if row and row['state'] == 'queued':
                blocker = self._blocking(db, json.loads(row['request']))
                if blocker:
                    db.execute("UPDATE jobs SET state='failed',output=?,updated=? WHERE id=?", (
                        self._json({'code': 'recovery-required', 'recoveryJobId': blocker,
                                    'executionStarted': False, 'retryEligible': False}), time.time(), job))
                    return False
            return db.execute("UPDATE jobs SET state='running',updated=? WHERE id=? AND state='queued' AND cancel_requested=0",
                              (time.time(), job)).rowcount == 1

    def preflight_failed(self, job):
        with self._connect() as db:
            db.execute("UPDATE jobs SET state='failed',output=?,updated=? WHERE id=? AND state='running' AND steps='[]'", (
                self._json({'code': 'engine-info-unavailable', 'executionStarted': False,
                            'retryEligible': True, 'automaticRetry': False,
                            'error': 'The read-only engine query failed before project execution.'}), time.time(), job))

    def record_stage(self, job, stage, result):
        if stage not in ("acquire", "test", "build", "diagnose") or not isinstance(result, dict):
            raise ValueError("invalid stage result")
        status = result.get("status")
        if status not in ("succeeded", "failed", "cancelled", "timed_out", "unconfirmed"):
            raise ValueError("invalid stage status")
        if status == "succeeded" and (type(result.get("exitCode")) is not int or result["exitCode"] != 0):
            raise ValueError("success requires zero exit status")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,steps,request FROM jobs WHERE id=?", (job,)).fetchone()
            if row is None or row["state"] != "running":
                raise ValueError("job is not running")
            steps = json.loads(row["steps"])
            expected = ('diagnose',) if json.loads(row['request']).get('kind') == 'diagnostic' else ("acquire", "test", "build")
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
            db.execute("UPDATE jobs SET cancel_requested=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,updated=? WHERE id=? AND state IN ('queued','running','unconfirmed')",
                       (time.time(), job))
        return self.observe(job)

    def diagnostic_result(self, job, output):
        """Record a fixed probe's receipt without asserting an imported artifact."""
        from project_diagnostics import validate_diagnostic
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state,steps,request,cancel_requested FROM jobs WHERE id=?', (job,)).fetchone()
            if row is None or json.loads(row['request']).get('kind') != 'diagnostic':
                raise ValueError('diagnostic job required')
            runtime = json.loads(row['request'])['runtime']
            if output.get('checks'):
                validate_diagnostic(self._json({'schemaVersion': 1, 'scope': 'managed-executor',
                                               'runtime': runtime, 'checks': output['checks']}), runtime)
            state = row['state']
            if state == 'running':
                steps = json.loads(row['steps'])
                if len(steps) != 1 or steps[0]['stage'] != 'diagnose' or steps[0]['status'] != 'succeeded':
                    raise ValueError('diagnostic execution evidence incomplete')
                state = 'cancelled' if row['cancel_requested'] else 'succeeded'
            if state == 'queued':
                raise ValueError('diagnostic has not run')
            db.execute('UPDATE jobs SET state=?,output=?,updated=? WHERE id=?',
                       (state, self._json(output), time.time(), job))

    def recover_interrupted(self):
        """Called only by the service holding its exclusive lifetime lock.

        Queued work never claimed execution. Running work may still have a
        Docker process or imported files, so restart cannot assert failure,
        cancellation or success. Preserve its stage evidence for reconciliation.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for state, outcome, message in (
                ("queued", "failed", "Service restarted before execution; this job was not replayed."),
                ("running", "unconfirmed", "Service restarted during execution; inspect the job before retrying."
                 " Containers or output may remain; this job was not replayed."),
            ):
                db.execute("UPDATE jobs SET state=?,output=?,updated=? WHERE state=?",
                           (outcome, self._json({"error": message, "recoveryRequired": state == "running"}),
                            time.time(), state))

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
        if row["state"] == "unconfirmed":
            from project_runtime import recover_job
            return {"job": row, "runtime": recover_job(row["request"]["image"], job,
                                                       runtime=row["request"].get("runtime", "npm"))}
        if row["state"] != "running":
            return {"job": row, "runtime": None}
        stages = ('diagnose',) if row['request'].get('kind') == 'diagnostic' else ("acquire", "test", "build")
        if len(row["steps"]) >= len(stages):
            status = 'awaiting-diagnostic-receipt' if row['request'].get('kind') == 'diagnostic' else 'awaiting-artifact-import'
            return {"job": row, "runtime": {"status": status}}
        stage = stages[len(row["steps"])]
        runtime_args = {"runtime": "python"} if row["request"].get("runtime") == "python" else {}
        evidence = observer(row["request"]["image"], job, stage, **runtime_args)
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

    def recovery_result(self, job, evidence):
        """Record stopped execution, without claiming lost import/exit evidence."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,output,cancel_requested,request FROM jobs WHERE id=?", (job,)).fetchone()
            if row is None or row["state"] != "unconfirmed":
                return
            output = json.loads(row["output"]) if row["output"] else {}
            output.update(runtimeRecovery=evidence)
            if json.loads(row['request']).get('kind') != 'diagnostic':
                output['artifactImportUnconfirmed'] = True
            confirmed = (row["cancel_requested"] and evidence.get("status") == "cancelled"
                         and evidence.get("evidence") == "docker-state")
            db.execute("UPDATE jobs SET state=?,output=?,updated=? WHERE id=?",
                       ("cancelled" if confirmed else "unconfirmed", self._json(output), time.time(), job))
