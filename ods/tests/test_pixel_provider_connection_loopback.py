"""Verify connection_url normalizes IPv6 loopback to literal 127.0.0.1 loopback."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.connection import connection_url

class ConnectionUrlLoopbackTests(unittest.TestCase):
    def test_ipv6_loopback_normalized(self):
        url = "http://[::1]:8080/v1"
        res = connection_url(url)
        self.assertEqual(res, "http://127.0.0.1:8080/v1")

    def test_localhost_normalized(self):
        url = "http://localhost:8080/v1"
        res = connection_url(url)
        self.assertEqual(res, "http://127.0.0.1:8080/v1")

if __name__ == "__main__":
    unittest.main()
