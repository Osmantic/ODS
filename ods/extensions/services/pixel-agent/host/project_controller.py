"""Project coordinator; requires an external authorization adapter.

The installed service binds
the adapter to real owner policy; neither tool arguments nor these job records
provide authority. Existing jobs are observed, never automatically replayed.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import threading

from project_artifacts import collect_artifacts, import_artifacts
from project_jobs import ProjectJobs
from project_runtime import recover_job, run_stage, seed_project, start_keeper, observe_stage
from project_storage import ProjectStorage, verify_volume
from project_runtime_protocol import validate_project_lock
from project_snapshot import snapshot_project


class ProjectController:
    def __init__(self, workspace, state_root, image, *, authorize, storage_limits=None):
        if not callable(authorize):
            raise ValueError("an external authorization adapter is required")
        self.workspace, self.image, self.authorize = str(workspace), image, authorize
        self.jobs = ProjectJobs(state_root)
        self.storage = ProjectStorage(state_root, **(storage_limits or {}))
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ods-project")
        self.lock = threading.Lock()
        self.futures, self.cancellations = {}, {}

    def _require(self, project, action, binding=None):
        if self.authorize(project, action, binding) is not True:
            raise PermissionError("project operation is not authorized")

    def submit(self, request_key, project, output_directory="out"):
        self._require(project, "snapshot")
        with self.lock:
            for completed in [key for key, future in self.futures.items() if future.done()]:
                self.futures.pop(completed)
                self.cancellations.pop(completed, None)
            if sum(not f.done() for f in self.futures.values()) >= 8:
                raise RuntimeError("project queue is full")
            source = snapshot_project(self.workspace, project)
            files = source["files"]
            validate_project_lock(json.loads(files["package.json"]), json.loads(files["package-lock.json"]))
            request = {"project": project, "sourceSha256": source["sha256"],
                       "image": self.image, "outputDirectory": output_directory}
            self._require(project, "execute", request)
            job, created = self.jobs.create(request_key, request)
            if created:
                cancel = threading.Event()
                self.cancellations[job] = cancel
                self.futures[job] = self.pool.submit(self._work, job, request, source, cancel)
        return self.jobs.observe(job)

    def observe(self, job):
        row = self.jobs.observe(job)
        self._require(row["request"]["project"], "observe", row["request"])
        return row

    def cancel(self, job):
        row = self.jobs.observe(job)
        self._require(row["request"]["project"], "cancel", row["request"])
        with self.lock:
            result = self.jobs.request_cancel(job)
            if job in self.cancellations:
                self.cancellations[job].set()
            future = self.futures.get(job)
            if result["state"] == "unconfirmed" and (future is None or future.done()):
                if sum(not f.done() for f in self.futures.values()) >= 8:
                    raise RuntimeError("project queue is full")
                self.futures[job] = self.pool.submit(self._recover_cancel, job, row["request"])
            elif (result['state'] in ('succeeded', 'failed', 'cancelled')
                  and (row.get('output') or {}).get('cleanupWarnings')
                  and (future is None or future.done())):
                if sum(not f.done() for f in self.futures.values()) >= 8:
                    raise RuntimeError('project queue is full')
                self.futures[job] = self.pool.submit(self._retry_cleanup, job, row['request'])
        return result

    def _retry_cleanup(self, job, request):
        # Retry only resource removal; do not rewrite a terminal job outcome.
        self._require(request['project'], 'cancel', request)
        self.jobs.cleanup_warnings(job, self._cleanup(job, image=request['image']))

    def _recover_cancel(self, job, request):
        # Recheck policy after queueing; stored job metadata grants no authority.
        try:
            self._require(request["project"], "cancel", request)
        except PermissionError:
            self.jobs.recovery_result(job, {"status": "unconfirmed", "evidence": "authorization-denied"})
            raise
        row = self.jobs.observe(job)
        stages = ("acquire", "test", "build")
        expected = stages[len(row["steps"])] if len(row["steps"]) < len(stages) else None
        if row['steps'] and row['steps'][-1]['status'] == 'unconfirmed':
            expected = row['steps'][-1]['stage']
        evidence = recover_job(request["image"], job, cancel=True, required_stage=expected, timeout=10)
        self.jobs.recovery_result(job, evidence)
        if evidence.get('status') == 'cancelled':
            # Tmpfs contents are ephemeral; retain receipts, not reserved RAM.
            self.jobs.cleanup_warnings(job, self._cleanup(job, image=request['image']))

    def _work(self, job, request, source, cancel):
        if not self.jobs.claim(job):
            return
        resources_started = False
        try:
            self._require(request["project"], "execute", request)
            self.storage.reserve(self.image, job)
            resources_started = True
            self.storage.create_volume(job)
            start_keeper(self.image, job)
            seed_project(self.image, job, source, manifests_only=True)
            for stage in ("acquire", "test", "build"):
                self._require(request["project"], "execute", request)
                if stage == "test" and not cancel.is_set():
                    seed_project(self.image, job, source, manifests_only=False)
                result = run_stage(self.image, job, stage, cancel=cancel)
                self.jobs.record_stage(job, stage, result)
                if result["status"] != "succeeded":
                    return
            self._require(request["project"], "import", request)
            if cancel.is_set():
                self.jobs.controller_failure(job, "cancelled before artifact import", state="cancelled")
                return
            artifacts = collect_artifacts(self.image, job, request["outputDirectory"])
            # Collection may take time: recheck owner authority and cancellation
            # immediately before writing anything back to the workspace.
            self._require(request["project"], "import", request)
            if cancel.is_set():
                self.jobs.controller_failure(job, "cancelled before artifact import", state="cancelled")
                return
            relative = import_artifacts(self.workspace, request["project"], job, artifacts)
            self.jobs.complete(job, {"sha256": artifacts["sha256"], "files": len(artifacts["files"]),
                                    "bytes": artifacts["bytes"], "relativeDirectory": relative})
        except Exception as error:
            self.jobs.controller_failure(job, error)
        finally:
            if resources_started:
                if self.jobs.observe(job)['state'] in ('succeeded', 'failed', 'cancelled'):
                    self.jobs.cleanup_warnings(job, self._cleanup(job))
                else:
                    # A timed-out Docker CLI can still create its container.
                    # Removing the volume now could make that late run create
                    # an ordinary unbounded volume with the same name.
                    self.jobs.cleanup_warnings(job, ['Unconfirmed execution: bounded storage remains reserved.'])

    def _cleanup(self, job, *, image=None):
        image = self.image if image is None else image
        warnings = []
        for stage in ("seed-manifests", "seed-source", "acquire", "test", "build", "keeper"):
            try:
                name = job + "-" + stage
                result = subprocess.run(["docker", "inspect", name], capture_output=True, timeout=10)
                if result.returncode != 0:
                    continue
                container = json.loads(result.stdout)[0]
                identity = container.get('Id')
                evidence = observe_stage(image, job, stage, container_id=identity)
                if (not identity or evidence.get('evidence') != 'docker-state'):
                    warnings.append("container identity mismatch: " + stage)
                    continue
                removed = subprocess.run(["docker", "rm", "-f", identity], capture_output=True, timeout=15)
                if removed.returncode:
                    warnings.append("container cleanup unconfirmed: " + stage)
            except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError):
                warnings.append("container cleanup unavailable: " + stage)
        # Remove only this private named volume if all containers released it.
        try:
            read = subprocess.run(["docker", "volume", "inspect", job], capture_output=True, check=True, timeout=15)
            volume = json.loads(read.stdout)[0]
            if not verify_volume(volume, job):
                warnings.append("volume identity mismatch")
            elif subprocess.run(["docker", "volume", "rm", job], capture_output=True, timeout=15).returncode:
                warnings.append("volume cleanup unconfirmed")
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
            warnings.append("volume cleanup unavailable")
        try:
            self.storage.release_removed(job)
        except (OSError, subprocess.SubprocessError, ValueError):
            warnings.append('storage capacity remains reserved')
        return warnings

    def close(self):
        self.pool.shutdown(wait=True)
