"""A plugin change waits briefly for the root lock, then runs exactly once.

Opening Settings reads provider, settings and connection status, and each read
holds the same root lock for a moment. Fleet tower3 (2026-11-11): the owner's
"Turn on" for the Knowledge wiki arrived ~100 ms after that burst and was refused
at once ("ODS is finishing another Portal change"), with nothing pending.
"""
import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
from pixel_access_bridge import AccessError  # noqa: E402
from pixel_connections import coordinator  # noqa: E402


class Bridge:
    """Records how the coordinator asks for admission; holds no real lock."""

    def __init__(self, *, busy=False):
        self.waits = []
        self.busy = busy

    @contextlib.contextmanager
    def bounded(self, seconds):
        yield

    @contextlib.contextmanager
    def locked(self, *, wait_seconds=0):
        self.waits.append(wait_seconds)
        if self.busy:
            raise AccessError("transition-busy")
        yield

    def discover(self):
        pass

    def pending(self):
        return None


class Source:
    def holds_sign_in(self):
        return False


class PluginAdmissionTests(unittest.TestCase):
    def change(self, bridge, gateway):
        with patch.object(coordinator, "_source_backend", return_value=Source()), \
                patch.object(coordinator, "_guard_native_enrollment"), \
                patch.object(coordinator, "_guard_google_exchange"), \
                patch.object(coordinator, "_guard_fresh_source"), \
                patch.object(coordinator, "_record", return_value=None), \
                patch.object(coordinator, "_change_gateway", side_effect=gateway):
            return coordinator.plugin_change(bridge, {"plugin": "memory-wiki", "enabled": True})

    def test_a_plugin_change_waits_for_admission_and_runs_once(self):
        bridge = Bridge()
        calls = []

        def gateway(*args, **kwargs):
            calls.append(kwargs["plugin_change"])
            return "applied"

        self.assertEqual(self.change(bridge, gateway), {"outcome": "applied"})
        self.assertEqual(bridge.waits, [coordinator.PLUGIN_LOCK_WAIT_SECONDS])
        self.assertGreater(coordinator.PLUGIN_LOCK_WAIT_SECONDS, 0)
        self.assertEqual([(call["plugin"], call["enabled"]) for call in calls], [("memory-wiki", True)])

    def test_a_lock_still_held_after_the_wait_is_refused_without_running_the_change(self):
        bridge = Bridge(busy=True)
        calls = []
        with self.assertRaises(AccessError) as caught:
            self.change(bridge, lambda *args, **kwargs: calls.append(kwargs) or "applied")
        self.assertEqual(str(caught.exception), "transition-busy")
        self.assertEqual(bridge.waits, [coordinator.PLUGIN_LOCK_WAIT_SECONDS])
        self.assertEqual(calls, [])

    def test_the_wait_fits_inside_the_operation_and_its_callers(self):
        # Root bounds the change at 300 s; the relay waits 310 s and the dashboard 330 s.
        self.assertLess(coordinator.PLUGIN_LOCK_WAIT_SECONDS, 300 - 120)


if __name__ == "__main__":
    unittest.main()
