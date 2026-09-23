"""healthcheck host-port parser must trim whitespace inside IPv6 brackets."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from healthcheck import _parse_host_port


class HealthcheckIpv6BracketTests(unittest.TestCase):
    def test_ipv6_whitespace_normalized(self):
        host, port = _parse_host_port("[ ::1 ]:8080")
        self.assertEqual(host, "::1")
        self.assertEqual(port, 8080)


if __name__ == "__main__":
    unittest.main()
