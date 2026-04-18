"""End-to-end tests for cluster_worker_agent.py.

The agent is a long-lived daemon that:
  1. discovers (UDP) or receives (via --controller) the controller IP
  2. joins the controller (TCP 50051) with a token
  3. starts a `dream-rpc-server` Docker container
  4. exposes an HTTP /health endpoint on --status-port

Docker is stubbed at /usr/local/bin/docker — step 3 spawns a Python TCP
listener as a stand-in for rpc-server.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest
import requests

AGENT = "/app/scripts/cluster_worker_agent.py"


def _free_port():
    s = socket.socket()
    s.bind(("", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_health(url, timeout=15.0, want_status=None):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            r = requests.get(url, timeout=1.5)
            if r.status_code == 200:
                body = r.json()
                if want_status is None or body["agent"]["status"] == want_status:
                    return body
                last = body
        except Exception as e:
            last = e
        time.sleep(0.3)
    raise AssertionError(f"health {url} never reached (want_status={want_status}): {last}")


def _start_agent(extra_env=None, extra_args=None):
    cfg = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    cfg.close()
    status_port = _free_port()
    rpc_port = _free_port()

    env = os.environ.copy()
    env["GPU_BACKEND"] = "cpu"  # skip hardware probing
    if extra_env:
        env.update(extra_env)

    # Seed the config with token + rpc_port so the agent doesn't error out on start.
    with open(cfg.name, "w") as f:
        json.dump({
            "token": os.environ["TOKEN"],
            "rpc_port": rpc_port,
            "gpu_backend": "cpu",
            "controller_ip": "",
            "setup_port": 50051,
            "status": "idle",
        }, f)

    args = [
        "python3", AGENT,
        "--config", cfg.name,
        "--status-port", str(status_port),
    ]
    if extra_args:
        args.extend(extra_args)

    proc = subprocess.Popen(
        args, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )
    return proc, cfg.name, status_port, rpc_port


def _stop(proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def test_agent_discovers_and_joins(controller_ip):
    """Agent with no preset controller_ip uses UDP discovery then joins."""
    proc, cfg, status_port, rpc_port = _start_agent()
    try:
        body = _wait_health(f"http://127.0.0.1:{status_port}/health",
                            timeout=30, want_status="running")
        agent = body["agent"]
        assert agent["controller_ip"] == controller_ip
        assert agent["setup_port"] == 50051
        assert body["rpc_server_running"] is True
    finally:
        _stop(proc)
        os.unlink(cfg)


def test_agent_direct_controller_skips_discovery(controller_ip):
    """--controller flag must bypass UDP beacon wait."""
    # Use a unique rpc_port so the setup-listener doesn't mark us as dup.
    proc, cfg, status_port, rpc_port = _start_agent(
        extra_args=["--controller", controller_ip],
    )
    try:
        # Should reach 'joined' or 'running' quickly; no 30s discovery wait.
        t0 = time.monotonic()
        _wait_health(f"http://127.0.0.1:{status_port}/health",
                     timeout=15, want_status="running")
        elapsed = time.monotonic() - t0
        assert elapsed < 15, f"agent took {elapsed:.1f}s — discovery not skipped?"
    finally:
        _stop(proc)
        os.unlink(cfg)


def test_agent_persists_controller_ip_to_config(controller_ip):
    proc, cfg, status_port, rpc_port = _start_agent()
    try:
        _wait_health(f"http://127.0.0.1:{status_port}/health",
                     timeout=30, want_status="running")
        with open(cfg) as f:
            state = json.load(f)
        assert state["controller_ip"] == controller_ip
        assert state["status"] == "running"
        assert state["gpu_backend"] == "cpu"
    finally:
        _stop(proc)
        os.unlink(cfg)


def test_agent_health_endpoint_shape(controller_ip):
    proc, cfg, status_port, rpc_port = _start_agent()
    try:
        body = _wait_health(f"http://127.0.0.1:{status_port}/health",
                            timeout=30, want_status="running")
        assert body["status"] == "ok"
        assert "agent" in body
        assert "rpc_server_running" in body
        for k in ("token", "controller_ip", "setup_port", "rpc_port", "gpu_backend", "status"):
            assert k in body["agent"]
    finally:
        _stop(proc)
        os.unlink(cfg)


def test_agent_sigterm_shuts_down_cleanly(controller_ip):
    proc, cfg, status_port, rpc_port = _start_agent()
    try:
        _wait_health(f"http://127.0.0.1:{status_port}/health",
                     timeout=30, want_status="running")
        proc.send_signal(signal.SIGTERM)
        try:
            rc = proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("agent did not exit within 15s of SIGTERM")
        assert rc == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        os.unlink(cfg)


def test_agent_without_token_exits_error():
    cfg = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    cfg.close()
    with open(cfg.name, "w") as f:
        json.dump({"token": "", "rpc_port": 50052, "gpu_backend": "cpu"}, f)

    status_port = _free_port()
    proc = subprocess.run(
        ["python3", AGENT, "--config", cfg.name, "--status-port", str(status_port)],
        capture_output=True, text=True, timeout=10,
    )
    os.unlink(cfg.name)
    assert "No token configured" in proc.stdout or "No token configured" in proc.stderr
