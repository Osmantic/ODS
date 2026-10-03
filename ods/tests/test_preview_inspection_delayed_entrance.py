"""Focused regression: delayed-entrance visibility must not be reported as a
premature visibility_mismatch, and permanently hidden or perpetually changing
targets must never pass. Opt-in real browser fixture uses the same skip
condition as tests/test_preview_inspection.py."""

import base64
import hashlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "extensions/services/pixel-agent/host")
)
import preview_inspection_protocol as protocol
import preview_inspection_capsule as capsule


def step(action, selector):
    return {"action": action, "locator": {"selector": selector}}


def bundle(html, steps, viewport=None):
    files = [("index.html", html.encode())]
    digest = hashlib.sha256()
    for name, data in files:
        digest.update(len(name.encode()).to_bytes(4, "big") + name.encode())
        digest.update(len(data).to_bytes(8, "big") + data)
    digest = digest.hexdigest()
    return {
        "schemaVersion": 1,
        "request": {
            "schemaVersion": 1,
            "action": "inspect",
            "siteId": "site-" + digest[:24],
            "sha256": digest,
            "viewport": viewport or {"width": 375, "height": 812},
            "steps": steps,
        },
        "files": [{"path": name, "base64": base64.b64encode(data).decode()}
                  for name, data in files],
    }


class ObserveUntilStableExpectedTests(unittest.TestCase):
    """observe_until_stable(expected=...) semantics, no browser."""

    def sample(self, values, expected=None, evaluation_cost=0):
        clock = [0.0]
        waits = []
        samples = iter(values)

        def once():
            clock[0] += evaluation_cost
            return next(samples)

        def wait(milliseconds):
            waits.append(milliseconds)
            clock[0] += milliseconds / 1000

        with patch.object(capsule.time, "monotonic", side_effect=lambda: clock[0]):
            result = capsule.observe_until_stable(once, wait, expected=expected)
        return result, waits, clock[0]

    def test_default_expected_none_is_unchanged(self):
        # No expected: first identical pair wins, exactly as before.
        result, waits, elapsed = self.sample([{"count": 1, "visible": False}] * 2)
        self.assertEqual(result, ({"count": 1, "visible": False}, True))
        self.assertEqual(waits, [100])
        self.assertEqual(elapsed, .1)

    def test_expected_true_does_not_exit_on_stable_false(self):
        # The proven failure: hidden stays hidden for 100ms, then flips.
        values = [{"count": 1, "visible": False}] * 8 + [{"count": 1, "visible": True}] * 2
        result, waits, elapsed = self.sample(values, expected=True)
        self.assertEqual(result, ({"count": 1, "visible": True}, True))
        self.assertGreater(elapsed, .8)
        self.assertLessEqual(elapsed, 1.5)

    def test_expected_true_never_arrives_reports_stable_false(self):
        # Permanently hidden: the unchanged opposite is stable, caller sees it.
        result, waits, elapsed = self.sample([{"count": 1, "visible": False}] * 30, expected=True)
        self.assertEqual(result, ({"count": 1, "visible": False}, True))
        self.assertLessEqual(elapsed, 1.5)

    def test_expected_true_with_perpetual_change_never_passes(self):
        values = [{"count": 1, "visible": False, "opacity": str(v)} for v in range(30)]
        result, waits, elapsed = self.sample(values, expected=True)
        self.assertFalse(result[1])
        self.assertLessEqual(elapsed, 1.5)

    def test_expected_true_late_match_after_deadline_does_not_pass(self):
        # A single late matching sample must not be accepted after the deadline.
        result, _, elapsed = self.sample([{"count": 1, "visible": False}, {"count": 1, "visible": True}], expected=True,
                                         evaluation_cost=.8)
        self.assertGreater(elapsed, 1.5)
        self.assertFalse(result[1])

    def test_expected_false_mirrors_expected_true(self):
        values = [{"count": 1, "visible": True}] * 8 + [{"count": 1, "visible": False}] * 2
        result, _, elapsed = self.sample(values, expected=False)
        self.assertEqual(result, ({"count": 1, "visible": False}, True))
        self.assertGreater(elapsed, .8)
        self.assertLessEqual(elapsed, 1.5)

    def test_nonunique_is_reported_without_waiting_for_visibility(self):
        for count in (0, 2):
            result, waits, elapsed = self.sample([{"count": count}] * 2, expected=True)
            self.assertEqual(result, ({"count": count}, True))
            self.assertEqual(waits, [100])
            self.assertEqual(elapsed, .1)


DELAYED_ENTRANCE_HTML = (
    "<!doctype html><title>Delayed</title>"
    "<style>"
    ".hero__actions{opacity:0;animation:fadein .3s linear .65s forwards}"
    "@keyframes fadein{to{opacity:1}}"
    "#windBtn{display:flex;opacity:1}"
    "</style>"
    "<div class=hero__actions><button id=windBtn>Wind</button></div>"
)


@unittest.skipUnless(
    os.environ.get("ODS_PREVIEW_BROWSER_TESTS") == "1", "real Chromium opt in"
)
class DelayedEntranceBrowserTests(unittest.TestCase):
    """Real run_browser: a delayed ancestor entrance must not fail
    assert-visible before the animation completes, and a permanently hidden
    target must still fail with visibility_mismatch."""

    def check(self, html, steps):
        import subprocess
        import signal

        script = str(Path(capsule.__file__).resolve())
        child = subprocess.Popen(
            [sys.executable, script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            output, error = child.communicate(
                protocol.canonical(bundle(html, steps)), timeout=20
            )
            self.assertEqual(child.returncode, 0, error.decode(errors="replace"))
            return protocol.strict_json(output)
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)

    def test_delayed_ancestor_entrance_passes_assert_visible(self):
        result = self.check(DELAYED_ENTRANCE_HTML, [step("assert-visible", "#windBtn")])
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["steps"][0]["before"]["count"], 1)
        self.assertTrue(result["steps"][0]["before"]["visible"])

    def test_permanently_hidden_target_still_fails(self):
        html = ("<!doctype html><style>.hero__actions{opacity:0}"
                "#windBtn{display:flex;opacity:1}</style>"
                "<div class=hero__actions><button id=windBtn>Wind</button></div>")
        result = self.check(html, [step("assert-visible", "#windBtn")])
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["steps"][0]["errorCode"], "visibility_mismatch")
        self.assertFalse(result["steps"][0]["before"]["visible"])

    def test_assert_hidden_on_delayed_ancestor_still_passes(self):
        result = self.check(DELAYED_ENTRANCE_HTML, [step("assert-hidden", "#windBtn")])
        self.assertEqual(result["status"], "passed", result)
        self.assertFalse(result["steps"][0]["before"]["visible"])

    def test_entrance_after_observation_deadline_does_not_pass(self):
        html = DELAYED_ENTRANCE_HTML.replace(".65s forwards", "3s forwards")
        result = self.check(html, [step("assert-visible", "#windBtn")])
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["steps"][0]["errorCode"], "visibility_mismatch")


if __name__ == "__main__":
    unittest.main()
