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
from project_runtime import recover_job, run_stage, seed_project
from project_runtime_protocol import validate_project_lock
from project_snapshot import snapshot_project


class ProjectController:
    def __init__(self, workspace, state_root, image, *, authorize):
        if not callable(authorize):
            raise ValueError("an external authorization adapter is required")
        self.workspace, self.image, self.authorize = str(workspace), image, authorize
        self.jobs = ProjectJobs(state_root)
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
        return result

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
        self.jobs.recovery_result(job, recover_job(request["image"], job, cancel=True,
                                                 required_stage=expected, timeout=10))

    def _work(self, job, request, source, cancel):
        if not self.jobs.claim(job):
            return
        resources_started = False
        try:
            self._require(request["project"], "execute", request)
            found = subprocess.run(["docker", "volume", "inspect", job], capture_output=True, timeout=15)
            if found.returncode == 0:
                raise RuntimeError("job volume already exists; reconciliation required")
            subprocess.run(["docker", "volume", "create", "--label", "org.osmantic.ods.project-job=" + job, job],
                           check=True, capture_output=True, timeout=15)
            resources_started = True
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
                self.jobs.cleanup_warnings(job, self._cleanup(job))

    def _cleanup(self, job):
        warnings = []
        for stage in ("seed-manifests", "seed-source", "acquire", "test", "build"):
            try:
                name = job + "-" + stage
                result = subprocess.run(["docker", "inspect", name], capture_output=True, timeout=10)
                if result.returncode != 0:
                    continue
                container = json.loads(result.stdout)[0]
                if (container["Config"]["Image"] != self.image
                        or container["Config"].get("Labels", {}).get("org.osmantic.ods.project-job") != job):
                    warnings.append("container identity mismatch: " + stage)
                    continue
                removed = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
                if removed.returncode:
                    warnings.append("container cleanup unconfirmed: " + stage)
            except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError):
                warnings.append("container cleanup unavailable: " + stage)
        # Remove only this private named volume if all containers released it.
        try:
            read = subprocess.run(["docker", "volume", "inspect", job], capture_output=True, check=True, timeout=15)
            volume = json.loads(read.stdout)[0]
            if (volume.get("Labels") or {}).get("org.osmantic.ods.project-job") != job:
                warnings.append("volume identity mismatch")
            elif subprocess.run(["docker", "volume", "rm", job], capture_output=True, timeout=15).returncode:
                warnings.append("volume cleanup unconfirmed")
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
            warnings.append("volume cleanup unavailable")
        return warnings

    def close(self):
        self.pool.shutdown(wait=True)
