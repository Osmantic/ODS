#!/usr/bin/env python3
"""Behavioral tests for the durable Pixel model transition."""
import contextlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import pixel_access_protocol as protocol  # noqa: E402
from pixel_access_bridge import AccessError, SystemdAccessBridge  # noqa: E402
from pixel_model_transition import execute, main  # noqa: E402


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64


class FakeBridge(SystemdAccessBridge):
    def __init__(self, root):
        self.state = pathlib.Path(root)
        self.state.mkdir(exist_ok=True)
        self.calls = []
        self.edge_state = {"capability": "available", "phase": "idle",
                           "revision": HEX_B, "streams": 0}
        self.native_state = {"available": True, "phase": "idle", "revision": HEX_C,
                             "active": 0, "pid": 123, "proof": None, "stopped": False}
        self.config = {"configured_status": "sandboxed", "config_sha256": HEX_A}
        self.edge_streams = []
        self.native_active = []
        self.fail_edge = None
        self.fail_native = None
        self.fail_edge_after = None
        self.fail_native_after = None
        self.fail_verify = False
        self.available = True
        self.scope = "owner-host"
        self.runtime_verified = True
        self.effective_mode = "sandboxed"
        self.reason = None

    @contextlib.contextmanager
    def locked(self):
        self.calls.append("lock")
        yield

    @contextlib.contextmanager
    def bounded(self, _seconds):
        yield

    def pending(self):
        path = self.state / "transition.json"
        return json.loads(path.read_text()) if path.exists() else None

    def remove_model_journal(self):
        (self.state / "transition.json").unlink()

    def inspect(self, *, allow_installing=False):
        self.discover(allow_installing=allow_installing)
        return {"available": self.available, "configured_mode": "sandboxed",
                "effective_mode": self.effective_mode,
                "runtime_verified": self.runtime_verified,
                "revision": HEX_A, "busy": bool(self.edge_state["streams"] or self.native_state["active"]),
                "pending": False, "reason": self.reason, "scope": self.scope,
                "_config": dict(self.config), "_native": dict(self.native_state),
                "_edge": dict(self.edge_state)}

    def discover(self, *, allow_installing=False):
        self.calls.append("discover-installing" if allow_installing else "discover")

    def edge(self, operation=None, token=None, revision=None):
        self.calls.append("edge:" + (operation or "status"))
        if operation and not (self.state / "transition.json").exists():
            raise AssertionError("edge operation preceded durable journal")
        if self.fail_edge is not None and self.fail_edge == operation:
            raise AccessError("edge-test-failure")
        if operation in ("acquire", "recover"):
            self.edge_state["phase"] = "held"
            if self.edge_streams:
                self.edge_state["streams"] = self.edge_streams.pop(0)
        elif operation == "release":
            self.edge_state["phase"] = "idle"
            self.edge_state["streams"] = 0
            self.edge_state["revision"] = HEX_C
        if self.fail_edge_after is not None and self.fail_edge_after == operation:
            raise AccessError("edge-reply-lost")
        return dict(self.edge_state)

    def native(self, operation=None, token=None, *, timeout=60):
        self.calls.append("native:" + (operation or "status"))
        if operation and not (self.state / "transition.json").exists():
            raise AssertionError("native operation preceded durable journal")
        if self.fail_native is not None and self.fail_native == operation:
            raise AccessError("native-test-failure")
        if operation == "acquire":
            self.native_state["phase"] = "held"
            if self.native_active:
                self.native_state["active"] = self.native_active.pop(0)
        elif operation == "release":
            self.native_state["phase"] = "idle"
            self.native_state["active"] = 0
        if self.fail_native_after is not None and self.fail_native_after == operation:
            raise AccessError("native-reply-lost")
        return dict(self.native_state)

    def worker(self, operation="status", **_kwargs):
        self.calls.append("worker")
        return dict(self.config)

    def verify_held_mode(self, token, mode):
        self.calls.append("verify:" + mode)
        if self.fail_verify:
            raise AccessError("runtime-proof-failed")
        if mode != "sandboxed" or len(token) != 64:
            raise AccessError("verification-test-failure")


def model_journal(phase="held"):
    return {"kind": "model", "transaction_id": HEX_A, "token": HEX_B,
            "phase": phase, "edge_revision": HEX_B,
            "configured_mode": "sandboxed", "start_config_sha256": HEX_C}


class ModelTransitionTests(unittest.TestCase):
    def setUp(self):
        def portable_atomic(path, value):
            pathlib.Path(path).write_text(json.dumps(value), encoding="utf-8")
        def portable_private(path, _uid, _maximum=1048576):
            return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        self.atomic = patch("pixel_access_bridge.atomic_json", side_effect=portable_atomic)
        self.private = patch("pixel_access_bridge.private_json", side_effect=portable_private)
        self.atomic.start()
        self.private.start()
        self.addCleanup(self.atomic.stop)
        self.addCleanup(self.private.stop)

    def test_begin_journals_before_both_gates_and_drains(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            bridge.edge_streams = [1, 1, 0]
            bridge.native_active = [1, 1, 0]
            with patch("pixel_access_bridge.time.sleep", return_value=None):
                result = bridge.model_begin()
            pending = bridge.model_journal(result["transaction_id"])
            self.assertEqual(result["status"], "held")
            self.assertEqual(set(result), {"status", "transaction_id"})
            self.assertNotEqual(result["transaction_id"], pending["token"])
            self.assertEqual(pending["phase"], "held")
            self.assertFalse(any(key in pending for key in ("ttl", "expires", "expires_at")))
            self.assertIn("discover-installing", bridge.calls)
            self.assertLess(bridge.calls.index("edge:acquire"), bridge.calls.index("native:acquire"))
            self.assertGreaterEqual(bridge.calls.count("edge:acquire"), 3)
            self.assertGreater(
                bridge.calls.index("verify:sandboxed"),
                max(index for index, call in enumerate(bridge.calls)
                    if call in ("edge:acquire", "native:acquire")),
            )

    def test_begin_reproofs_exact_stale_runtime_under_held_gates(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            bridge.runtime_verified = False
            bridge.effective_mode = "unknown"
            bridge.reason = "runtime-proof-required"
            result = bridge.model_begin()
            pending = bridge.model_journal(result["transaction_id"])
            self.assertEqual(pending["phase"], "held")
            self.assertEqual(bridge.edge_state["phase"], "held")
            self.assertEqual(bridge.native_state["phase"], "held")
            self.assertGreater(
                bridge.calls.index("verify:sandboxed"),
                max(bridge.calls.index("edge:acquire"),
                    bridge.calls.index("native:acquire")),
            )

    def test_begin_rejects_ambiguous_missing_runtime_proof(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            bridge.runtime_verified = False
            bridge.effective_mode = "unknown"
            bridge.reason = "inspection-failed"
            with self.assertRaisesRegex(AccessError, "runtime-proof-required"):
                bridge.model_begin()
            self.assertIsNone(bridge.pending())
            self.assertNotIn("edge:acquire", bridge.calls)
            self.assertNotIn("native:acquire", bridge.calls)

    def test_begin_rejects_every_stale_runtime_shape_mismatch(self):
        mismatches = {
            "available": False,
            "scope": "unknown",
            "configured_mode": "unknown",
            "effective_mode": "sandboxed",
            "runtime_verified": True,
            "busy": True,
            "pending": True,
            "reason": None,
            "revision": "not-a-revision",
        }
        for field, value in mismatches.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                bridge = FakeBridge(root)
                snapshot = bridge.inspect()
                snapshot.update({
                    "effective_mode": "unknown",
                    "runtime_verified": False,
                    "reason": "runtime-proof-required",
                })
                snapshot[field] = value
                with patch.object(bridge, "inspect", return_value=snapshot):
                    with self.assertRaisesRegex(AccessError, "runtime-proof-required"):
                        bridge.model_begin()
                self.assertIsNone(bridge.pending())
                self.assertNotIn("edge:acquire", bridge.calls)
                self.assertNotIn("native:acquire", bridge.calls)

    def test_begin_reproof_failure_keeps_both_gates_held(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            bridge.runtime_verified = False
            bridge.effective_mode = "unknown"
            bridge.reason = "runtime-proof-required"
            bridge.fail_verify = True
            with self.assertRaisesRegex(AccessError, "runtime-proof-failed"):
                bridge.model_begin()
            pending = bridge.pending()
            self.assertEqual(pending["phase"], "error")
            self.assertEqual(pending["error"], "runtime-proof-failed")
            self.assertEqual(bridge.edge_state["phase"], "held")
            self.assertEqual(bridge.native_state["phase"], "held")

    def test_begin_failure_retains_root_journal(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            bridge.fail_edge = "acquire"
            with self.assertRaisesRegex(AccessError, "edge-test-failure"):
                bridge.model_begin()
            pending = bridge.pending()
            self.assertEqual(pending["kind"], "model")
            self.assertEqual(pending["phase"], "error")
            self.assertEqual(pending["error"], "edge-test-failure")

    def test_finish_reacquires_reproofs_and_releases(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            (bridge.state / "transition.json").write_text(json.dumps(model_journal()), encoding="utf-8")
            bridge.edge_state["phase"] = "held"
            bridge.native_state["phase"] = "held"
            result = bridge.model_finish({"transaction_id": HEX_A, "outcome": "applied"})
            self.assertEqual(result, {"status": "released", "outcome": "applied"})
            self.assertFalse((bridge.state / "transition.json").exists())
            self.assertTrue((bridge.state / "model-completed.json").exists())
            self.assertIn("discover-installing", bridge.calls)
            self.assertLess(bridge.calls.index("verify:sandboxed"), bridge.calls.index("native:release"))
            self.assertLess(bridge.calls.index("native:release"), bridge.calls.index("edge:release"))

    def test_finish_replays_exact_root_completion_after_lost_socket_reply(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            (bridge.state / "transition.json").write_text(json.dumps(model_journal()), encoding="utf-8")
            bridge.edge_state["phase"] = "held"
            bridge.native_state["phase"] = "held"
            expected = {"status": "released", "outcome": "applied"}
            self.assertEqual(bridge.model_finish({"transaction_id": HEX_A, "outcome": "applied"}), expected)
            releases = (bridge.calls.count("native:release"), bridge.calls.count("edge:release"))
            self.assertEqual(bridge.model_finish({"transaction_id": HEX_A, "outcome": "applied"}), expected)
            self.assertEqual((bridge.calls.count("native:release"), bridge.calls.count("edge:release")), releases)
            with self.assertRaisesRegex(AccessError, "model-recovery-required"):
                bridge.model_finish({"transaction_id": HEX_A, "outcome": "rolled-back"})

    def test_finish_rejects_wrong_transaction_without_release(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            (bridge.state / "transition.json").write_text(json.dumps(model_journal()), encoding="utf-8")
            with self.assertRaisesRegex(AccessError, "model-transaction-mismatch"):
                bridge.model_finish({"transaction_id": HEX_C, "outcome": "applied"})
            self.assertTrue((bridge.state / "transition.json").exists())
            self.assertNotIn("edge:release", bridge.calls)

    def test_partial_release_failure_remains_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            (bridge.state / "transition.json").write_text(json.dumps(model_journal()), encoding="utf-8")
            bridge.edge_state["phase"] = "held"
            bridge.native_state["phase"] = "held"
            bridge.config["config_sha256"] = HEX_C
            bridge.fail_native = "release"
            with self.assertRaisesRegex(AccessError, "native-test-failure"):
                bridge.model_finish({"transaction_id": HEX_A, "outcome": "rolled-back"})
            pending = bridge.pending()
            self.assertEqual(pending["phase"], "error")
            self.assertEqual(bridge.native_state["phase"], "held")
            self.assertEqual(bridge.edge_state["phase"], "held")
            self.assertTrue((bridge.state / "transition.json").exists())

    def test_cli_loads_the_root_custodied_client_only_at_runtime(self):
        request = lambda *args: (200, {"status": "held", "transaction_id": HEX_A})
        with patch("pixel_model_transition._load_request_access", return_value=request), \
                patch("builtins.print") as output:
            self.assertEqual(main(["begin"]), 0)
        output.assert_called_once_with(HEX_A)

    def test_model_status_is_read_only_and_never_discloses_gate_token(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            self.assertEqual(bridge.model_status(), {"pending": False})
            journal = model_journal("error")
            journal["error"] = "model-transition-failed"
            path = bridge.state / "transition.json"
            path.write_text(json.dumps(journal), encoding="utf-8")
            before = path.read_bytes()
            disclosed = bridge.model_status()
            self.assertEqual(disclosed, {"pending": True, "kind": "model",
                                         "transaction_id": HEX_A, "phase": "error",
                                         "configured_mode": "sandboxed",
                                         "start_config_sha256": HEX_C,
                                         "error": "model-transition-failed"})
            self.assertNotIn("token", disclosed)
            self.assertNotIn("edge_revision", disclosed)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(bridge.calls, [])
            journal["token"] = "not-a-token"
            path.write_text(json.dumps(journal), encoding="utf-8")
            with self.assertRaisesRegex(AccessError, "model-recovery-required"):
                bridge.model_status()

    def test_finish_recovers_native_released_error_before_retry(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            journal = model_journal("error")
            journal["error"] = "edge-test-failure"
            (bridge.state / "transition.json").write_text(json.dumps(journal), encoding="utf-8")
            bridge.edge_state["phase"] = "held"
            bridge.native_state["phase"] = "idle"
            result = bridge.model_finish({"transaction_id": HEX_A, "outcome": "applied"})
            self.assertEqual(result["status"], "released")
            self.assertIn("native:acquire", bridge.calls)
            self.assertLess(bridge.calls.index("native:acquire"), bridge.calls.index("native:release"))

    def test_finish_recovers_interrupted_edge(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            (bridge.state / "transition.json").write_text(json.dumps(model_journal()), encoding="utf-8")
            bridge.edge_state["phase"] = "interrupted"
            bridge.native_state["phase"] = "held"
            result = bridge.model_finish({"transaction_id": HEX_A, "outcome": "applied"})
            self.assertEqual(result["status"], "released")
            self.assertIn("edge:recover", bridge.calls)

    def test_finish_reacquires_interrupted_native_with_native_acquire(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            (bridge.state / "transition.json").write_text(json.dumps(model_journal()), encoding="utf-8")
            bridge.edge_state["phase"] = "held"
            bridge.native_state["phase"] = "interrupted"
            result = bridge.model_finish({"transaction_id": HEX_A, "outcome": "applied"})
            self.assertEqual(result["status"], "released")
            self.assertIn("native:acquire", bridge.calls)

    def test_lost_release_replies_are_reconciled_from_idle_gates(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            (bridge.state / "transition.json").write_text(json.dumps(model_journal()), encoding="utf-8")
            bridge.edge_state["phase"] = "held"
            bridge.native_state["phase"] = "held"
            bridge.fail_native_after = "release"
            bridge.fail_edge_after = "release"
            result = bridge.model_finish({"transaction_id": HEX_A, "outcome": "applied"})
            self.assertEqual(result, {"status": "released", "outcome": "applied"})
            self.assertFalse((bridge.state / "transition.json").exists())

    def test_cleanup_failure_cannot_reclassify_verified_apply(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            (bridge.state / "transition.json").write_text(json.dumps(model_journal()), encoding="utf-8")
            bridge.edge_state["phase"] = "held"
            bridge.native_state["phase"] = "held"
            bridge.remove_model_journal = lambda: (_ for _ in ()).throw(OSError("test cleanup"))
            result = bridge.model_finish({"transaction_id": HEX_A, "outcome": "applied"})
            self.assertEqual(result, {"status": "released", "outcome": "applied"})
            self.assertEqual(bridge.pending()["error"], "journal-cleanup-failed")
            self.assertEqual(bridge.native_state["phase"], "idle")
            self.assertEqual(bridge.edge_state["phase"], "idle")

    def test_rolled_back_finish_requires_original_config(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = FakeBridge(root)
            (bridge.state / "transition.json").write_text(json.dumps(model_journal()), encoding="utf-8")
            bridge.edge_state["phase"] = "held"
            bridge.native_state["phase"] = "held"
            with self.assertRaisesRegex(AccessError, "rollback-config-mismatch"):
                bridge.model_finish({"transaction_id": HEX_A, "outcome": "rolled-back"})
            self.assertEqual(bridge.edge_state["phase"], "held")
            self.assertEqual(bridge.native_state["phase"], "held")
            self.assertTrue((bridge.state / "transition.json").exists())

    def test_control_and_cli_contracts_are_bounded(self):
        self.assertEqual(protocol.control_request({"operation": "model-status"}),
                         {"operation": "model-status"})
        with self.assertRaises(protocol.ProtocolError):
            protocol.control_request({"operation": "model-status", "request": {}})
        self.assertEqual(protocol.control_request({"operation": "model-begin"}),
                         {"operation": "model-begin"})
        value = {"operation": "model-finish", "request": {
            "transaction_id": HEX_A, "outcome": "applied"}}
        self.assertEqual(protocol.control_request(value), value)
        with self.assertRaises(protocol.ProtocolError):
            protocol.control_request({"operation": "model-finish", "request": {
                "transaction_id": HEX_A, "outcome": "release"}})
        calls = []
        self.assertEqual(execute("begin", request=lambda *args: (
            calls.append(args) or (200, {"status": "held", "transaction_id": HEX_A}))), HEX_A)
        self.assertEqual(calls, [("model-begin",)])
        self.assertEqual(execute("finish", HEX_A, "applied", request=lambda *args: (
            calls.append(args) or (200, {"status": "released", "outcome": "applied"}))), "released")
        self.assertNotIn(HEX_B, repr(calls))
        status_body = {"pending": True, "kind": "model", "transaction_id": HEX_A,
                       "phase": "held", "configured_mode": "sandboxed",
                       "start_config_sha256": HEX_C}
        self.assertEqual(execute("status", request=lambda operation: (
            200, status_body if operation == "model-status" else {})), status_body)
        with self.assertRaisesRegex(RuntimeError, "model-transition-status-failed"):
            execute("status", request=lambda _operation: (200, dict(status_body, token=HEX_B)))
        with self.assertRaisesRegex(RuntimeError, "model-transition-status-failed") as failed:
            execute("status", request=lambda _operation: (
                200, dict(status_body, token=HEX_B, error=HEX_B)))
        self.assertNotIn(HEX_B, str(failed.exception))


if __name__ == "__main__":
    unittest.main()
