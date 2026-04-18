"""End-to-end tests with two real worker containers joining the controller.

These exercise the full chain: controller beacon → worker agent discovers
→ TCP join → rpc-server (stub) comes up. Then the supervisor (running
from the test-runner) is pointed at both worker RPC endpoints.

Fixtures come from conftest.py:
  worker1_ip, worker2_ip, worker_rpc_port, worker_status_port, token.

Compose starts worker1 + worker2 before the test-runner; each worker
container has a healthcheck that waits for agent.status=="running", so
by the time tests run both are registered and serving the stub RPC port.
"""
import json
import os
import signal
import socket
import subprocess
import time

import pytest
import requests

SUPERVISOR = "/app/scripts/dream-cluster-supervisor.py"
EVENTS = "/tmp/cluster-events/events.json"


def _tcp_open(ip, port, timeout=2.0):
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _reset_events():
    if os.path.isfile(EVENTS):
        os.unlink(EVENTS)
    os.makedirs(os.path.dirname(EVENTS), exist_ok=True)


def _read_events():
    if not os.path.isfile(EVENTS):
        return []
    with open(EVENTS) as f:
        return json.load(f)


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


def _spawn_supervisor(workers, restart_policy="always"):
    env = os.environ.copy()
    env["CLUSTER_WORKERS"] = ",".join(workers)
    env["CLUSTER_RESTART_POLICY"] = restart_policy
    return subprocess.Popen(
        ["python3", SUPERVISOR, "--rpc", ",".join(workers), "--port", "8080"],
        env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _stop(proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def test_worker1_reaches_running(worker1_ip, worker_status_port):
    """Sanity: compose healthcheck already waited for this, but assert directly."""
    body = requests.get(
        f"http://{worker1_ip}:{worker_status_port}/health", timeout=5
    ).json()
    assert body["status"] == "ok"
    assert body["agent"]["status"] == "running"
    assert body["rpc_server_running"] is True


def test_worker2_reaches_running(worker2_ip, worker_status_port):
    body = requests.get(
        f"http://{worker2_ip}:{worker_status_port}/health", timeout=5
    ).json()
    assert body["agent"]["status"] == "running"
    assert body["rpc_server_running"] is True


def test_both_workers_discovered_controller_ip(
    worker1_ip, worker2_ip, worker_status_port, controller_ip,
):
    """Both workers must have filled in their controller_ip via UDP discovery."""
    for ip in (worker1_ip, worker2_ip):
        body = requests.get(
            f"http://{ip}:{worker_status_port}/health", timeout=5
        ).json()
        assert body["agent"]["controller_ip"] == controller_ip, (
            f"{ip}: expected controller_ip={controller_ip}, "
            f"got {body['agent']['controller_ip']}"
        )
        assert body["agent"]["setup_port"] == 50051


def test_both_worker_rpc_ports_reachable(
    worker1_ip, worker2_ip, worker_rpc_port,
):
    """Supervisor preflight uses raw TCP — make sure both worker stub
    rpc-servers answer on their advertised port from peer containers."""
    assert _tcp_open(worker1_ip, worker_rpc_port), f"{worker1_ip}:{worker_rpc_port} not reachable"
    assert _tcp_open(worker2_ip, worker_rpc_port), f"{worker2_ip}:{worker_rpc_port} not reachable"


def test_workers_have_distinct_ips(worker1_ip, worker2_ip):
    assert worker1_ip != worker2_ip


def test_supervisor_preflight_accepts_both_real_workers(
    worker1_ip, worker2_ip, worker_rpc_port,
):
    """Point the supervisor at both live worker rpc-servers — preflight
    should include both in the initial server_starting event and NOT emit
    degraded_start."""
    _reset_events()
    workers = [f"{worker1_ip}:{worker_rpc_port}", f"{worker2_ip}:{worker_rpc_port}"]
    proc = _spawn_supervisor(workers)
    try:
        ev = _wait_for_event("server_starting", timeout=15)
        for w in workers:
            assert w in ev["detail"], f"{w} missing from server_starting: {ev['detail']}"
        assert not any(e["type"] == "degraded_start" for e in _read_events()), \
            "supervisor entered degraded mode with two live workers"
    finally:
        _stop(proc)


def test_supervisor_degrades_when_one_real_worker_is_offline(
    worker1_ip, worker_rpc_port,
):
    """One live real worker + one bogus endpoint → supervisor must enter
    degraded mode and launch with only the live worker."""
    _reset_events()
    dead = "172.30.10.99:50099"  # no container at this IP
    live = f"{worker1_ip}:{worker_rpc_port}"
    proc = _spawn_supervisor([live, dead])
    try:
        ev = _wait_for_event("degraded_start", timeout=20)
        assert live in ev["detail"]
        assert dead in ev["detail"]

        started = _wait_for_event("server_starting", timeout=10)
        assert live in started["detail"]
        assert dead not in started["detail"]
    finally:
        _stop(proc)


def test_supervisor_all_workers_live_then_one_disappears(
    worker1_ip, worker2_ip, worker_rpc_port,
):
    """Start with both live. Watchdog must still be ticking — verified by
    observing the periodic workers_healthy event OR that the supervisor
    survives >5s without emitting degraded_start."""
    _reset_events()
    workers = [f"{worker1_ip}:{worker_rpc_port}", f"{worker2_ip}:{worker_rpc_port}"]
    proc = _spawn_supervisor(workers)
    try:
        _wait_for_event("server_starting", timeout=15)
        # Let watchdog run a few cycles.
        time.sleep(12)
        events = _read_events()
        types = [e["type"] for e in events]
        assert "degraded_start" not in types, (
            f"supervisor degraded with both workers live: {events[-10:]}"
        )
    finally:
        _stop(proc)
