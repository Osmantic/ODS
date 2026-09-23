"""healthcheck Result.to_json must serialize strictly typed JSON dictionaries."""
import json
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from healthcheck import Result


class HealthcheckJsonTests(unittest.TestCase):
    def test_json_payload_types(self):
        r = Result(ok=True, target="http://localhost:8080", kind="http", detail="OK", status=200, elapsed_ms=15)
        parsed = json.loads(r.to_json())
        self.assertIs(parsed["ok"], True)
        self.assertEqual(parsed["target"], "http://localhost:8080")
        self.assertEqual(parsed["kind"], "http")
        self.assertEqual(parsed["status"], 200)


if __name__ == "__main__":
    unittest.main()
