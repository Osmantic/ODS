"""Supervisor logic with a fake launcher, fake job, fake probe and fake clock (runs on any OS)."""
import http.server
import json
import os
import socket
import threading

import pytest

from supervisor.plan import build_plan
from supervisor.runner import (
    HealthTimeout, JobObjectUnavailable, RestartCapExceeded, RotatingLog, Supervisor, WindowsJob,
    http_probe, read_secret, write_json_atomic,
)


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.on_sleep = []  # callables(t) run after each sleep

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds
        for hook in self.on_sleep:
            hook(self.t)

    def now_iso(self):
        return f"t={self.t:g}"


class FakeProcess:
    """Becomes healthy `healthy_after` seconds after launch; exits with `code` at `exit_after` (None = never)."""

    def __init__(self, clock, pid, healthy_after=2, exit_after=None, code=1):
        self.clock, self.pid, self.code = clock, pid, code
        self.started = clock.t
        self.healthy_after, self.exit_after = healthy_after, exit_after
        self.returncode = None
        self.terminated = False

    def poll(self):
        if self.returncode is None and self.exit_after is not None and self.clock.t - self.started >= self.exit_after:
            self.returncode = self.code
        return self.returncode

    def healthy(self):
        return self.poll() is None and self.clock.t - self.started >= self.healthy_after

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    kill = terminate

    def wait(self, timeout=None):
        return self.returncode


class FakeLauncher:
    """behaviours: service name -> list of FakeProcess kwargs, one per launch (last one repeats)."""

    def __init__(self, clock, behaviours):
        self.clock, self.behaviours = clock, behaviours
        self.launches = []  # (name, env, proc)
        self.current = {}   # health_url -> proc
        self._pid = 1000

    def launch(self, service, env):
        options = self.behaviours.get(service.name, [{}])
        count = sum(1 for name, _, _ in self.launches if name == service.name)
        self._pid += 1
        proc = FakeProcess(self.clock, self._pid, **options[min(count, len(options) - 1)])
        self.launches.append((service.name, env, proc))
        self.current[service.health_url] = proc
        return proc

    def probe(self, url):
        return self.current[url].healthy()

    def procs(self, name):
        return [proc for n, _, proc in self.launches if n == name]


class FakeJob:
    def __init__(self):
        self.assigned = []

    def assign(self, pid):
        self.assigned.append(pid)


@pytest.fixture
def plan(tmp_path):
    state = tmp_path / "state"
    for sub in ("logs", "run", "secrets"):
        (state / sub).mkdir(parents=True)
    (state / "secrets" / "llama-server-api-key").write_text("llama-secret\n")
    (state / "secrets" / "dashboard-api-key").write_text("dash-secret\n")
    return build_plan({
        "schema_version": 1, "install_root": str(tmp_path / "app"), "state_root": str(state),
        "services": {
            "llama-server": {"port": 18080, "model_path": str(state / "models" / "m.gguf"), "health_timeout_seconds": 30},
            "dashboard-api": {"port": 13002, "health_timeout_seconds": 30},
        },
        "restart": {"delay_seconds": 5, "max_restarts": 2, "window_seconds": 100},
    })


def make(plan, behaviours=None):
    clock = FakeClock()
    launcher = FakeLauncher(clock, behaviours or {})
    job = FakeJob()
    sup = Supervisor(plan, launcher=launcher, job=job, probe=launcher.probe, clock=clock, base_env={"BASE": "1"})
    return sup, clock, launcher, job


def stop_at(sup, when):
    return lambda t: sup.request_stop() if t >= when else None


def record(plan):
    with open(plan.record_path, encoding="utf-8") as handle:
        return json.load(handle)


def test_starts_in_order_waits_for_health_and_stops_cleanly(plan):
    sup, clock, launcher, job = make(plan)
    seen = []
    clock.on_sleep.append(lambda t: seen.append((t, record(plan)["status"])) if t == 10 else None)
    clock.on_sleep.append(stop_at(sup, 20))

    assert sup.run() == 0

    assert [name for name, _, _ in launcher.launches] == ["llama-server", "dashboard-api"]
    llama_proc, dash_proc = launcher.procs("llama-server")[0], launcher.procs("dashboard-api")[0]
    assert dash_proc.started >= llama_proc.started + llama_proc.healthy_after  # waited for llama health
    assert job.assigned == [llama_proc.pid, dash_proc.pid]
    assert llama_proc.terminated and dash_proc.terminated
    assert seen == [(10, "running")]
    dash_env = launcher.launches[1][1]
    assert dash_env["BASE"] == "1" and dash_env["DASHBOARD_API_KEY"] == "dash-secret"
    assert dash_env["LLAMA_SERVER_API_KEY"] == "llama-secret"
    rec = record(plan)
    assert rec["status"] == "stopped"
    assert rec["services"]["llama-server"]["port"] == 18080
    assert "secret" not in json.dumps(rec)


def test_crashed_child_is_restarted_after_delay(plan):
    sup, clock, launcher, _ = make(plan, {"llama-server": [{"exit_after": 10}, {}]})
    clock.on_sleep.append(stop_at(sup, 60))

    assert sup.run() == 0

    first, second = launcher.procs("llama-server")
    assert first.returncode == 1
    assert second.started - (first.started + first.exit_after) >= plan.restart.delay_seconds
    assert len(launcher.procs("dashboard-api")) == 1
    rec = record(plan)
    assert rec["services"]["llama-server"]["restarts"] == 1
    assert rec["services"]["llama-server"]["last_exit_code"] == -15  # terminated at stop


def test_restart_cap_exceeded_raises_and_stops_everything(plan):
    sup, clock, launcher, _ = make(plan, {"dashboard-api": [{"exit_after": 3, "code": 7}]})

    with pytest.raises(RestartCapExceeded, match=r"dashboard-api exited 3 times within 100s .*last exit code 7"):
        sup.run()

    assert len(launcher.procs("dashboard-api")) == 3  # first start + max_restarts (2)
    assert launcher.procs("llama-server")[0].terminated
    rec = record(plan)
    assert rec["status"] == "failed"
    assert rec["services"]["dashboard-api"]["restarts"] == 2


def test_exit_before_healthy_counts_as_crash(plan):
    sup, _, launcher, _ = make(plan, {"llama-server": [{"healthy_after": 5, "exit_after": 1, "code": 9}]})

    with pytest.raises(RestartCapExceeded, match="llama-server exited 3 times"):
        sup.run()

    assert launcher.procs("dashboard-api") == []


def test_crashes_outside_window_do_not_accumulate(plan):
    # Crashes every ~50s; window is 100s and cap 2, so at most 2 crashes ever share a window.
    sup, clock, launcher, _ = make(plan, {"llama-server": [{"exit_after": 50}]})
    clock.on_sleep.append(stop_at(sup, 500))

    assert sup.run() == 0
    assert len(launcher.procs("llama-server")) >= 8


def test_health_timeout_raises(plan):
    sup, clock, launcher, _ = make(plan, {"llama-server": [{"healthy_after": 10_000}]})

    with pytest.raises(HealthTimeout, match="llama-server did not answer http://127.0.0.1:18080/health within 30s"):
        sup.run()

    assert 30 <= clock.t <= 32
    assert launcher.procs("llama-server")[0].terminated
    assert record(plan)["status"] == "failed"


def test_stop_file_stops_and_is_removed(plan):
    sup, clock, launcher, _ = make(plan)

    def touch_stop(t):
        if t >= 15:
            open(plan.stop_file, "w").close()
    clock.on_sleep.append(touch_stop)

    assert sup.run() == 0
    assert not os.path.exists(plan.stop_file)
    assert all(proc.terminated for _, _, proc in launcher.launches)


def test_stale_stop_file_does_not_block_start(plan):
    open(plan.stop_file, "w").close()
    sup, clock, launcher, _ = make(plan)
    clock.on_sleep.append(stop_at(sup, 20))

    assert sup.run() == 0
    assert len(launcher.launches) == 2


def test_stop_during_health_wait(plan):
    sup, clock, launcher, _ = make(plan, {"llama-server": [{"healthy_after": 1000}]})
    clock.on_sleep.append(stop_at(sup, 5))

    assert sup.run() == 0
    assert launcher.procs("llama-server")[0].terminated
    assert launcher.procs("dashboard-api") == []


def test_empty_secret_file_fails_launch(plan, tmp_path):
    with open(plan.services[1].secret_env["DASHBOARD_API_KEY"], "w") as handle:
        handle.write("  \n")
    sup, _, _, _ = make(plan)
    with pytest.raises(ValueError, match="is empty"):
        sup.run()


def test_write_json_atomic_replaces_without_leftovers(tmp_path):
    target = tmp_path / "supervisor.json"
    target.write_text("old")
    write_json_atomic(str(target), {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}
    assert os.listdir(tmp_path) == ["supervisor.json"]


def test_rotating_log_caps_size_and_backups(tmp_path):
    path = str(tmp_path / "svc.log")
    sink = RotatingLog(path, max_bytes=100, backups=2)
    for index in range(10):
        sink.write(f"{index}".encode() * 60)
    sink.close()
    assert sorted(os.listdir(tmp_path)) == ["svc.log", "svc.log.1", "svc.log.2"]
    assert open(path, "rb").read() == b"9" * 60
    assert open(path + ".1", "rb").read() == b"8" * 60
    assert open(path + ".2", "rb").read() == b"7" * 60


def test_rotating_log_without_backups_truncates(tmp_path):
    path = str(tmp_path / "svc.log")
    sink = RotatingLog(path, max_bytes=100, backups=0)
    sink.write(b"a" * 80)
    sink.write(b"b" * 80)
    sink.close()
    assert os.listdir(tmp_path) == ["svc.log"]
    assert open(path, "rb").read() == b"b" * 80


def test_rotating_log_appends_across_opens(tmp_path):
    path = str(tmp_path / "svc.log")
    for chunk in (b"one\n", b"two\n"):
        sink = RotatingLog(path, max_bytes=1000, backups=1)
        sink.write(chunk)
        sink.close()
    assert open(path, "rb").read() == b"one\ntwo\n"


def test_read_secret_strips_and_rejects_empty(tmp_path):
    (tmp_path / "k").write_text("abc\r\n")
    (tmp_path / "e").write_text("")
    assert read_secret(str(tmp_path / "k")) == "abc"
    with pytest.raises(ValueError):
        read_secret(str(tmp_path / "e"))


@pytest.mark.skipif(os.name == "nt", reason="the Windows path is covered by test_real_process.py")
def test_job_object_is_cleanly_unavailable_off_windows():
    with pytest.raises(JobObjectUnavailable, match="need Windows"):
        WindowsJob()


def test_http_probe_maps_status_and_refusal():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 503)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        assert http_probe(f"http://127.0.0.1:{port}/health") is True
        assert http_probe(f"http://127.0.0.1:{port}/loading") is False
    finally:
        server.shutdown()
        server.server_close()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    assert http_probe(f"http://127.0.0.1:{closed_port}/health") is False
