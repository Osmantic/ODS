"""healthcheck target parser must reject unsupported URL schemes."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from healthcheck import _parse_target


class HealthcheckSchemeTests(unittest.TestCase):
    def test_unsupported_schemes_rejected(self):
        for bad in ("ftp://localhost:21", "ssh://localhost:22", "ws://localhost:8080"):
            with self.assertRaises(ValueError) as ctx:
                _parse_target(bad)
            self.assertIn("unsupported scheme", str(ctx.exception))

    def test_supported_targets_accepted(self):
        self.assertEqual(_parse_target("http://localhost:8080")[0], "http")
        self.assertEqual(_parse_target("https://localhost:8443")[0], "http")
        self.assertEqual(_parse_target("tcp://localhost:5432")[0], "tcp")


if __name__ == "__main__":
    unittest.main()
