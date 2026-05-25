#!/usr/bin/env python3
"""
Tests for dream-cluster-supervisor.py

Covers: parse_workers (including malformed-input rejection),
        check_worker, partition_workers, log_event (atomic writes),
        run_server, SIGTERM forwarding.

Restart-policy branching (manual / on-worker-recovery / always) is now
inlined in main() and is exercised by the cluster-e2e fault-tolerance
tests rather than unit-mocked here.

Usage: python3 tests/test-cluster-supervisor.py
       pytest tests/test-cluster-supervisor.py -v
"""
import importlib.util
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import unittest

# Canonical supervisor lives in scripts/; images/llama-rpc/ is a staged
# build-context copy populated by installers/phases/08-images.sh.
SUPERVISOR_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "dream-cluster-supervisor.py"
)
spec = importlib.util.spec_from_file_location("supervisor", SUPERVISOR_PATH)
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class TestParseWorkers(unittest.TestCase):
    def test_single_worker(self):
        result = supervisor.parse_workers("192.168.1.1:50052")
        self.assertEqual(result, [("192.168.1.1", 50052)])

    def test_multiple_workers(self):
        result = supervisor.parse_workers("192.168.1.1:50052,192.168.1.2:50052")
        self.assertEqual(result, [("192.168.1.1", 50052), ("192.168.1.2", 50052)])

    def test_whitespace_handling(self):
        result = supervisor.parse_workers("  10.0.0.1:50052 , 10.0.0.2:50052  ")
        self.assertEqual(result, [("10.0.0.1", 50052), ("10.0.0.2", 50052)])

    def test_empty_string(self):
        result = supervisor.parse_workers("")
        self.assertEqual(result, [])

    def test_malformed_entry_raises(self):
        """Malformed entries raise ValueError with the offending token."""
        cases = [
            ("nocolon",        "expected host:port"),
            ("host:abc",       "not an integer"),
            ("host:",          "not an integer"),
            (":50052",         "empty host"),
            ("h:0",            "out of range"),
            ("h:65536",        "out of range"),
            ("h:-1",           "out of range"),
        ]
        for bad, hint in cases:
            with self.assertRaises(ValueError) as ctx:
                supervisor.parse_workers(f"192.168.1.1:50052,{bad}")
            msg = str(ctx.exception)
            self.assertIn(bad, msg, f"error message should include {bad!r}: {msg!r}")
            self.assertIn(hint, msg)

    def test_empty_segments_tolerated(self):
        """Trailing/duplicate commas produce empty segments that are skipped."""
        result = supervisor.parse_workers("10.0.0.1:50052,,10.0.0.2:50052,")
        self.assertEqual(result, [("10.0.0.1", 50052), ("10.0.0.2", 50052)])

    def test_custom_port(self):
        result = supervisor.parse_workers("10.0.0.1:9999")
        self.assertEqual(result, [("10.0.0.1", 9999)])

    def test_ipv6_rsplit(self):
        """rsplit on ':' handles IPv6-style addresses if last segment is port."""
        result = supervisor.parse_workers("[::1]:50052")
        self.assertEqual(result, [("[::1]", 50052)])


class TestCheckWorker(unittest.TestCase):
    def test_unreachable_host(self):
        """Non-routable address should return False, not raise."""
        result = supervisor.check_worker("192.0.2.1", 50052, timeout=0.3)
        self.assertFalse(result)

    def test_reachable_host(self):
        """Connect to a real listening socket."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            result = supervisor.check_worker("127.0.0.1", port, timeout=1)
            self.assertTrue(result)
        finally:
            srv.close()

    def test_connection_refused(self):
        """Port with nothing listening should return False."""
        # Use a high ephemeral port that's almost certainly not in use
        result = supervisor.check_worker("127.0.0.1", 59999, timeout=0.3)
        self.assertFalse(result)


class TestPartitionWorkers(unittest.TestCase):
    def test_no_workers(self):
        live, dead = supervisor.partition_workers([])
        self.assertEqual(live, [])
        self.assertEqual(dead, [])

    def test_all_reachable(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            live, dead = supervisor.partition_workers([("127.0.0.1", port)])
            self.assertEqual(live, [("127.0.0.1", port)])
            self.assertEqual(dead, [])
        finally:
            srv.close()

    def test_mixed_reachable_unreachable(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            live, dead = supervisor.partition_workers([
                ("127.0.0.1", port),
                ("192.0.2.1", 50052),
            ])
            self.assertEqual(live, [("127.0.0.1", port)])
            self.assertEqual(dead, [("192.0.2.1", 50052)])
        finally:
            srv.close()

    def test_order_preserved(self):
        """partition_workers must keep input order within live/dead lists."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            live, dead = supervisor.partition_workers([
                ("192.0.2.1", 50052),
                ("127.0.0.1", port),
                ("192.0.2.2", 50052),
            ])
            self.assertEqual(live, [("127.0.0.1", port)])
            self.assertEqual(dead, [("192.0.2.1", 50052), ("192.0.2.2", 50052)])
        finally:
            srv.close()


class TestLogEvent(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.events_file = os.path.join(self.tmpdir, "events.json")
        self._orig = supervisor.EVENTS_FILE
        supervisor.EVENTS_FILE = self.events_file

    def tearDown(self):
        supervisor.EVENTS_FILE = self._orig
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_file(self):
        supervisor.log_event("test_event", "detail")
        self.assertTrue(os.path.exists(self.events_file))
        with open(self.events_file) as f:
            events = json.load(f)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "test_event")
        self.assertEqual(events[0]["detail"], "detail")
        self.assertIn("timestamp", events[0])

    def test_appends_events(self):
        supervisor.log_event("first", "a")
        supervisor.log_event("second", "b")
        with open(self.events_file) as f:
            events = json.load(f)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "first")
        self.assertEqual(events[1]["type"], "second")

    def test_truncates_at_100(self):
        for i in range(110):
            supervisor.log_event(f"event_{i}")
        with open(self.events_file) as f:
            events = json.load(f)
        self.assertEqual(len(events), 100)
        # Oldest events dropped — first event should be event_10
        self.assertEqual(events[0]["type"], "event_10")

    def test_atomic_write_no_partial_json(self):
        """Verify no .tmp file is left behind (atomic replace completed)."""
        supervisor.log_event("atomic_test")
        tmp_file = self.events_file + ".tmp"
        self.assertFalse(os.path.exists(tmp_file))

    def test_creates_parent_directory(self):
        nested = os.path.join(self.tmpdir, "sub", "dir", "events.json")
        supervisor.EVENTS_FILE = nested
        supervisor.log_event("nested_test")
        self.assertTrue(os.path.exists(nested))


class TestRunServer(unittest.TestCase):
    def test_returns_exit_code(self):
        exit_code = supervisor.run_server(["python3", "-c", "import sys; sys.exit(42)"])
        self.assertEqual(exit_code, 42)

    def test_returns_zero_on_success(self):
        exit_code = supervisor.run_server(["python3", "-c", "pass"])
        self.assertEqual(exit_code, 0)

    def test_child_proc_set_during_execution(self):
        """Verify _child_proc is set while the server runs and cleared after."""
        self.assertIsNone(supervisor._child_proc)
        exit_code = supervisor.run_server(["python3", "-c", "import time; time.sleep(0.1)"])
        self.assertEqual(exit_code, 0)
        self.assertIsNone(supervisor._child_proc)


class TestSIGTERMForwarding(unittest.TestCase):
    def test_sigterm_terminates_child(self):
        """
        Verify that SIGTERM to the supervisor causes the child to be terminated.
        We launch the supervisor as a subprocess with a long-running child,
        send SIGTERM to the supervisor, and verify the child is also gone.
        """
        script = f"""
import sys, signal, subprocess, importlib.util

spec = importlib.util.spec_from_file_location("supervisor", "{SUPERVISOR_PATH}")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

signal.signal(signal.SIGTERM, mod._handle_term)

# Simulate run_server: launch a long-running child
mod._child_proc = subprocess.Popen(["sleep", "60"])
print(f"CHILD_PID={{mod._child_proc.pid}}", flush=True)

# _handle_term calls sys.exit(0), which raises SystemExit.
# Let it propagate so the process actually exits.
mod._child_proc.wait()
"""
        proc = subprocess.Popen(
            ["python3", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Read child PID
        line = proc.stdout.readline().decode().strip()
        self.assertTrue(line.startswith("CHILD_PID="), f"Unexpected output: {line}")
        child_pid = int(line.split("=")[1])

        # Verify child is alive
        os.kill(child_pid, 0)  # raises OSError if dead

        # Send SIGTERM to supervisor
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

        # Give the child a moment to die
        time.sleep(0.2)

        # Verify child is dead
        with self.assertRaises(OSError):
            os.kill(child_pid, 0)


if __name__ == "__main__":
    unittest.main()
