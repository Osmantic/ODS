"""Project coordinator; requires an external authorization adapter.

The installed service binds
the adapter to real owner policy; neither tool arguments nor these job records
provide authority. Existing jobs are observed, never automatically replayed.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import subprocess
import threading

from project_artifacts import collect_artifacts, import_artifacts
from project_jobs import ProjectJobs
from project_runtime import recover_job, run_stage, seed_project
from project_runtime_protocol import select_project_runtime
from project_capabilities import probe_python_runtime, cleanup_pending_probe, ProbeCleanupPending
from project_snapshot import snapshot_project


class ProjectController:
    def __init__(self, workspace, state_root, image, *, authorize, python_image=None):
        if not callable(authorize):
            raise ValueError("an external authorization adapter is required")
        self.workspace, self.image, self.authorize = str(workspace), image, authorize
        self.python_image = python_image
        self.jobs = ProjectJobs(state_root)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ods-project")
        self.lock = threading.Lock()
        self.futures, self.cancellations = {}, {}
        self._capability_cache = (None, None)
        self._pending_capability_cleanup = None
        self._capability_stop, self._capability_thread = threading.Event(), None

    def initialize_capabilities(self, *, cancel=None):
        """One service-owned probe attempt; never invoked by model tool requests."""
        image = self.python_image
        if self._pending_capability_cleanup is not None:
            pending = self._pending_capability_cleanup
            try:
                cleanup_pending_probe(pending.image, pending.name)
            except (OSError, ValueError, subprocess.SubprocessError):
                return
            self._pending_capability_cleanup = None
        if cancel is not None and cancel.is_set():
            return
        cached_image, cached_evidence = self._capability_cache
        if not image or (cached_image == image and cached_evidence is not None):
            return
        self._capability_cache = (image, None)
        try:
            evidence = probe_python_runtime(image, cancel=cancel)
        except ProbeCleanupPending as pending:
            self._pending_capability_cleanup = pending
            return
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            # Identity discovery must not take the existing executor offline.
            # No facts from an earlier image or inferred host data are returned.
            return
        if self.python_image == image and not (cancel is not None and cancel.is_set()):
            self._capability_cache = (image, evidence)

    def start_capability_probe(self):
        """One service-owned worker; no tool request starts or retries probes."""
        if not self.python_image or self._capability_thread is not None:
            return
        def collect():
            failures = 0
            while not self._capability_stop.is_set():
                self.initialize_capabilities(cancel=self._capability_stop)
                cached_image, cached_evidence = self._capability_cache
                available = cached_image == self.python_image and cached_evidence is not None
                delay = 60 if available else (1, 5, 15, 60)[min(failures, 3)]
                failures = 0 if available else failures + 1
                if self._capability_stop.wait(delay):
                    return
        self._capability_thread = threading.Thread(target=collect, name='ods-project-capabilities', daemon=True)
        self._capability_thread.start()

    def capabilities(self, runtime):
        if runtime != 'python':
            raise ValueError('unsupported capability runtime')
        self._require(None, 'capabilities')
        response = {'schemaVersion': 1, 'kind': 'ods-project-capabilities', 'runtime': runtime,
                    'scope': 'installed-image-only', 'status': 'unavailable'}
        image = self.python_image
        cached_image, cached_evidence = self._capability_cache
        if not image:
            return {**response, 'reason': 'runtime-not-installed'}
        if cached_image != image or cached_evidence is None:
            return {**response, 'reason': 'runtime-probe-unavailable'}
        return {**response, 'status': 'ready', 'image': image,
                **copy.deepcopy(cached_evidence)}

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
            runtime = select_project_runtime(source["files"])
            image = self.python_image if runtime == "python" else self.image
            if not image:
                raise ValueError("managed Python runtime is not installed")
            request = {"project": project, "sourceSha256": source["sha256"],
                       "image": image, "outputDirectory": output_directory}
            if runtime == "python":
                request["runtime"] = runtime
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
                                                 required_stage=expected, timeout=10,
                                                 runtime=request.get("runtime", "npm")))

    def _work(self, job, request, source, cancel):
        if not self.jobs.claim(job):
            return
        resources_started = False
        image, runtime = request["image"], request.get("runtime", "npm")
        try:
            configured = self.python_image if runtime == "python" else self.image
            if runtime not in ("npm", "python") or image != configured:
                raise ValueError("job runtime no longer matches installed configuration")
            self._require(request["project"], "execute", request)
            found = subprocess.run(["docker", "volume", "inspect", job], capture_output=True, timeout=15)
            if found.returncode == 0:
                raise RuntimeError("job volume already exists; reconciliation required")
            subprocess.run(["docker", "volume", "create", "--label", "org.osmantic.ods.project-job=" + job, job],
                           check=True, capture_output=True, timeout=15)
            resources_started = True
            seed_project(image, job, source, manifests_only=True, runtime=runtime)
            for stage in ("acquire", "test", "build"):
                self._require(request["project"], "execute", request)
                if stage == "test" and not cancel.is_set():
                    seed_project(image, job, source, manifests_only=False, runtime=runtime)
                result = run_stage(image, job, stage, cancel=cancel, runtime=runtime)
                self.jobs.record_stage(job, stage, result)
                if result["status"] != "succeeded":
                    return
            self._require(request["project"], "import", request)
            if cancel.is_set():
                self.jobs.controller_failure(job, "cancelled before artifact import", state="cancelled")
                return
            artifacts = collect_artifacts(image, job, request["outputDirectory"])
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
                self.jobs.cleanup_warnings(job, self._cleanup(job, image=image))

    def _cleanup(self, job, *, image=None):
        image = self.image if image is None else image
        warnings = []
        for stage in ("seed-manifests", "seed-source", "acquire", "test", "build"):
            try:
                name = job + "-" + stage
                result = subprocess.run(["docker", "inspect", name], capture_output=True, timeout=10)
                if result.returncode != 0:
                    continue
                container = json.loads(result.stdout)[0]
                if (container["Config"]["Image"] != image
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
        self._capability_stop.set()
        try:
            if self._capability_thread is not None:
                self._capability_thread.join(timeout=10)
                if self._capability_thread.is_alive():
                    raise RuntimeError('capability probe shutdown unconfirmed')
        finally:
            self.pool.shutdown(wait=True)
