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


# ---------------------------------------------------------------------------
# H8 fault-tolerance coverage: cases the existing tests don't cover.
# ---------------------------------------------------------------------------

def _read_fake_llama_entries(log_path):
    """Return parsed entries from FAKE_LLAMA_LOG. Empty list if none yet."""
    if not os.path.isfile(log_path):
        return []
    with open(log_path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_sigterm_to_supervisor_kills_llama_child():
    """SIGTERM to the supervisor must terminate the fake llama-server child.

    Regression guard for H1: the flag-based _handle_term pattern must still
    forward the signal (via proc.terminate()) to the child, otherwise
    systemd's stop sends SIGKILL after the grace period and operators see
    an orphaned llama-server on restart.
    """
    _reset_events()
    log = "/tmp/fake-llama-sigterm.log"
    if os.path.isfile(log):
        os.unlink(log)

    with fake_worker(50190):
        proc = _spawn_supervisor(["127.0.0.1:50190"], fake_log=log)
        try:
            _wait_for_event("server_starting", timeout=15)
            # Give fake-llama a beat to record its PID in the log.
            deadline = time.monotonic() + 10
            entries = []
            while time.monotonic() < deadline and not entries:
                entries = _read_fake_llama_entries(log)
                if entries:
                    break
                time.sleep(0.2)
            assert entries, "fake llama-server never recorded its launch"
            child_pid = entries[-1]["pid"]
            assert _pid_alive(child_pid), f"fake-llama PID {child_pid} already dead before SIGTERM"

            proc.send_signal(signal.SIGTERM)
            # Supervisor should drain cleanly within the run_server shutdown
            # path — no need for the _stop() helper's fallback kill.
            rc = proc.wait(timeout=10)
            assert rc == 0, f"supervisor exited with {rc} on SIGTERM"

            # Child must also be gone; give the kernel a tick to reap it.
            for _ in range(50):
                if not _pid_alive(child_pid):
                    break
                time.sleep(0.1)
            assert not _pid_alive(child_pid), (
                f"fake-llama PID {child_pid} still alive after supervisor exit — "
                "SIGTERM was not forwarded to the child"
            )
        finally:
            _stop(proc)


def test_live_worker_dying_mid_run_triggers_degraded_restart():
    """Worker that was live at preflight goes offline while llama is running.

    Real llama.cpp aborts on RPC loss (exit 134 via SIGABRT), which
    triggers the supervisor's restart loop. We simulate that by SIGKILL'ing
    the fake llama-server child (exit -9 → non-zero, same restart path)
    while one of the two workers is offline — the supervisor's main loop
    should re-run preflight and relaunch in degraded mode with only the
    surviving worker.

    Note: SIGTERM would give the fake child exit 0 (clean shutdown), which
    the supervisor interprets as "llama exited cleanly" and stops — the
    wrong path. SIGKILL reproduces the crash-then-restart sequence.
    """
    _reset_events()
    log = "/tmp/fake-llama-mid-die.log"
    if os.path.isfile(log):
        os.unlink(log)

    # Use a plain thread so we can close the listener mid-test.
    import threading
    srv2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv2.bind(("127.0.0.1", 50192))
    srv2.listen(32)
    srv2.settimeout(0.3)
    stop2 = threading.Event()

    def loop():
        while not stop2.is_set():
            try:
                c, _ = srv2.accept()
                c.close()
            except socket.timeout:
                continue
            except OSError:
                return

    t = threading.Thread(target=loop, daemon=True)
    t.start()

    with fake_worker(50191):
        proc = _spawn_supervisor(["127.0.0.1:50191", "127.0.0.1:50192"],
                                 fake_log=log)
        try:
            first = _wait_for_event("server_starting", timeout=15)
            assert "127.0.0.1:50191" in first["detail"]
            assert "127.0.0.1:50192" in first["detail"]

            # Read the first fake-llama PID from the log.
            deadline = time.monotonic() + 10
            entries = []
            while time.monotonic() < deadline:
                entries = _read_fake_llama_entries(log)
                if entries:
                    break
                time.sleep(0.2)
            assert entries, "fake llama-server never recorded its launch"
            first_child_pid = entries[-1]["pid"]
            initial_event_count = len(_read_events())

            # Worker 50192 "dies" — stop accepting TCP.
            stop2.set()
            srv2.close()
            t.join(timeout=2)
            # Give the kernel a moment to release the port before preflight reruns.
            time.sleep(0.5)

            # Simulate llama.cpp aborting on RPC loss (SIGKILL → exit -9,
            # non-zero exit path → supervisor restarts).
            os.kill(first_child_pid, signal.SIGKILL)

            # Degraded restart event must mention 50192 as the offline worker.
            degraded = _wait_for_event("degraded_start", timeout=30,
                                       detail_contains="127.0.0.1:50192")
            assert "127.0.0.1:50191" in degraded["detail"]
            assert "offline" in degraded["detail"].lower()

            # Find the relaunch server_starting — must be an event posted
            # AFTER the initial one and must not list 50192.
            deadline = time.monotonic() + 20
            relaunch = None
            while time.monotonic() < deadline:
                evs = _read_events()
                for ev in evs[initial_event_count:]:
                    if ev["type"] != "server_starting":
                        continue
                    if "127.0.0.1:50192" in ev["detail"]:
                        continue
                    if "127.0.0.1:50191" in ev["detail"]:
                        relaunch = ev
                        break
                if relaunch:
                    break
                time.sleep(0.3)
            assert relaunch, (
                "supervisor never logged a relaunch server_starting with "
                "only 127.0.0.1:50191. Last events: "
                f"{_read_events()[-10:]}"
            )

            # And the new llama-server must be a fresh PID. server_starting
            # is logged just before subprocess.Popen() — give the new child
            # a beat to actually exec and write its startup entry.
            deadline = time.monotonic() + 10
            entries = _read_fake_llama_entries(log)
            while time.monotonic() < deadline and len(entries) < 2:
                time.sleep(0.2)
                entries = _read_fake_llama_entries(log)
            assert len(entries) >= 2, (
                f"expected at least two fake-llama launches, got {len(entries)}: "
                f"{entries}"
            )
            new_pid = entries[-1]["pid"]
            assert new_pid != first_child_pid, (
                "supervisor did not spawn a new llama-server after child death"
            )
        finally:
            stop2.set()
            try:
                srv2.close()
            except OSError:
                pass
            _stop(proc)
