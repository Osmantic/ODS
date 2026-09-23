"""healthcheck host-port parser must enforce valid port numbers [1, 65535]."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from healthcheck import _parse_host_port


class HealthcheckPortRangeTests(unittest.TestCase):
    def test_out_of_range_ports_rejected(self):
        for bad in ("localhost:0", "localhost:65536", "localhost:99999", "127.0.0.1:-1"):
            with self.assertRaises(ValueError) as ctx:
                _parse_host_port(bad)
            self.assertIn("port must be in range 1-65535", str(ctx.exception))

    def test_valid_ports_accepted(self):
        host, port = _parse_host_port("localhost:8080")
        self.assertEqual(host, "localhost")
        self.assertEqual(port, 8080)


if __name__ == "__main__":
    unittest.main()
