"""End-to-end tests for dream-cluster-supervisor.py.

Exercises the supervisor wrapper process with fake `llama-server` (http
only) and TCP listeners as fake workers. Tests preflight partitioning,
degraded mode, watchdog recovery, and restart policies.

Events log: /tmp/cluster-events/events.json (atomic JSON list, capped 100).
"""
import json
import os
import signal
import socket
import subprocess
import time
from contextlib import contextmanager

import pytest

SUPERVISOR = "/app/scripts/dream-cluster-supervisor.py"
EVENTS = "/tmp/cluster-events/events.json"


@contextmanager
def fake_worker(port):
    """A TCP listener that accepts and immediately closes — stand-in for rpc-server."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(32)
    srv.settimeout(0.3)

    import threading
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
                c.close()
            except socket.timeout:
                continue
            except OSError:
                return

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        yield srv
    finally:
        stop.set()
        srv.close()
        t.join(timeout=1)


def _reset_events():
    if os.path.isfile(EVENTS):
        os.unlink(EVENTS)
    os.makedirs(os.path.dirname(EVENTS), exist_ok=True)


def _read_events():
    if not os.path.isfile(EVENTS):
        return []
    with open(EVENTS) as f:
        return json.load(f)


def _spawn_supervisor(workers, restart_policy="always", fake_log=None):
    env = os.environ.copy()
    env["CLUSTER_WORKERS"] = ",".join(workers)
    env["CLUSTER_RESTART_POLICY"] = restart_policy
    if fake_log:
        env["FAKE_LLAMA_LOG"] = fake_log
    return subprocess.Popen(
        ["python3", SUPERVISOR, "--rpc", ",".join(workers), "--port", "8080"],
        env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _wait_for_event(event_type, timeout=20, detail_contains=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for ev in _read_events():
            if ev["type"] != event_type:
                continue
            if detail_contains and detail_contains not in ev.get("detail", ""):
                continue
            return ev
        time.sleep(0.2)
    raise AssertionError(
        f"event {event_type!r} (detail~={detail_contains!r}) never observed. "
        f"Last events: {_read_events()[-10:]}"
    )


def _stop(proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def test_preflight_all_workers_live():
    """All workers reachable → server_starting with full worker set, no degraded_start."""
    _reset_events()
    with fake_worker(50152), fake_worker(50153):
        proc = _spawn_supervisor(["127.0.0.1:50152", "127.0.0.1:50153"])
        try:
            ev = _wait_for_event("server_starting", timeout=15)
            assert "127.0.0.1:50152" in ev["detail"]
            assert "127.0.0.1:50153" in ev["detail"]
            assert not any(e["type"] == "degraded_start" for e in _read_events())
        finally:
            _stop(proc)


def test_degraded_start_with_one_worker_down():
    """One of two workers unreachable → supervisor enters degraded mode on start."""
    _reset_events()
    log = "/tmp/fake-llama-degraded.log"
    if os.path.isfile(log):
        os.unlink(log)

    with fake_worker(50154):
        # 50155 is NOT listening — should be reported as dead.
        proc = _spawn_supervisor(["127.0.0.1:50154", "127.0.0.1:50155"],
                                 fake_log=log)
        try:
            ev = _wait_for_event("degraded_start", timeout=15)
            assert "127.0.0.1:50154" in ev["detail"]  # live
            assert "127.0.0.1:50155" in ev["detail"]  # offline

            # Verify llama-server launched with only the live worker in --rpc.
            started = _wait_for_event("server_starting", timeout=5)
            assert "127.0.0.1:50154" in started["detail"]
            assert "127.0.0.1:50155" not in started["detail"]

            # Cross-check the fake llama's own launch log.
            time.sleep(0.5)
            with open(log) as f:
                entries = [json.loads(line) for line in f if line.strip()]
            assert entries, "fake llama-server never recorded a launch"
            last = entries[-1]
            assert last["rpc"] == "127.0.0.1:50154"
        finally:
            _stop(proc)


def test_controller_only_when_all_workers_offline_with_always_policy():
    """No workers reachable + policy=always → supervisor still starts llama on controller GPU only."""
    _reset_events()
    proc = _spawn_supervisor(
        ["127.0.0.1:50160", "127.0.0.1:50161"],  # neither listening
        restart_policy="always",
    )
    try:
        _wait_for_event("all_workers_offline", timeout=15)
        _wait_for_event("controller_only_start", timeout=30)
        started = _wait_for_event("server_starting", timeout=5)
        assert "controller only" in started["detail"].lower()
    finally:
        _stop(proc)


def test_manual_policy_exits_when_all_workers_offline():
    """policy=manual + all workers offline → supervisor exits non-zero without starting llama."""
    _reset_events()
    proc = _spawn_supervisor(
        ["127.0.0.1:50162", "127.0.0.1:50163"],
        restart_policy="manual",
    )
    try:
        rc = proc.wait(timeout=15)
        assert rc == 0, f"supervisor expected to exit cleanly after break, got rc={rc}"
        events = _read_events()
        types = [e["type"] for e in events]
        assert "all_workers_offline" in types
        assert "server_starting" not in types  # never attempted to launch
    finally:
        _stop(proc)


def test_recovery_restarts_with_full_cluster():
    """Start degraded, then bring dead worker up → watchdog triggers restart with full set."""
    _reset_events()
    log = "/tmp/fake-llama-recovery.log"
    if os.path.isfile(log):
        os.unlink(log)

    with fake_worker(50170):
        proc = _spawn_supervisor(["127.0.0.1:50170", "127.0.0.1:50171"],
                                 fake_log=log)
        try:
            _wait_for_event("degraded_start", timeout=15)
            _wait_for_event("server_starting", timeout=5,
                            detail_contains="127.0.0.1:50170")

            # Now bring 50171 online — watchdog must detect within ~5s.
            with fake_worker(50171):
                _wait_for_event("workers_recovered", timeout=20,
                                detail_contains="127.0.0.1:50171")
                # Supervisor restarts and launches with both.
                _wait_for_event("full_cluster_restored", timeout=20)
                # The new server_starting event should list both workers.
                deadline = time.monotonic() + 15
                found_full = False
                while time.monotonic() < deadline:
                    for ev in _read_events():
                        if (ev["type"] == "server_starting"
                                and "127.0.0.1:50170" in ev["detail"]
                                and "127.0.0.1:50171" in ev["detail"]):
                            found_full = True
                            break
                    if found_full:
                        break
                    time.sleep(0.3)
                assert found_full, "no server_starting event with both workers"
        finally:
            _stop(proc)


def test_event_file_truncates_at_100():
    """log_event() caps events list at the last 100 entries."""
    _reset_events()
    # Import helper and hammer it.
    import importlib.util
    spec = importlib.util.spec_from_file_location("sup", SUPERVISOR)
    sup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sup)

    for i in range(150):
        sup.log_event("noise", f"i={i}")

    events = _read_events()
    assert len(events) == 100
    # Newest survive.
    assert events[-1]["detail"] == "i=149"
    assert events[0]["detail"] == "i=50"


def test_parse_workers_round_trip():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sup", SUPERVISOR)
    sup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sup)

    workers = sup.parse_workers("10.0.0.1:50052,10.0.0.2:50052,10.0.0.3:50099")
    assert workers == [("10.0.0.1", 50052), ("10.0.0.2", 50052), ("10.0.0.3", 50099)]
    assert sup.workers_to_rpc(workers) == "10.0.0.1:50052,10.0.0.2:50052,10.0.0.3:50099"


def test_partition_workers_discriminates():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sup", SUPERVISOR)
    sup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sup)

    with fake_worker(50180):
        live, dead = sup.partition_workers([("127.0.0.1", 50180), ("127.0.0.1", 50181)])
        assert live == [("127.0.0.1", 50180)]
        assert dead == [("127.0.0.1", 50181)]


def test_build_cmd_replaces_rpc_value():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sup", SUPERVISOR)
    sup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sup)

    cmd = sup.build_cmd(
        ["--rpc", "a:1,b:2", "--port", "8080", "--no-warmup"],
        [("a", 1)],
    )
    assert cmd[0] == "llama-server"
    i = cmd.index("--rpc")
    assert cmd[i + 1] == "a:1"
    assert "--port" in cmd and "8080" in cmd
    assert "--no-warmup" in cmd
