"""Real child processes through PopenLauncher: start, health, crash, restart, stop.

Runs on Linux and Windows. On Windows the real Job Object is used and the test also
asserts that every child is a member of it and that closing the job kills a child;
off Windows a recording fake stands in and only those assertions are skipped.
"""
import concurrent.futures
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from supervisor.plan import LaunchPlan, RestartPolicy, ServicePlan
from supervisor.runner import PopenLauncher, RestartCapExceeded, Supervisor, SystemClock, WindowsJob, http_probe

CHILD = str(Path(__file__).resolve().parent / "fixtures" / "health_child.py")
ON_WINDOWS = os.name == "nt"


class RecordingJob:
    def __init__(self):
        self.assigned = []

    def assign(self, pid):
        self.assigned.append(pid)

    def close(self):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_plan(tmp_path, mode, max_restarts=2):
    for sub in ("logs", "run"):
        (tmp_path / sub).mkdir()
    port = free_port()
    service = ServicePlan(
        name="child", argv=(sys.executable, CHILD, str(port), mode, str(tmp_path / "crashed.marker")),
        cwd=str(tmp_path), port=port, health_url=f"http://127.0.0.1:{port}/health",
        health_timeout_seconds=20, log_path=str(tmp_path / "logs" / "child.log"),
        env={"CHILD_ENV": "1"},
    )
    return LaunchPlan(
        services=(service,), install_root=str(tmp_path), state_root=str(tmp_path),
        log_dir=str(tmp_path / "logs"), run_dir=str(tmp_path / "run"),
        record_path=str(tmp_path / "run" / "supervisor.json"), stop_file=str(tmp_path / "run" / "supervisor.stop"),
        supervisor_log=str(tmp_path / "logs" / "supervisor.log"),
        restart=RestartPolicy(delay_seconds=0.2, max_restarts=max_restarts, window_seconds=60),
        log_max_bytes=64 * 1024, log_backups=1,
        poll_interval_seconds=0.1, health_interval_seconds=0.1, stop_timeout_seconds=10,
    )


def make_job():
    return WindowsJob() if ON_WINDOWS else RecordingJob()


def read_record(plan):
    with open(plan.record_path, encoding="utf-8") as handle:
        return json.load(handle)


def wait_for(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(f"condition not met within {timeout}s")


def restarted_and_running(plan):
    if not os.path.exists(plan.record_path):
        return False
    rec = read_record(plan)
    return rec["status"] == "running" and rec["services"]["child"]["restarts"] == 1


def test_start_crash_restart_stop_with_real_processes(tmp_path):
    plan = make_plan(tmp_path, "crash-once")
    job = make_job()
    sup = Supervisor(plan, launcher=PopenLauncher(plan.log_max_bytes, plan.log_backups),
                     job=job, probe=http_probe, clock=SystemClock())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(sup.run)
        wait_for(lambda: future.done() or restarted_and_running(plan))
        assert not future.done(), future.result()

        rec = read_record(plan)["services"]["child"]
        assert rec["last_exit_code"] == 3
        assert http_probe(plan.services[0].health_url)
        child_pid = rec["pid"]
        if ON_WINDOWS:
            assert job.contains(child_pid)
        else:
            assert len(job.assigned) == 2 and job.assigned[-1] == child_pid

        Path(plan.stop_file).touch()
        assert future.result(timeout=30) == 0

    rec = read_record(plan)
    assert rec["status"] == "stopped" and rec["services"]["child"]["pid"] is None
    assert not http_probe(plan.services[0].health_url)
    assert not os.path.exists(plan.stop_file)
    output = Path(plan.services[0].log_path).read_text(encoding="utf-8")
    assert output.count("supervisor starting child") == 2
    assert "health_child will crash" in output
    assert output.count("health_child serving") == 2
    job.close()


def test_child_that_always_exits_hits_restart_cap(tmp_path):
    plan = make_plan(tmp_path, "exit", max_restarts=1)
    job = make_job()
    sup = Supervisor(plan, launcher=PopenLauncher(plan.log_max_bytes, plan.log_backups),
                     job=job, probe=http_probe, clock=SystemClock())

    with pytest.raises(RestartCapExceeded, match="child exited 2 times .*last exit code 2"):
        sup.run()

    assert read_record(plan)["status"] == "failed"
    assert Path(plan.services[0].log_path).read_text(encoding="utf-8").count("mode=exit") == 2
    job.close()


@pytest.mark.skipif(not ON_WINDOWS, reason="Job Objects exist only on Windows; runs in the windows-native CI job")
def test_closing_job_kills_member_process():
    job = WindowsJob()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    job.assign(child.pid)
    assert job.contains(child.pid)
    job.close()
    assert child.wait(timeout=15) != 0
