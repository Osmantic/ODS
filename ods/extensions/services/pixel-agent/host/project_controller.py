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
from project_runtime import recover_job, run_stage, seed_project, start_keeper, observe_stage
from project_runtime_protocol import select_project_runtime
from project_capabilities import probe_python_runtime, cleanup_pending_probe, ProbeCleanupPending
from project_storage import ProjectStorage, verify_volume, ReadOnlyPreflightError, StorageAdmissionError
from project_snapshot import snapshot_project
from project_diagnostics import SCRATCH_BYTES, diagnostic_digest, diagnostic_output, validate_diagnostic, diagnostic_failure


class ProjectController:
    def __init__(self, workspace, state_root, image, *, authorize, python_image=None, storage_limits=None):
        if not callable(authorize):
            raise ValueError("an external authorization adapter is required")
        self.workspace, self.image, self.authorize = str(workspace), image, authorize
        self.python_image = python_image
        self.jobs = ProjectJobs(state_root)
        self.storage = ProjectStorage(state_root, **(storage_limits or {}))
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

    def diagnose(self, request_key, runtime):
        if runtime not in ('npm', 'python'):
            raise ValueError('unsupported diagnostic runtime')
        self._require(None, 'execute')
        image = self.python_image if runtime == 'python' else self.image
        if not image:
            return diagnostic_output(runtime, code='unavailable', cleanup='not-started')
        request = {'project': 'ods-diagnostic', 'kind': 'diagnostic', 'runtime': runtime,
                   'sourceSha256': diagnostic_digest(runtime), 'image': image, 'outputDirectory': 'diagnostic'}
        with self.lock:
            for completed in [key for key, future in self.futures.items() if future.done()]:
                self.futures.pop(completed)
                self.cancellations.pop(completed, None)
            if sum(not future.done() for future in self.futures.values()) >= 8:
                raise RuntimeError('project queue is full')
            self._require(None, 'execute', request)
            job, created = self.jobs.create(request_key, request)
            if created:
                cancel = threading.Event()
                self.cancellations[job] = cancel
                self.futures[job] = self.pool.submit(self._diagnose, job, request, cancel)
        return self.jobs.observe(job)

    def _diagnose(self, job, request, cancel):
        if not self.jobs.claim(job):
            return
        runtime, image = request['runtime'], request['image']
        storage = ProjectStorage(self.storage.state, job_bytes=min(self.storage.job_bytes, SCRATCH_BYTES),
                                 total_bytes=self.storage.total_bytes, max_jobs=self.storage.max_jobs)
        resources_started, report, code = False, None, 'unavailable'
        reservation_attempted = False
        phase, failure = 'configuration', None
        try:
            if image != (self.python_image if runtime == 'python' else self.image):
                raise ValueError('job runtime no longer matches installed configuration')
            phase = 'authorization'
            self._require(None, 'execute', request)
            if cancel.is_set():
                self.jobs.controller_failure(job, 'cancelled before diagnostic execution', state='cancelled')
                return
            phase = 'storage-reservation'
            reservation_attempted = True
            storage.reserve(image, job)
            resources_started = True
            phase = 'storage-create'
            storage.create_volume(job)
            phase = 'authorization'
            self._require(None, 'execute', request)
            phase = 'execution'
            result = run_stage(image, job, 'diagnose', cancel=cancel, runtime=runtime, timeout=35)
            self.jobs.record_stage(job, 'diagnose', result)
            if result['status'] == 'succeeded' and not result.get('truncated', {}).get('stdout'):
                phase = 'output-validation'
                report = validate_diagnostic(result.get('stdout'), runtime)
                required = ('python', 'pip', 'venv', 'scratch') if runtime == 'python' else ('node', 'npm', 'scratch')
                codes = [report['checks'][name]['code'] for name in required]
                code = next((value for value in ('missing', 'incompatible', 'unavailable', 'unsupported') if value in codes), 'ready')
            else:
                failure = {'phase': 'execution', 'code': 'probe-failed'}
        except PermissionError as error:
            code = 'denied' if phase == 'authorization' else 'unavailable'
            failure = diagnostic_failure(error, phase)
            self.jobs.controller_failure(job, 'diagnostic access unavailable',
                state='unconfirmed' if reservation_attempted else 'failed')
        except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
            failure = diagnostic_failure(error, phase)
            steps = self.jobs.observe(job)['steps']
            stopped = bool(steps and steps[-1]['status'] in ('succeeded', 'failed', 'cancelled', 'timed_out'))
            readonly_refusal = phase == 'storage-reservation' and isinstance(error, StorageAdmissionError)
            self.jobs.controller_failure(job, 'diagnostic execution unavailable',
                state='failed' if stopped or not reservation_attempted or readonly_refusal else 'unconfirmed')
        finally:
            state = self.jobs.observe(job)['state']
            # A successful diagnostic stage is terminal, unlike the build pipeline.
            steps = self.jobs.observe(job)['steps']
            known_terminal = state in ('succeeded', 'failed', 'cancelled') or (
                state == 'running' and len(steps) == 1 and steps[0]['status'] == 'succeeded')
            warnings = []
            cleanup = 'not-started'
            if resources_started:
                warnings = self._cleanup(job, image=image, runtime=runtime) if known_terminal else [
                    'Unconfirmed execution: bounded storage remains reserved.']
                cleanup = 'unconfirmed' if warnings else 'confirmed'
            elif reservation_attempted and state == 'unconfirmed':
                # A write can fail after committing the reservation. Neither
                # absence of a returned value nor zero stages proves no effect.
                cleanup = 'unconfirmed'
            output = diagnostic_output(runtime, report=report, code=code, cleanup=cleanup,
                                       scratch_bytes=storage.job_bytes, failure=failure)
            self.jobs.diagnostic_result(job, output)
            self.jobs.cleanup_warnings(job, warnings)

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
        warnings = self._cleanup(job, image=request['image'], runtime=request.get('runtime', 'npm'))
        output = self.jobs.observe(job).get('output') or {}
        if request.get('kind') == 'diagnostic' and output.get('kind') == 'ods-project-diagnostic':
            updated = {**output, **diagnostic_output(request['runtime'], report=output,
                       code=output['code'], cleanup='unconfirmed' if warnings else 'confirmed',
                       scratch_bytes=output['scratchLimitBytes'])}
            if not warnings:
                updated.pop('cleanupWarnings', None)
            self.jobs.diagnostic_result(job, updated)
        self.jobs.cleanup_warnings(job, warnings)

    def _recover_cancel(self, job, request):
        # Recheck policy after queueing; stored job metadata grants no authority.
        try:
            self._require(request["project"], "cancel", request)
        except PermissionError:
            self.jobs.recovery_result(job, {"status": "unconfirmed", "evidence": "authorization-denied"})
            raise
        row = self.jobs.observe(job)
        stages = ('diagnose',) if request.get('kind') == 'diagnostic' else ("acquire", "test", "build")
        expected = stages[len(row["steps"])] if len(row["steps"]) < len(stages) else None
        if row['steps'] and row['steps'][-1]['status'] == 'unconfirmed':
            expected = row['steps'][-1]['stage']
        evidence = recover_job(request["image"], job, cancel=True, required_stage=expected, timeout=10, runtime=request.get("runtime", "npm"))
        self.jobs.recovery_result(job, evidence)
        if evidence.get('status') == 'cancelled':
            # Tmpfs contents are ephemeral; retain receipts, not reserved RAM.
            self._retry_cleanup(job, request)

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
            self.storage.reserve(image, job)
            resources_started = True
            self.storage.create_volume(job)
            start_keeper(image, job, runtime=runtime)
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
        except ReadOnlyPreflightError:
            if resources_started:
                self.jobs.controller_failure(job, 'execution outcome requires recovery')
            else:
                self.jobs.preflight_failed(job)
        except Exception as error:
            self.jobs.controller_failure(job, error)
        finally:
            if resources_started:
                if self.jobs.observe(job)['state'] in ('succeeded', 'failed', 'cancelled'):
                    self.jobs.cleanup_warnings(job, self._cleanup(job, image=image, runtime=runtime))
                else:
                    # A timed-out Docker CLI can still create its container.
                    # Removing the volume now could make that late run create
                    # an ordinary unbounded volume with the same name.
                    self.jobs.cleanup_warnings(job, ['Unconfirmed execution: bounded storage remains reserved.'])

    def _cleanup(self, job, *, image=None, runtime='npm'):
        image = self.image if image is None else image
        warnings = []
        for stage in ("seed-manifests", "seed-source", "acquire", "test", "build", "keeper", "diagnose"):
            try:
                name = job + "-" + stage
                result = subprocess.run(["docker", "inspect", name], capture_output=True, timeout=10)
                if result.returncode != 0:
                    continue
                container = json.loads(result.stdout)[0]
                identity = container.get('Id')
                evidence = observe_stage(image, job, stage, container_id=identity, runtime=runtime)
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
        self._capability_stop.set()
        try:
            if self._capability_thread is not None:
                self._capability_thread.join(timeout=10)
                if self._capability_thread.is_alive():
                    raise RuntimeError('capability probe shutdown unconfirmed')
        finally:
            self.pool.shutdown(wait=True)
