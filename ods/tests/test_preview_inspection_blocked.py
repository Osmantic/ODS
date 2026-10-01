"""Policy refusals stay distinct from an unavailable inspection runtime."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import uuid
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions/services/pixel-agent/host"))
import preview_inspection_capsule as capsule
import preview_inspection_protocol as protocol
import preview_inspection as broker


def bundle(html, extra=None, steps=None):
    files = {"index.html": html.encode(), **(extra or {})}
    digest = hashlib.sha256()
    for name, raw in sorted(files.items()):
        digest.update(len(name.encode()).to_bytes(4, "big") + name.encode())
        digest.update(len(raw).to_bytes(8, "big") + raw)
    sha = digest.hexdigest()
    return {"schemaVersion": 1, "request": {"schemaVersion": 1, "action": "inspect", "siteId": "site-"+sha[:24], "sha256": sha,
        "viewport": {"width": 375, "height": 812}, "steps": steps or [{"action": "assert-text", "locator": {"selector": "h1"}, "expectedText": "Ready"}]},
        "files": [{"path":name,"base64":base64.b64encode(raw).decode()} for name,raw in sorted(files.items())]}


def execute(payload):
    output = io.BytesIO()
    with patch.object(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(protocol.canonical(payload)))), patch.object(sys, "stdout", SimpleNamespace(buffer=output)):
        capsule.main()
    return json.loads(output.getvalue())


def execute_real(payload):
    image = os.environ.get("ODS_INSPECTION_TEST_IMAGE")
    if not image:
        return execute(payload)
    if not __import__('re').fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("test image must be an exact local image ID")
    config = {"docker":"/usr/bin/docker", "transport":"local", "imageId":image}
    name = "ods-inspection-blocked-test-" + uuid.uuid4().hex
    try:
        result = subprocess.run(broker.capsule_argv(config, name), input=protocol.canonical(payload),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45, check=True)
        if len(result.stdout) > protocol.MAX_RESULT:
            raise ValueError("test output exceeded protocol bound")
        return json.loads(result.stdout)
    finally:
        subprocess.run([*broker.docker_prefix(config), "rm", "-f", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)


class FailureClassification(unittest.TestCase):
    def test_runtime_failure_stays_unavailable_without_exception_text(self):
        with patch.object(capsule, "run_browser", side_effect=RuntimeError("private browser path token")):
            result = execute(bundle("<h1>Ready</h1>"))
        self.assertEqual(result["errorCode"], "unavailable")
        self.assertNotIn("private", json.dumps(result))

    def test_broker_preserves_bound_capsule_policy_failure(self):
        payload = bundle("<h1>Ready</h1>")
        request = payload["request"]
        expected = protocol.failure("request_blocked", request)
        config = {"ownerUid": os.getuid(), "docker":"/usr/bin/docker", "transport":"local",
                  "snapshotRoot":"/unused", "imageId":"sha256:"+"a"*64}
        with patch.object(broker, "snapshot_bundle", return_value=payload), patch.object(broker, "bounded_process", side_effect=[protocol.canonical(expected), b""]):
            self.assertEqual(broker.inspect_request(request, config), expected)

    def test_matching_untyped_exception_is_not_misclassified(self):
        with patch.object(capsule, "run_browser", side_effect=protocol.Invalid("preview navigation or request blocked")):
            self.assertEqual(execute(bundle("<h1>Ready</h1>"))["errorCode"], "unavailable")


@unittest.skipUnless(os.environ.get("ODS_PREVIEW_BROWSER_TESTS") == "1" or os.environ.get("ODS_INSPECTION_TEST_IMAGE"), "requires isolated real browser")
class RealBlockedRequests(unittest.TestCase):
    def assert_blocked(self, payload):
        result = execute_real(payload)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["errorCode"], "request_blocked", result)
        self.assertEqual(result["siteId"], payload["request"]["siteId"])
        self.assertEqual(result["planSha256"], protocol.plan_hash(payload["request"]))
        self.assertEqual(set(result), {"schemaVersion", "kind", "status", "errorCode", "siteId", "sha256", "planSha256", "scope"})
        self.assertNotIn("secret-canary", json.dumps(result))

    def test_non_snapshot_root_asset_is_blocked_not_runtime_unavailable(self):
        self.assert_blocked(bundle('<h1>Ready</h1><script src="/outside/app.js?token=secret-canary"></script>', {"assets/app.js":b"window.loaded=true;"}))

    def test_root_asset_inside_snapshot_runs(self):
        result = execute_real(bundle('<h1>Loading</h1><script src="/assets/app.js"></script>', {"assets/app.js": b"document.querySelector('h1').textContent='Ready';"}))
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["blockedRequests"], [])

    def test_navigation_stays_blocked(self):
        result = execute_real(bundle('<h1>Ready</h1><button id="leave" onclick="location.href=\'/outside?token=secret-canary\'">Leave</button>', steps=[{"action":"click", "locator":{"selector":"#leave"}}, {"action":"assert-text", "locator":{"selector":"h1"}, "expectedText":"Ready"}]))
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("navigation", result["blockedRequests"])
        self.assertNotEqual(result.get("errorCode"), "unavailable")
        self.assertNotIn("secret-canary", json.dumps(result))

    def test_relative_asset_inside_snapshot_still_runs(self):
        result = execute_real(bundle('<h1>Loading</h1><script src="assets/app.js"></script>', {"assets/app.js": b"document.querySelector('h1').textContent='Ready';"}))
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["blockedRequests"], [])


if __name__ == "__main__":
    unittest.main()
