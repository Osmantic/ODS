#!/usr/bin/env python3
"""Focused contract tests for post-restart Pixel access-mode reproof."""
import copy
import importlib.util
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

    def test_unsafe_or_ambiguous_states_never_mutate(self):
        base = projection(effective_mode="unknown", runtime_verified=False,
                          reason="runtime-proof-required")
        variants = [
            {"available": False}, {"busy": True}, {"pending": True},
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


if __name__ == "__main__":
    unittest.main()
