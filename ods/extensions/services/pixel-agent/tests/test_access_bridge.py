import contextlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "bin"))
import pixel_access_bridge as bridge


class OwnerLauncherTests(unittest.TestCase):
    @unittest.skipUnless(Path("/usr/sbin/runuser").exists(), "Linux runuser required")
    def test_owner_path_cannot_select_privileged_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attacker = root / "runuser"
            attacker.write_text("#!/bin/sh\nexit 99\n")
            attacker.chmod(0o755)
            adapter = bridge.SystemdAccessBridge(root, "k" * 64)
            adapter.home = root
            adapter.owner = types.SimpleNamespace(pw_name="fixture")
            adapter.binary = str(root / "openclaw")
            with patch.object(bridge.subprocess, "Popen", side_effect=RuntimeError("captured")) as launch:
                with self.assertRaisesRegex(RuntimeError, "captured"):
                    adapter.worker()
            args = launch.call_args.args[0]
            self.assertTrue(Path(args[0]).is_absolute())
            self.assertEqual(Path(args[0]), Path("/usr/sbin/runuser").resolve())
            self.assertNotEqual(Path(args[0]), attacker)
            self.assertEqual(launch.call_args.kwargs["env"]["PATH"].split(":")[0], str(root))

    def test_writable_launcher_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsafe = root / "runuser"
            unsafe.write_text("#!/bin/sh\nexit 99\n")
            unsafe.chmod(0o777)
            adapter = bridge.SystemdAccessBridge(root, "k" * 64)
            adapter.home = root
            adapter.owner = types.SimpleNamespace(pw_name="fixture")
            adapter.binary = str(root / "openclaw")
            with patch.object(bridge.Path, "resolve", return_value=unsafe), patch.object(bridge.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(bridge.AccessError, "unsafe-owner-launcher"):
                    adapter.worker()
            launch.assert_not_called()


class FakeBridge(bridge.SystemdAccessBridge):
    """Fake the installed services, retaining the real coordinator and journals."""
    def __init__(self, root):
        super().__init__(root, "k" * 64, state=root / "state", dropin=root / "dropin")
        self.mode, self.managed, self.pid = "sandboxed", False, 123
        self.active = 0
        self.native_phase = self.edge_phase = "idle"
        self.nrev, self.erev = "a" * 64, "b" * 64
        self.proof = None
        self.log = []
        self.fail = None

    def discover(self):
        self.surface = "linux-systemd"
        self.home = self.install
        self.owner = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_name="fixture")
        self.native_origin = "http://127.0.0.1:18789"
        self.native_key = "test"

    @contextlib.contextmanager
    def locked(self):
        self.state.mkdir(mode=0o700, exist_ok=True)
        yield

    def native(self, operation=None, token=None, *, timeout=60):
        if operation:
            self.log.append("native-" + operation)
            if self.fail == operation: raise bridge.AccessError("injected-" + operation)
            if operation == "acquire":
                if self.active: raise bridge.AccessError("busy")
                self.native_phase = "held"
            elif operation == "release": self.native_phase = "idle"
            elif operation == "probe": self.proof = {"mode": self.mode, "executed": True, "pid": self.pid}
        return {"available": True, "phase": self.native_phase, "revision": self.nrev,
                "pid": self.pid, "active": self.active, "proof": self.proof}

    def edge(self, operation=None, token=None, revision=None):
        if operation:
            self.log.append("edge-" + operation)
            if self.fail == "edge-" + operation: raise bridge.AccessError("injected-edge")
            self.edge_phase = "idle" if operation == "release" else "held"
        return {"capability": "available", "phase": self.edge_phase, "revision": self.erev, "streams": 0}

    def worker(self, operation="status", **kwargs):
        if operation != "status":
            self.log.append("controller-" + operation)
            if kwargs["busy"](): raise bridge.AccessError("busy")
            self.mode, self.managed = operation, operation == "full-access"
            self.owner_config()
            if not kwargs["restart"](): raise bridge.AccessError("restart-failed")
        return {"configured_status": self.mode, "config_sha256": bridge.digest(self.mode), "managed": self.managed}

    def owner_config(self):
        folder = self.home / ".openclaw"
        folder.mkdir(exist_ok=True)
        bridge.atomic_json(folder / "openclaw.json", {"agents": {"list": [{"id": "pixel",
            "sandbox": {"mode": "off" if self.mode == "full-access" else "all"},
            "tools": {"exec": {"host": "gateway" if self.mode == "full-access" else "sandbox"}}}]}})

    def command(self, args, timeout=20):
        if "restart" in args:
            self.log.append("restart")
            self.pid += 1
            self.proof = None
            return ""
        return str(self.pid)

    def http(self, *_args, **_kwargs): return {"ok": True}
    def provision_probe(self): self.log.append("provision-probe")
    def dropin_for(self, enabled):
        # Filesystem service adapter is simulated; never write to real systemd.
        if enabled: self.dropin.write_text("[Service]\nProtectSystem=false\nProtectHome=false\n")
        elif self.dropin.exists(): self.dropin.unlink()
    def unit_boundary(self):
        return ("ProtectSystem=no\nProtectHome=no" if self.dropin.exists() else "ProtectSystem=strict\nProtectHome=tmpfs") + "\nNoNewPrivileges=yes"


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ods-access-bridge-test-"))
        self.runtime = FakeBridge(self.root)
        original = bridge.private_json
        # Dedicated owner-owned fixtures emulate root-owned journals; production
        # ownership checks themselves are exercised separately below.
        self.patched = patch.object(bridge, "private_json", side_effect=lambda path, _uid, maximum=1048576: original(path, os.getuid(), maximum))
        self.patched.start()

    def tearDown(self): self.patched.stop()
    def request(self, mode="full-access"):
        return {"mode": mode, "confirmed": mode == "full-access", "revision": self.runtime.status()["revision"]}

    def test_explicit_confirmation_and_exact_request_schema_before_mutation(self):
        for request in ({"mode": "full-access", "confirmed": False, "revision": "a" * 64},
                        {"mode": "full-access", "confirmed": "yes", "revision": "a" * 64},
                        {"mode": "full-access", "confirmed": True, "revision": "a" * 64, "path": "/etc"}):
            with self.assertRaises(bridge.AccessError): self.runtime.change(request)
        self.assertEqual(self.runtime.log, [])
        self.assertFalse(self.runtime.state.exists())

    def test_busy_and_stale_revision_do_not_write_receipts_or_settings(self):
        request = self.request()
        self.runtime.active = 1
        with self.assertRaises(bridge.AccessError): self.runtime.change(request)
        self.assertFalse((self.runtime.state / "transition.json").exists())
        self.runtime.active = 0
        self.runtime.nrev = "c" * 64
        with self.assertRaises(bridge.AccessError): self.runtime.change(request)
        self.assertEqual(self.runtime.log, [])

    def test_full_enable_and_restore_require_executed_proof_and_preserve_boundaries(self):
        self.assertEqual(self.runtime.status()["effective_mode"], "unknown")
        enabled = self.runtime.change(self.request())
        self.assertEqual(enabled["effective_mode"], "full-access")
        self.assertTrue(enabled["runtime_verified"])
        self.assertEqual(self.runtime.dropin.read_text(), "[Service]\nProtectSystem=false\nProtectHome=false\n")
        self.assertLess(self.runtime.log.index("native-acquire"), self.runtime.log.index("controller-full-access"))
        self.assertLess(self.runtime.log.index("native-probe"), self.runtime.log.index("edge-release"))
        restored = self.runtime.change(self.request("sandboxed"))
        self.assertEqual(restored["effective_mode"], "sandboxed")
        self.assertFalse(self.runtime.dropin.exists())
        self.assertFalse((self.runtime.state / "transition.json").exists())

    def test_failed_probe_retains_both_gates_and_recovery_receipt(self):
        self.runtime.fail = "probe"
        with self.assertRaises(bridge.AccessError): self.runtime.change(self.request())
        self.assertEqual(self.runtime.native_phase, "held")
        self.assertEqual(self.runtime.edge_phase, "held")
        self.assertEqual(self.runtime.pending()["phase"], "error")
        self.assertEqual(self.runtime.status()["effective_mode"], "unknown")
        self.runtime.fail = None
        self.assertEqual(self.runtime.change(self.request("sandboxed"))["effective_mode"], "sandboxed")

    def test_pristine_safer_probe_recovery_does_not_restart_unchanged_gateway(self):
        self.runtime.fail = "probe"
        with self.assertRaises(bridge.AccessError):
            self.runtime.change(self.request("sandboxed"))
        self.assertFalse(self.runtime.managed)
        self.assertEqual(self.runtime.pending()["phase"], "error")
        self.assertEqual(self.runtime.pending()["error"], "injected-probe")
        self.runtime.fail = None
        restored = self.runtime.change(self.request("sandboxed"))
        self.assertTrue(restored["runtime_verified"])
        self.assertEqual(restored["effective_mode"], "sandboxed")
        self.assertNotIn("restart", self.runtime.log)

    def test_slow_gateway_start_is_observed_without_restarting_again(self):
        original = self.runtime.native
        elapsed = [0.0]
        reads = []
        def native(operation=None, token=None, *, timeout=60):
            if "restart" in self.runtime.log and operation is None:
                reads.append(timeout)
                if len(reads) <= 40:
                    raise bridge.AccessError("native-idle-unconfirmed")
            return original(operation, token, timeout=timeout)
        def sleep(seconds): elapsed[0] += seconds
        with patch.object(self.runtime, "native", side_effect=native), \
                patch.object(bridge.time, "monotonic", side_effect=lambda: elapsed[0]), \
                patch.object(bridge.time, "sleep", side_effect=sleep):
            result = self.runtime.change(self.request())
        self.assertTrue(result["runtime_verified"])
        self.assertEqual(self.runtime.log.count("restart"), 1)
        self.assertEqual(reads[:40], [3] * 40)

    def test_gateway_readiness_deadline_retains_both_holds(self):
        original = self.runtime.native
        elapsed = [0.0]
        def native(operation=None, token=None, *, timeout=60):
            if "restart" in self.runtime.log and operation is None:
                raise bridge.AccessError("native-idle-unconfirmed")
            return original(operation, token, timeout=timeout)
        def sleep(seconds): elapsed[0] += seconds
        with patch.object(self.runtime, "native", side_effect=native), \
                patch.object(bridge.time, "monotonic", side_effect=lambda: elapsed[0]), \
                patch.object(bridge.time, "sleep", side_effect=sleep):
            with self.assertRaises(bridge.AccessError):
                self.runtime.change(self.request())
        self.assertEqual(elapsed[0], 120)
        self.assertEqual(self.runtime.log.count("restart"), 1)
        self.assertEqual(self.runtime.pending()["phase"], "error")
        self.assertEqual(self.runtime.native_phase, "held")
        self.assertEqual(self.runtime.edge_phase, "held")

    def test_unavailable_inspection_preserves_pending_transition_for_polling(self):
        self.runtime.fail = "probe"
        with self.assertRaises(bridge.AccessError):
            self.runtime.change(self.request("sandboxed"))
        with patch.object(self.runtime, "worker", side_effect=bridge.AccessError("controller-lock-busy")):
            status = self.runtime.status()
        self.assertTrue(status["pending"])
        self.assertFalse(status["available"])
        self.assertFalse(status["runtime_verified"])
        self.assertIsNone(status["revision"])

    def test_release_failure_does_not_reopen_native_admission(self):
        self.runtime.fail = "edge-release"
        with self.assertRaises(bridge.AccessError): self.runtime.change(self.request())
        self.assertEqual(self.runtime.native_phase, "held")
        self.assertNotIn("native-release", self.runtime.log)

    def test_proof_invalidated_by_external_config_change_or_gateway_restart(self):
        self.runtime.change(self.request())
        self.runtime.pid += 1
        self.assertEqual(self.runtime.status()["effective_mode"], "unknown")

    def test_stopped_gateway_recovery_requires_empty_unit_and_same_durable_lease(self):
        real = bridge.SystemdAccessBridge(self.root, "k" * 64)
        real.home = self.root
        real.owner = types.SimpleNamespace(pw_uid=os.getuid())
        directory = self.root / ".openclaw/.ods-access-runtime"
        directory.mkdir(parents=True, mode=0o700)
        token = "c" * 64
        bridge.atomic_json(directory / "state.json", {"phase": "held", "revision": "d" * 64,
                           "tokenHash": bridge.hashlib.sha256(token.encode()).hexdigest()})
        real.command = lambda *_args, **_kwargs: "MainPID=0\nActiveState=failed\nControlGroup="
        self.assertTrue(real.stopped_native(token)["stopped"])
        with self.assertRaises(bridge.AccessError): real.stopped_native("e" * 64)
        real.command = lambda *_args, **_kwargs: "MainPID=234\nActiveState=active\nControlGroup="
        with self.assertRaises(bridge.AccessError): real.stopped_native(token)
        real.command = lambda *_args, **_kwargs: "MainPID=0\nActiveState=failed\nControlGroup=/../../etc"
        with self.assertRaises(bridge.AccessError): real.stopped_native(token)


if __name__ == "__main__": unittest.main()
