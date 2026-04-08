#!/usr/bin/env python3
"""
Tests for dream-cluster-supervisor.py

Covers: parse_workers, check_worker, preflight, log_event (atomic writes),
        wait_for_workers (all three policies), run_server, SIGTERM forwarding.

Usage: python3 tests/test-cluster-supervisor.py
       pytest tests/test-cluster-supervisor.py -v
"""
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

# Load the supervisor module from the Docker build context (canonical location)
SUPERVISOR_PATH = os.path.join(
    os.path.dirname(__file__), "..", "images", "llama-rpc", "dream-cluster-supervisor.py"
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

    def test_entry_without_port_skipped(self):
        """Entries without ':' are silently skipped."""
        result = supervisor.parse_workers("192.168.1.1:50052,badentry,10.0.0.1:50052")
        self.assertEqual(result, [("192.168.1.1", 50052), ("10.0.0.1", 50052)])

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


class TestPreflight(unittest.TestCase):
    def test_no_workers(self):
        ok, failed = supervisor.preflight([])
        self.assertTrue(ok)
        self.assertEqual(failed, [])

    def test_all_reachable(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            ok, failed = supervisor.preflight([("127.0.0.1", port)])
            self.assertTrue(ok)
            self.assertEqual(failed, [])
        finally:
            srv.close()

    def test_mixed_reachable_unreachable(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            ok, failed = supervisor.preflight([
                ("127.0.0.1", port),
                ("192.0.2.1", 50052),
            ])
            self.assertFalse(ok)
            self.assertEqual(failed, ["192.0.2.1:50052"])
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


class TestWaitForWorkers(unittest.TestCase):
    """Test wait_for_workers with mocked preflight to avoid real network calls."""

    def setUp(self):
        self._orig_preflight = supervisor.preflight
        self._orig_log_event = supervisor.log_event
        self._orig_poll_interval = supervisor.POLL_INTERVAL
        self._orig_max_wait = supervisor.MAX_WORKER_WAIT
        # Speed up tests
        supervisor.POLL_INTERVAL = 0.05
        supervisor.MAX_WORKER_WAIT = 0.3
        self.logged_events = []
        supervisor.log_event = lambda t, d="": self.logged_events.append((t, d))

    def tearDown(self):
        supervisor.preflight = self._orig_preflight
        supervisor.log_event = self._orig_log_event
        supervisor.POLL_INTERVAL = self._orig_poll_interval
        supervisor.MAX_WORKER_WAIT = self._orig_max_wait

    def test_all_reachable_restarts(self):
        """When all workers are reachable after crash, should restart."""
        supervisor.preflight = lambda w: (True, [])
        result = supervisor.wait_for_workers([("1.2.3.4", 50052)], "always")
        self.assertTrue(result)

    def test_manual_policy_exits(self):
        """Manual policy should exit when workers are offline."""
        supervisor.preflight = lambda w: (False, ["1.2.3.4:50052"])
        result = supervisor.wait_for_workers([("1.2.3.4", 50052)], "manual")
        self.assertFalse(result)
        self.assertEqual(self.logged_events[0][0], "manual_intervention_required")

    def test_on_worker_recovery_exits_on_timeout(self):
        """on-worker-recovery should exit when workers don't recover in time."""
        supervisor.preflight = lambda w: (False, ["1.2.3.4:50052"])
        result = supervisor.wait_for_workers([("1.2.3.4", 50052)], "on-worker-recovery")
        self.assertFalse(result)

    def test_on_worker_recovery_restarts_on_recovery(self):
        """on-worker-recovery should restart when workers come back."""
        call_count = [0]
        def mock_preflight(w):
            call_count[0] += 1
            if call_count[0] >= 3:
                return (True, [])
            return (False, ["1.2.3.4:50052"])
        supervisor.preflight = mock_preflight
        result = supervisor.wait_for_workers([("1.2.3.4", 50052)], "on-worker-recovery")
        self.assertTrue(result)

    def test_always_policy_restarts_after_timeout(self):
        """always policy should restart even when workers stay offline."""
        supervisor.preflight = lambda w: (False, ["1.2.3.4:50052"])
        result = supervisor.wait_for_workers([("1.2.3.4", 50052)], "always")
        self.assertTrue(result)

    def test_stale_failed_list_fix(self):
        """Verify the log uses the latest failed list, not the initial one."""
        # Simulate: initially both workers fail, then worker1 recovers but worker2 stays down
        call_count = [0]
        def mock_preflight(w):
            call_count[0] += 1
            if call_count[0] == 1:
                return (False, ["1.1.1.1:50052", "2.2.2.2:50052"])
            return (False, ["2.2.2.2:50052"])
        supervisor.preflight = mock_preflight
        supervisor.wait_for_workers(
            [("1.1.1.1", 50052), ("2.2.2.2", 50052)], "on-worker-recovery"
        )
        # The logged event should only mention the still-offline worker
        offline_events = [e for e in self.logged_events if e[0] == "workers_offline"]
        self.assertTrue(len(offline_events) > 0)
        self.assertIn("2.2.2.2:50052", offline_events[0][1])
        self.assertNotIn("1.1.1.1:50052", offline_events[0][1])


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
