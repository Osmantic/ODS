#!/usr/bin/env python3
"""Focused contract tests for post-restart Pixel access-mode reproof."""
import copy
import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import unittest
from unittest.mock import patch


HERE = pathlib.Path(__file__).resolve().parent
MODULE = HERE.parent / "bin/pixel_access_reconcile.py"
spec = importlib.util.spec_from_file_location("pixel_access_reconcile", MODULE)
reconcile = importlib.util.module_from_spec(spec)
with patch.object(pathlib.Path, "lstat", autospec=True) as lstat:
    lstat.return_value.st_mode = 0o100644
    lstat.return_value.st_uid = 0
    sys.modules[spec.name] = reconcile
    spec.loader.exec_module(reconcile)


def projection(**changes):
    value = {
        "available": True,
        "scope": "owner-host",
        "configured_mode": "sandboxed",
        "effective_mode": "sandboxed",
        "runtime_verified": True,
        "revision": "a" * 64,
        "busy": False,
        "pending": False,
        "reason": None,
    }
    value.update(changes)
    return value


class ReconcileTests(unittest.TestCase):
    def test_diagnostic_projection_retains_only_bounded_coordinator_error(self):
        diagnostic = reconcile.diagnostic_projection({
            "error": "runtime-proof-failed",
            "private": "discard-me",
        })
        self.assertEqual(diagnostic["coordinator_error"], "runtime-proof-failed")
        self.assertNotIn("private", diagnostic)
        self.assertNotIn("coordinator_error",
                         reconcile.diagnostic_projection({"error": "INVALID"}))

    def test_ready_projection_is_read_only(self):
        calls = []
        def request(operation, body=None):
            calls.append((operation, body))
            return 200, projection()
        value, changed = reconcile.reconcile(request)
        self.assertFalse(changed)
        self.assertEqual(value["effective_mode"], "sandboxed")
        self.assertEqual(calls, [("status", None)])

    def test_exact_unverified_projection_is_reproved_in_same_mode(self):
        calls = []
        before = projection(effective_mode="unknown", runtime_verified=False,
                            reason="runtime-proof-required")
        def request(operation, body=None):
            calls.append((operation, body))
            return (200, before) if operation == "status" else (200, projection())
        value, changed = reconcile.reconcile(request)
        self.assertTrue(changed)
        self.assertTrue(value["runtime_verified"])
        self.assertEqual(calls[1], ("change", {
            "mode": "sandboxed", "revision": "a" * 64, "confirmed": False}))

    def test_existing_full_access_uses_explicit_same_mode_confirmation(self):
        before = projection(configured_mode="full-access", effective_mode="unknown",
                            runtime_verified=False, reason="runtime-proof-required")
        after = projection(configured_mode="full-access", effective_mode="full-access")
        calls = []
        def request(operation, body=None):
            calls.append((operation, body))
            return (200, before) if operation == "status" else (200, after)
        _value, changed = reconcile.reconcile(request)
        self.assertTrue(changed)
        self.assertIs(calls[1][1]["confirmed"], True)

    def test_pending_sandboxed_transition_uses_coordinator_restore(self):
        before = projection(effective_mode="unknown", runtime_verified=False,
                            pending=True, reason="transition-recovery-required")
        calls = []
        def request(operation, body=None):
            calls.append((operation, body))
            return (200, before) if operation == "status" else (200, projection())
        value, changed = reconcile.reconcile(request)
        self.assertTrue(changed)
        self.assertTrue(value["runtime_verified"])
        self.assertEqual(calls[1], ("change", {
            "mode": "sandboxed", "revision": "a" * 64, "confirmed": False}))

    def test_pending_full_access_transition_never_mutates(self):
        before = projection(configured_mode="full-access", effective_mode="unknown",
                            runtime_verified=False, pending=True,
                            reason="transition-recovery-required")
        calls = []
        def request(operation, body=None):
            calls.append((operation, body))
            return 200, before
        with self.assertRaises(RuntimeError):
            reconcile.reconcile(request)
        self.assertEqual(calls, [("status", None)])

    def test_unsafe_or_ambiguous_states_never_mutate(self):
        base = projection(effective_mode="unknown", runtime_verified=False,
                          reason="runtime-proof-required")
        variants = [
            {"available": False}, {"busy": True}, {"pending": True, "scope": "edge"},
            {"configured_mode": "unknown"}, {"reason": "transition-recovery-required"},
            {"revision": "not-a-revision"}, {"scope": "edge"},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                value = copy.deepcopy(base)
                value.update(changes)
                calls = []
                def request(operation, body=None):
                    calls.append((operation, body))
                    return 200, value
                with self.assertRaises(RuntimeError):
                    reconcile.reconcile(request)
                self.assertEqual(calls, [("status", None)])

    def test_change_must_return_a_ready_projection(self):
        before = projection(effective_mode="unknown", runtime_verified=False,
                            reason="runtime-proof-required")
        def request(operation, _body=None):
            return (200, before) if operation == "status" else (200, before)
        with self.assertRaises(RuntimeError):
            reconcile.reconcile(request)

    def test_unsafe_state_retains_only_bounded_projection(self):
        unsafe = projection(effective_mode="unknown", runtime_verified=False,
                            reason="transition-recovery-required",
                            revision="b" * 64, private_value="do-not-log")
        with self.assertRaises(reconcile.ReconcileError) as raised:
            reconcile.reconcile(lambda _operation, _body=None: (200, unsafe))
        diagnostic = raised.exception.diagnostic()
        self.assertEqual(diagnostic["stage"], "unsafe-state")
        self.assertEqual(diagnostic["projection"]["reason"], "transition-recovery-required")
        self.assertNotIn("revision", diagnostic["projection"])
        self.assertNotIn("private_value", diagnostic["projection"])

    def test_main_emits_structured_change_failure_without_private_fields(self):
        before = projection(effective_mode="unknown", runtime_verified=False,
                            reason="runtime-proof-required")
        rejected = projection(effective_mode="unknown", runtime_verified=False,
                              reason="transition-recovery-required",
                              private_value="do-not-log")
        calls = []
        def request(operation, _body=None):
            calls.append(operation)
            return (200, before) if operation == "status" else (409, rejected)
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(reconcile.main(request), 1)
        diagnostic = json.loads(output.getvalue())
        self.assertEqual(diagnostic["error"], "pixel-access-reproof-failed")
        self.assertEqual(diagnostic["stage"], "change-failed")
        self.assertEqual(diagnostic["httpStatus"], 409)
        self.assertEqual(diagnostic["projection"]["reason"], "transition-recovery-required")
        self.assertNotIn("private_value", output.getvalue())
        self.assertEqual(calls, ["status", "change"])

    def test_status_failure_reports_stage_and_http_status(self):
        def request(_operation, _body=None):
            return 503, {"error": "runtime-busy"}
        with self.assertRaises(reconcile.ReconcileError) as raised:
            reconcile.reconcile(request)
        self.assertEqual(raised.exception.stage, "status-unavailable")
        self.assertEqual(raised.exception.status, 503)
        diagnostic = raised.exception.diagnostic()
        self.assertEqual(diagnostic["httpStatus"], 503)
        self.assertEqual(diagnostic["projection"]["coordinator_error"], "runtime-busy")

    def test_diagnostic_projection_drops_oversized_and_nonprintable(self):
        result = reconcile.diagnostic_projection({
            "reason": "x" * 97,
            "configured_mode": "has\nnewline",
            "effective_mode": "y" * 96,
        })
        self.assertNotIn("reason", result)
        self.assertNotIn("configured_mode", result)
        self.assertEqual(result["effective_mode"], "y" * 96)

    def test_diagnostic_projection_accepts_non_dict(self):
        self.assertEqual(reconcile.diagnostic_projection("text"), {})
        self.assertEqual(reconcile.diagnostic_projection(None), {})

    def test_main_reports_client_exception_type_only(self):
        def request(_operation, _body=None):
            raise ConnectionError("token=ods_infer_secret")
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(reconcile.main(request), 1)
        diagnostic = json.loads(output.getvalue())
        self.assertEqual(diagnostic["stage"], "client-exception")
        self.assertEqual(diagnostic["exceptionType"], "ConnectionError")
        self.assertNotIn("ods_infer_secret", output.getvalue())

    def test_main_already_ready_reports_without_change(self):
        def request(_operation, _body=None):
            return 200, projection()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(reconcile.main(request), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result, {"result": "already-ready", "mode": "sandboxed"})


if __name__ == "__main__":
    unittest.main()
