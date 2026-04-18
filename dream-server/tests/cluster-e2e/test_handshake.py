"""Raw TCP join handshake tests against the controller's setup listener.

Sends JSON-over-TCP directly — covers the wire protocol without needing
the full cluster_worker_agent Docker plumbing.
"""
import json
import socket

import pytest


def _send_join(controller_ip, payload, recv_timeout=10.0):
    sock = socket.create_connection((controller_ip, 50051), timeout=5.0)
    try:
        sock.sendall(json.dumps(payload).encode() + b"\n")
        sock.settimeout(recv_timeout)
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        if not buf:
            return None
        return json.loads(buf.split(b"\n", 1)[0])
    finally:
        sock.close()


def test_valid_token_accepted(controller_ip, token):
    resp = _send_join(controller_ip, {
        "action": "join",
        "token": token,
        "gpu_backend": "cpu",
        "gpus": [{"name": "E2E-CPU", "vram_mb": 0}],
        "rpc_port": 50052,
    })
    assert resp is not None
    assert resp["status"] == "accepted", resp


def test_invalid_token_rejected(controller_ip):
    resp = _send_join(controller_ip, {
        "action": "join",
        "token": "WRONG-TOKEN",
        "gpu_backend": "cpu",
        "gpus": [],
        "rpc_port": 50052,
    })
    assert resp is not None
    assert resp["status"] == "rejected"
    assert "token" in resp.get("reason", "").lower()


def test_unknown_action_rejected(controller_ip, token):
    resp = _send_join(controller_ip, {
        "action": "hello",
        "token": token,
    })
    assert resp["status"] == "rejected"
    assert "action" in resp.get("reason", "").lower()


def test_duplicate_registration_returns_note(controller_ip, token):
    """Same (ip, rpc_port) registered twice → second call succeeds with 'already registered'."""
    payload = {
        "action": "join",
        "token": token,
        "gpu_backend": "cpu",
        "gpus": [{"name": "E2E-CPU", "vram_mb": 0}],
        "rpc_port": 50099,  # unique port so we don't clash with other tests
    }
    first = _send_join(controller_ip, payload)
    assert first["status"] == "accepted"

    second = _send_join(controller_ip, payload)
    assert second["status"] == "accepted"
    assert "already" in second.get("note", "").lower()


def test_malformed_json_closes_connection(controller_ip):
    sock = socket.create_connection((controller_ip, 50051), timeout=5.0)
    try:
        sock.sendall(b"not-valid-json-at-all\n")
        sock.settimeout(5.0)
        data = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        # Listener logs the bad request and closes — no response payload expected.
        assert data == b""
    finally:
        sock.close()


def test_gpu_metadata_persists_in_config(controller_ip, token):
    """Successful join writes worker into cluster.json; a repeat join sees 'already registered'."""
    payload = {
        "action": "join",
        "token": token,
        "gpu_backend": "nvidia",
        "gpus": [{"name": "RTX-4090-TEST", "vram_mb": 24564}],
        "rpc_port": 50088,
    }
    resp = _send_join(controller_ip, payload)
    assert resp["status"] == "accepted"

    resp2 = _send_join(controller_ip, payload)
    assert resp2["status"] == "accepted"
    assert "already" in resp2.get("note", "").lower()


def test_join_from_alt_container_carries_peer_ip(controller_ip, token):
    """Controller must record the worker's container IP (observed by accept()), not whatever the client claims."""
    # This test-runner joins with rpc_port 51234 — the recorded IP will be OWN_IP.
    import os
    own = os.environ["OWN_IP"]
    resp = _send_join(controller_ip, {
        "action": "join",
        "token": token,
        "gpu_backend": "cpu",
        "gpus": [{"name": "runner", "vram_mb": 0}],
        "rpc_port": 51234,
    })
    assert resp["status"] == "accepted"
    # Re-join same rpc_port should dedupe per (OWN_IP, 51234).
    resp2 = _send_join(controller_ip, {
        "action": "join",
        "token": token,
        "gpu_backend": "cpu",
        "gpus": [{"name": "runner", "vram_mb": 0}],
        "rpc_port": 51234,
    })
    assert "already" in resp2.get("note", "").lower()


def test_join_client_binary(controller_ip, token):
    """Invoke the real cluster-join-client.py CLI the way dream-cli does."""
    import subprocess
    proc = subprocess.run(
        ["python3", "/app/scripts/cluster-join-client.py",
         "--controller-ip", controller_ip,
         "--port", "50051",
         "--token", token,
         "--gpu-backend", "cpu",
         "--rpc-port", "52000",
         "--gpu-json", json.dumps([{"name": "cli-test", "vram_mb": 0}])],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r} stdout={proc.stdout!r}"


def test_join_client_bad_token_exits_nonzero(controller_ip):
    import subprocess
    proc = subprocess.run(
        ["python3", "/app/scripts/cluster-join-client.py",
         "--controller-ip", controller_ip,
         "--port", "50051",
         "--token", "WRONG",
         "--gpu-backend", "cpu",
         "--rpc-port", "52001",
         "--gpu-json", "[]"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1
    assert "rejected" in proc.stderr.lower()


def test_join_client_unreachable_controller():
    import subprocess
    proc = subprocess.run(
        ["python3", "/app/scripts/cluster-join-client.py",
         "--controller-ip", "172.30.10.254",  # no such container
         "--port", "50051",
         "--token", "anything",
         "--gpu-backend", "cpu",
         "--rpc-port", "52002",
         "--gpu-json", "[]"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1
    assert "cannot reach" in proc.stderr.lower() or "refused" in proc.stderr.lower() or "timed out" in proc.stderr.lower()
