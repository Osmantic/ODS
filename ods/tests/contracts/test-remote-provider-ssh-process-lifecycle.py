#!/usr/bin/env python3
"""Exercise the Linux SSH supervisor's child revocation with real processes.

Only the injected process factory is replaced: it launches an owned sleeping
Python interpreter, never SSH. Route/secret planning and termination run as
shipped. A separate child is a sentinel for unrelated processes.
"""

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


CONTRACT = Path(__file__).with_name("test-remote-provider-ssh-tunnel-service.py")
SPEC = importlib.util.spec_from_file_location("ssh_service_contract", CONTRACT)
assert SPEC is not None and SPEC.loader is not None
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)

# A readiness file is written only after SIGTERM behavior is installed. The
# parent never races termination against interpreter startup.
CHILD = """
import os, signal, sys, time
from pathlib import Path
if sys.argv[2] == 'resist':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')
time.sleep(60)
"""


class ProcessLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ods-ssh-process-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.children = []
        self.supervisors = []
        self.addCleanup(self.cleanup_processes)
        self.sentinel = self.spawn_child(resist=False)

    def cleanup_processes(self):
        # Reap only Popen objects created by this fixture, even when a broken
        # supervisor forgets its process or a test assertion fails.
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        for supervisor in self.supervisors:
            supervisor.close()

    def spawn_child(self, *, resist, **kwargs):
        ready = self.root / f"child-{len(self.children)}.ready"
        child = subprocess.Popen(
            [sys.executable, "-I", "-c", CHILD, str(ready), "resist" if resist else "normal"],
            **kwargs,
        )
        self.children.append(child)
        deadline = time.monotonic() + 5
        while not ready.exists():
            self.assertIsNone(child.poll(), "fixture child exited before readiness")
            self.assertLess(time.monotonic(), deadline, "fixture child never became ready")
            time.sleep(0.01)
        self.assertEqual(int(ready.read_text(encoding="utf-8")), child.pid)
        return child

    def supervisor(self, label, *, resist=False):
        root = self.root / label
        root.mkdir()
        route, secrets = contract._ready_supervisor_fixture(root)
        children = []

        def factory(_argv, **kwargs):
            child = self.spawn_child(resist=resist, **kwargs)
            children.append(child)
            return child

        supervisor = contract._health_app().SshProcessSupervisor(
            route, secrets, process_factory=factory,
            start_grace=0, restart_cooldown=0, stop_timeout=0.5,
        )
        self.supervisors.append(supervisor)
        return supervisor, route, secrets, children

    def assert_running(self, supervisor, children, count):
        payload = supervisor.reconcile()
        self.assertTrue(payload["ready"])
        self.assertEqual(len(children), count)
        self.assertIsNone(children[-1].poll())
        self.assertEqual(payload["process"]["pid"], children[-1].pid)
        self.assertIsNone(self.sentinel.poll(), "unrelated fixture process was stopped")

    def test_route_disable_and_custody_loss_reap_child_then_allow_recovery(self):
        for action in ("disable", "remove-key"):
            with self.subTest(action=action):
                supervisor, route, secrets, children = self.supervisor(action)
                self.assert_running(supervisor, children, 1)
                original = children[0]
                # An unchanged valid plan must retain the existing child.
                self.assert_running(supervisor, children, 1)
                state = contract.ssh_route_state()
                if action == "disable":
                    route.write_text(json.dumps({**state, "enabled": False}), encoding="utf-8")
                else:
                    (secrets / "ssh-identity").unlink()
                payload = supervisor.reconcile()
                self.assertFalse(payload["ready"])
                self.assertEqual(payload["process"]["status"], "stopped")
                self.assertEqual(original.poll(), -signal.SIGTERM, "revoked child was not reaped")
                self.assertFalse(supervisor.reconcile()["ready"])
                self.assertEqual(len(children), 1, "revoked plan started another child")
                self.assertIsNone(self.sentinel.poll())
                route.write_text(json.dumps(state), encoding="utf-8")
                (secrets / "ssh-identity").write_text("unit-test-key\n", encoding="utf-8")
                self.assert_running(supervisor, children, 2)
                self.assertNotEqual(children[-1].pid, original.pid)
                supervisor.close()
                self.assertEqual(children[-1].poll(), -signal.SIGTERM)
                supervisor.close()  # Repeated shutdown must be harmless.
                self.assertIsNone(self.sentinel.poll())

    def test_resistant_child_is_killed_and_reaped_on_revocation_or_close(self):
        for action in ("remove-key", "close"):
            with self.subTest(action=action):
                supervisor, _route, secrets, children = self.supervisor(action, resist=True)
                self.assert_running(supervisor, children, 1)
                if action == "remove-key":
                    (secrets / "ssh-identity").unlink()
                    self.assertFalse(supervisor.reconcile()["ready"])
                else:
                    supervisor.close()
                self.assertEqual(children[0].poll(), -signal.SIGKILL, "resistant child survived shutdown")
                self.assertIsNone(self.sentinel.poll())


if __name__ == "__main__":
    if os.name != "posix":
        raise SystemExit("This Linux-container lifecycle contract requires POSIX process signals")
    unittest.main(verbosity=2)
