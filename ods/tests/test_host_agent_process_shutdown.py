#!/usr/bin/env python3
"""Real POSIX signal/HTTP/PID lifecycle; no installed services or host helpers."""

import http.client
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest


AGENT = Path(__file__).resolve().parents[1] / "bin" / "ods-host-agent.py"
LAUNCHER = """\
import importlib.util
import os
import sys

source = sys.argv.pop(1)
spec = importlib.util.spec_from_file_location('shutdown_fixture_agent', source)
agent = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = agent
spec.loader.exec_module(agent)
# These workers can reconcile installed native helpers or remote-provider
# custody. Retain real main(), HTTP server, signals, PID and atexit cleanup.
agent._monitor_native_pixel_access = lambda: None
agent._start_wsl_port_check = lambda: None
agent._schedule_initial_switchboard_verification = lambda *args: None
agent._repair_remote_provider_secret_permissions = lambda: []
agent._migrate_legacy_switchboard_route = lambda *args: False
if os.environ.get('ODS_SHUTDOWN_TEST_DIRECT') == '1':
    # Controlled broken variant: Python documents that this same-thread call
    # deadlocks. Never patch production source to perform the negative control.
    agent._request_server_shutdown = lambda server, signum=None: server.shutdown()
agent.main()
"""


@unittest.skipUnless(os.name == "posix", "requires POSIX SIGTERM/SIGINT semantics")
class HostAgentProcessShutdownTests(unittest.TestCase):
    def run_agent(self, requested_signal, *, idle_connection=False, broken=False):
        with tempfile.TemporaryDirectory(prefix="ods-process-shutdown-") as temporary:
            root = Path(temporary)
            install = root / "install"
            install.mkdir()
            home = root / "home"
            home.mkdir()
            tools = root / "bin"
            tools.mkdir()
            marker = root / "unexpected-host-command"
            for command in ("docker", "sudo", "systemctl", "service", "launchctl"):
                tool = tools / command
                tool.write_text('#!/bin/sh\nprintf unexpected > "' + str(marker) + '"\nexit 99\n')
                tool.chmod(0o755)
            sentinel = root / "foreign-install"
            sentinel.write_text("retain foreign installation\n")
            launcher = root / "launcher.py"
            launcher.write_text(LAUNCHER)
            pid_file = install / "agent.pid"
            config = ("ODS_AGENT_KEY=" + secrets.token_urlsafe(32) + "\n"
                      "ODS_AGENT_BIND=127.0.0.1\nGPU_BACKEND=cpu\nODS_MODE=local\n")
            (install / ".env").write_text(config)
            # The child owns the listener; reserve only a loopback candidate.
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            environment = {"HOME": str(home), "PATH": str(tools), "LANG": "C.UTF-8",
                           "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
            if broken:
                environment["ODS_SHUTDOWN_TEST_DIRECT"] = "1"
            idle = None
            with (root / "agent.log").open("w+") as log:
                child = subprocess.Popen(
                    [sys.executable, str(launcher), str(AGENT), "--install-dir", str(install),
                     "--pid-file", str(pid_file), "--port", str(port)],
                    env=environment, cwd=home, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        if time.monotonic() >= deadline:
                            self.fail("private host agent never became healthy")
                        if child.poll() is not None:
                            log.seek(0)
                            self.fail("private host agent exited during startup: " + log.read())
                        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
                        try:
                            connection.request("GET", "/health")
                            response = connection.getresponse()
                            if response.status == 200 and json.loads(response.read())["status"] == "ok":
                                break
                        except (OSError, http.client.HTTPException):
                            pass
                        finally:
                            connection.close()
                        time.sleep(0.02)
                    self.assertEqual(pid_file.read_text(), str(child.pid))
                    if idle_connection:
                        idle = socket.create_connection(("127.0.0.1", port), timeout=1)
                        idle.sendall(b"GET /health HTTP/1.1\r\nHost: fixture\r\nX-Incomplete: ")
                        idle.settimeout(0.1)
                        with self.assertRaises(socket.timeout):
                            idle.recv(1)
                    # Signal only the process object created above, never a PID
                    # from installed receipts, process-name scans or port owners.
                    child.send_signal(requested_signal)
                    if broken:
                        with self.assertRaises(subprocess.TimeoutExpired):
                            child.wait(timeout=2)
                    else:
                        self.assertEqual(child.wait(timeout=5), 0)
                        self.assertFalse(pid_file.exists(), "normal signal exit must remove its PID receipt")
                        with self.assertRaises(OSError):
                            socket.create_connection(("127.0.0.1", port), timeout=0.2)
                        if idle is not None:
                            self.assertEqual(idle.recv(1), b"", "shutdown retained an incomplete-header client")
                    self.assertFalse(marker.exists(), "test reached a host command")
                    self.assertEqual(sentinel.read_text(), "retain foreign installation\n")
                    self.assertEqual((install / ".env").read_text(), config)
                finally:
                    if idle is not None:
                        idle.close()
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)

    def test_signals_remove_pid_and_release_listener(self):
        for requested_signal in (signal.SIGTERM, signal.SIGINT):
            for idle_connection in (False, True):
                with self.subTest(signal=requested_signal, idle=idle_connection):
                    self.run_agent(requested_signal, idle_connection=idle_connection)

    def test_direct_shutdown_negative_control_deadlocks(self):
        self.run_agent(signal.SIGTERM, idle_connection=True, broken=True)


if __name__ == "__main__":
    unittest.main()
