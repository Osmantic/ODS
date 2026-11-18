# ============================================================================
# ODS Windows-native supervisor — runner (imperative shell)
# ============================================================================
# Part of: installers/windows/native/supervisor/
# Purpose: Start the services of a LaunchPlan, keep them in a Windows Job
#          Object (kill-on-close), pipe their output into size-capped logs,
#          wait for health, write run\supervisor.json, restart crashed
#          children within the restart policy, and stop cleanly on request.
#
# Expects: A LaunchPlan from plan.build_plan().
# Provides: Supervisor(plan, launcher, job, probe, clock).run() -> 0 on a
#             requested stop; raises RestartCapExceeded / HealthTimeout
#             (Let It Crash: Task Scheduler restarts the supervisor).
#           WindowsJob, JobObjectUnavailable, PopenLauncher, RotatingLog,
#           SystemClock, http_probe, write_json_atomic, read_secret
#
# Modder notes:
#   Every collaborator (launcher, job, probe, clock) is injected so the logic
#   runs on Linux with fakes. Only WindowsJob needs Windows; it raises
#   JobObjectUnavailable elsewhere. Children are assigned to the job right
#   after they start; anything they spawn later inherits the job.
# ============================================================================
from __future__ import annotations

import ctypes
import http.client
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .plan import recent_crashes, restart_allowed

log = logging.getLogger("ods.supervisor")

RECORD_SCHEMA_VERSION = 1


class RestartCapExceeded(RuntimeError):
    """A service crashed more often than the restart policy allows."""


class HealthTimeout(RuntimeError):
    """A running service did not become healthy in time."""


class JobObjectUnavailable(RuntimeError):
    """Windows Job Objects are not available on this platform."""


class _StopRequested(Exception):
    """Internal: a stop was requested while waiting; unwinds to Supervisor.run()."""


# ---------------------------------------------------------------- small I/O helpers

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json_atomic(path, data):
    """Write JSON to a temp file next to `path`, fsync it, then os.replace() it into place."""
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_secret(path):
    """Read a secret file; an empty file is an install error, not an empty key."""
    with open(path, encoding="utf-8") as handle:
        value = handle.read().strip()
    if not value:
        raise ValueError(f"secret file {path} is empty")
    return value


def http_probe(url, timeout=2.0):
    """True when GET url answers 200. Connection-level failures mean 'not healthy yet'."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError, http.client.HTTPException):
        # URLError covers refused connections and HTTPError (e.g. 503 while llama loads).
        return False


class SystemClock:
    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)

    def now_iso(self):
        return utc_now_iso()


# ---------------------------------------------------------------- logs

class RotatingLog:
    """Append-only byte log that rotates to .1 .. .N when the next write would pass max_bytes."""

    def __init__(self, path, max_bytes, backups):
        self.path, self.max_bytes, self.backups = path, max_bytes, backups
        self._handle = open(path, "ab")

    def write(self, data):
        size = self._handle.tell()
        if size > 0 and size + len(data) > self.max_bytes:
            self._rotate()
        self._handle.write(data)
        self._handle.flush()

    def _rotate(self):
        self._handle.close()
        if self.backups == 0:
            self._handle = open(self.path, "wb")
            return
        for index in range(self.backups - 1, 0, -1):
            older = f"{self.path}.{index}"
            if os.path.exists(older):
                os.replace(older, f"{self.path}.{index + 1}")
        os.replace(self.path, f"{self.path}.1")
        self._handle = open(self.path, "ab")

    def close(self):
        self._handle.close()


def _pump(stream, sink):
    """Copy a child's output into its log until EOF (the child and its descendants exited)."""
    try:
        while True:
            chunk = stream.read1(65536)
            if not chunk:
                return
            sink.write(chunk)
    finally:
        stream.close()
        sink.close()


# ---------------------------------------------------------------- processes

class ChildProcess:
    """A started service: the Popen plus the thread that copies its output into the log."""

    def __init__(self, popen, pump_thread):
        self._popen, self._pump_thread = popen, pump_thread
        self.pid = popen.pid

    @property
    def returncode(self):
        return self._popen.returncode

    def poll(self):
        return self._popen.poll()

    def terminate(self):
        self._popen.terminate()

    def kill(self):
        self._popen.kill()

    def wait(self, timeout=None):
        code = self._popen.wait(timeout=timeout)
        # A grandchild that inherited the pipe can hold it open; do not hang on it.
        self._pump_thread.join(timeout=5)
        return code


class PopenLauncher:
    """Start a service with subprocess.Popen: no shell, no console window, output to a rotating log."""

    def __init__(self, log_max_bytes, log_backups):
        self.log_max_bytes, self.log_backups = log_max_bytes, log_backups

    def launch(self, service, env):
        sink = RotatingLog(service.log_path, self.log_max_bytes, self.log_backups)
        sink.write(f"\n===== {utc_now_iso()} supervisor starting {service.name} =====\n".encode("utf-8"))
        # creationflags are Windows-only; POSIX Popen rejects non-zero values.
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        popen = subprocess.Popen(
            list(service.argv), cwd=service.cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False, creationflags=flags,
        )
        thread = threading.Thread(target=_pump, args=(popen.stdout, sink), name=f"log-{service.name}", daemon=True)
        thread.start()
        return ChildProcess(popen, thread)


# ---------------------------------------------------------------- Windows Job Object

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WindowsJob:
    """A Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: closing it (or the supervisor dying) kills every member."""

    def __init__(self):
        if os.name != "nt":
            raise JobObjectUnavailable(f"Windows Job Objects need Windows; this platform is {sys.platform}")
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        k32.IsProcessInJob.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        self._k32, self._wintypes = k32, wintypes

        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                           ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())

    def _open(self, pid, access):
        process = self._k32.OpenProcess(access, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        return process

    def assign(self, pid):
        process = self._open(pid, _PROCESS_SET_QUOTA | _PROCESS_TERMINATE)
        try:
            if not self._k32.AssignProcessToJobObject(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._k32.CloseHandle(process)

    def contains(self, pid):
        process = self._open(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
        try:
            result = self._wintypes.BOOL()
            if not self._k32.IsProcessInJob(process, self._handle, ctypes.byref(result)):
                raise ctypes.WinError(ctypes.get_last_error())
            return bool(result.value)
        finally:
            self._k32.CloseHandle(process)

    def close(self):
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = None


# ---------------------------------------------------------------- supervisor

class Supervisor:
    def __init__(self, plan, *, launcher, job, probe, clock, base_env=None, secret_reader=read_secret):
        self.plan, self.launcher, self.job, self.probe, self.clock = plan, launcher, job, probe, clock
        self.base_env = dict(os.environ if base_env is None else base_env)
        self.secret_reader = secret_reader
        self._stop = threading.Event()
        self._procs = {}
        self._state = {s.name: {"restarts": 0, "crashes": (), "pid": None, "started_at": None,
                                "last_exit_code": None} for s in plan.services}
        self._started_at = clock.now_iso()

    # -- stop handling
    def request_stop(self):
        self._stop.set()

    def stop_requested(self):
        return self._stop.is_set() or os.path.exists(self.plan.stop_file)

    def _sleep(self, seconds):
        """Sleep, then unwind if a stop arrived meanwhile."""
        self.clock.sleep(seconds)
        if self.stop_requested():
            raise _StopRequested()

    # -- run record
    def _write_record(self, status):
        services = {}
        for svc in self.plan.services:
            st = self._state[svc.name]
            services[svc.name] = {
                "pid": st["pid"], "port": svc.port, "health_url": svc.health_url, "log_path": svc.log_path,
                "started_at": st["started_at"], "restarts": st["restarts"], "last_exit_code": st["last_exit_code"],
            }
        write_json_atomic(self.plan.record_path, {
            "schema_version": RECORD_SCHEMA_VERSION, "status": status, "supervisor_pid": os.getpid(),
            "started_at": self._started_at, "updated_at": self.clock.now_iso(), "services": services,
        })

    # -- one service
    def _child_env(self, svc):
        env = dict(self.base_env)
        env.update(svc.env)
        for var, path in svc.secret_env.items():
            env[var] = self.secret_reader(path)
        return env

    def _launch(self, svc):
        log.info("starting %s: %s", svc.name, " ".join(svc.argv))
        proc = self.launcher.launch(svc, self._child_env(svc))
        self._procs[svc.name] = proc
        self.job.assign(proc.pid)
        self._state[svc.name].update(pid=proc.pid, started_at=self.clock.now_iso())
        return proc

    def _wait_healthy(self, svc, proc):
        """True once healthy; False if the process exited first. Raises HealthTimeout."""
        deadline = self.clock.monotonic() + svc.health_timeout_seconds
        while True:
            if self.stop_requested():
                raise _StopRequested()
            if proc.poll() is not None:
                return False
            if self.probe(svc.health_url):
                log.info("%s is healthy at %s (pid %s)", svc.name, svc.health_url, proc.pid)
                return True
            if self.clock.monotonic() >= deadline:
                raise HealthTimeout(f"{svc.name} did not answer {svc.health_url} within "
                                    f"{svc.health_timeout_seconds:g}s; see {svc.log_path}")
            self.clock.sleep(self.plan.health_interval_seconds)

    def _record_crash(self, svc, exit_code):
        st = self._state[svc.name]
        now = self.clock.monotonic()
        policy = self.plan.restart
        st["crashes"] = recent_crashes(st["crashes"] + (now,), now, policy)
        st["last_exit_code"] = exit_code
        st["pid"] = None
        log.warning("%s exited with code %s", svc.name, exit_code)
        if not restart_allowed(st["crashes"], now, policy):
            raise RestartCapExceeded(
                f"{svc.name} exited {len(st['crashes'])} times within {policy.window_seconds:g}s "
                f"(limit {policy.max_restarts} restarts, last exit code {exit_code}); see {svc.log_path}")

    def _bring_up(self, svc):
        while True:
            proc = self._launch(svc)
            self._write_record("starting")
            if self._wait_healthy(svc, proc):
                return
            self._record_crash(svc, proc.returncode)
            self._write_record("restarting")
            self._sleep(self.plan.restart.delay_seconds)
            self._state[svc.name]["restarts"] += 1

    def _check_children(self):
        for svc in self.plan.services:
            proc = self._procs[svc.name]
            code = proc.poll()
            if code is None:
                continue
            self._record_crash(svc, code)
            self._write_record("restarting")
            self._sleep(self.plan.restart.delay_seconds)
            self._state[svc.name]["restarts"] += 1
            self._bring_up(svc)
            self._write_record("running")

    def _stop_all(self):
        for svc in reversed(self.plan.services):
            proc = self._procs.get(svc.name)
            if proc is None or proc.poll() is not None:
                continue
            log.info("stopping %s (pid %s)", svc.name, proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=self.plan.stop_timeout_seconds)
            except subprocess.TimeoutExpired:
                log.warning("%s did not exit in %ss; killing it", svc.name, self.plan.stop_timeout_seconds)
                proc.kill()
                proc.wait(timeout=self.plan.stop_timeout_seconds)
            self._state[svc.name]["last_exit_code"] = proc.returncode
            self._state[svc.name]["pid"] = None

    # -- main loop
    def run(self):
        """Supervise until a stop is requested (returns 0). Fatal conditions raise."""
        if os.path.exists(self.plan.stop_file):
            # A stop file left by the previous run must not stop this one.
            os.remove(self.plan.stop_file)
        status = "failed"
        try:
            for svc in self.plan.services:
                self._bring_up(svc)
            self._write_record("running")
            log.info("all services healthy")
            while True:
                self._sleep(self.plan.poll_interval_seconds)
                self._check_children()
        except _StopRequested:
            log.info("stop requested")
            status = "stopped"
            return 0
        finally:
            self._stop_all()
            self._write_record(status)
            if os.path.exists(self.plan.stop_file):
                os.remove(self.plan.stop_file)
