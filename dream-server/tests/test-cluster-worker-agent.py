#!/usr/bin/env python3
"""Unit tests for cluster_worker_agent.join_cluster and cluster-join-client.

Covers C2 (malformed JSON from controller must not crash) and the
surrounding handshake-response edge cases. Uses a real in-process TCP
listener on 127.0.0.1 so the socket code path is actually exercised.

Usage: python3 tests/test-cluster-worker-agent.py
       pytest  tests/test-cluster-worker-agent.py -v
"""
import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
import unittest

HERE = os.path.dirname(__file__)
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, SCRIPTS)  # so cluster_worker_agent can import cluster_discovery

spec = importlib.util.spec_from_file_location(
    "cluster_worker_agent", os.path.join(SCRIPTS, "cluster_worker_agent.py")
)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

JOIN_CLIENT = os.path.join(SCRIPTS, "cluster-join-client.py")


class FakeController:
    """Minimal TCP listener that sends a scripted reply and closes.

    reply: bytes to write after the first client sends any data. Writer
           is on a background thread so join_cluster can do recv() first.
    """

    def __init__(self, reply: bytes):
        self._reply = reply
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self._srv.settimeout(5.0)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self.received = b""
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._srv.accept()
        except socket.timeout:
            return
        conn.settimeout(5.0)
        try:
            # Drain the worker's join payload until newline so the test can
            # assert on what was sent.
            while b"\n" not in self.received:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                self.received += chunk
            if self._reply is not None:
                conn.sendall(self._reply)
        finally:
            conn.close()

    def close(self):
        try:
            self._srv.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)


class TestJoinCluster(unittest.TestCase):
    """join_cluster() must tolerate every shape of bad controller response."""

    def _join(self, fake):
        return agent.join_cluster(
            controller_ip="127.0.0.1",
            setup_port=fake.port,
            token="t",
            gpu_backend="cpu",
            gpus=[{"name": "CPU only", "vram_mb": 0}],
            rpc_port=50052,
        )

    def test_garbage_bytes_returns_false(self):
        """Non-JSON reply (C2) must not raise."""
        fake = FakeController(reply=b"not-json-at-all\n")
        try:
            self.assertFalse(self._join(fake))
        finally:
            fake.close()

    def test_truncated_json_returns_false(self):
        fake = FakeController(reply=b'{"status": "acce\n')
        try:
            self.assertFalse(self._join(fake))
        finally:
            fake.close()

    def test_invalid_utf8_returns_false(self):
        fake = FakeController(reply=b"\xff\xfe\xfd\n")
        try:
            self.assertFalse(self._join(fake))
        finally:
            fake.close()

    def test_json_list_returns_false(self):
        """A well-formed but non-dict payload must be rejected."""
        fake = FakeController(reply=b'["accepted"]\n')
        try:
            self.assertFalse(self._join(fake))
        finally:
            fake.close()

    def test_rejection_returns_false(self):
        fake = FakeController(reply=b'{"status": "rejected", "reason": "bad token"}\n')
        try:
            self.assertFalse(self._join(fake))
        finally:
            fake.close()

    def test_accept_returns_true(self):
        fake = FakeController(reply=b'{"status": "accepted"}\n')
        try:
            self.assertTrue(self._join(fake))
        finally:
            fake.close()

    def test_controller_closes_silently_returns_false(self):
        fake = FakeController(reply=b"")  # accept, send nothing, close
        try:
            self.assertFalse(self._join(fake))
        finally:
            fake.close()

    def test_connect_refused_returns_false(self):
        # bind+close to reserve a port, then release it so connect gets ECONNREFUSED
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertFalse(agent.join_cluster(
            controller_ip="127.0.0.1",
            setup_port=port,
            token="t",
            gpu_backend="cpu",
            gpus=[],
            rpc_port=50052,
        ))


class SilentController:
    """TCP listener that accepts but never sends a response and never closes.

    Simulates a wedged / network-partitioned controller — the worker must
    give up within HANDSHAKE_TOTAL_TIMEOUT rather than hang forever (M13).
    """

    def __init__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self._srv.settimeout(5.0)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._conn = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._srv.accept()
        except (socket.timeout, OSError):
            return
        self._conn = conn
        # Sit on the connection — don't read, don't write. Holding the
        # reference keeps the kernel from RST'ing. Released by close().
        while not self._stop.is_set():
            self._stop.wait(0.25)
        try:
            conn.close()
        except OSError:
            pass

    def close(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)


class TestHandshakeTimeout(unittest.TestCase):
    """Slow-but-alive controller must be dropped within HANDSHAKE_TOTAL_TIMEOUT (M13)."""

    def test_join_cluster_gives_up_on_silent_controller(self):
        # Override module constant so the test is fast. join_cluster reads
        # HANDSHAKE_TOTAL_TIMEOUT off the module at each call.
        original = agent.HANDSHAKE_TOTAL_TIMEOUT
        agent.HANDSHAKE_TOTAL_TIMEOUT = 2
        fake = SilentController()
        try:
            started = time.monotonic()
            result = agent.join_cluster(
                controller_ip="127.0.0.1",
                setup_port=fake.port,
                token="t",
                gpu_backend="cpu",
                gpus=[{"name": "CPU only", "vram_mb": 0}],
                rpc_port=50052,
            )
            elapsed = time.monotonic() - started
            self.assertFalse(result, "join_cluster should reject a silent controller")
            # Upper bound = timeout + one poll slot + generous slack for CI.
            # Lower bound guards against the function returning early by
            # accident (e.g. reading b"" and bailing as EOF).
            self.assertGreaterEqual(elapsed, agent.HANDSHAKE_TOTAL_TIMEOUT - 0.5,
                                    f"join_cluster returned too fast ({elapsed:.2f}s)")
            self.assertLess(elapsed, agent.HANDSHAKE_TOTAL_TIMEOUT + agent.HANDSHAKE_RECV_POLL + 5,
                            f"join_cluster hung past budget ({elapsed:.2f}s)")
        finally:
            fake.close()
            agent.HANDSHAKE_TOTAL_TIMEOUT = original


class TestJoinClient(unittest.TestCase):
    """cluster-join-client.py must exit non-zero on bad controller responses."""

    def _run_client(self, fake_port, timeout=10):
        return subprocess.run(
            ["python3", JOIN_CLIENT,
             "--controller-ip", "127.0.0.1",
             "--port", str(fake_port),
             "--token", "t",
             "--gpu-backend", "cpu",
             "--rpc-port", "50052",
             "--gpu-json", "[]"],
            capture_output=True, text=True, timeout=timeout,
        )

    def test_garbage_response_exits_nonzero(self):
        fake = FakeController(reply=b"garbage-not-json\n")
        try:
            res = self._run_client(fake.port)
            self.assertEqual(res.returncode, 1)
            self.assertIn("Malformed response", res.stderr)
        finally:
            fake.close()

    def test_non_dict_response_exits_nonzero(self):
        fake = FakeController(reply=b'"accepted"\n')
        try:
            res = self._run_client(fake.port)
            self.assertEqual(res.returncode, 1)
            self.assertIn("Unexpected response shape", res.stderr)
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()
