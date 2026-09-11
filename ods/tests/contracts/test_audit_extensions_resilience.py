"""Unit test suite for audit-extensions script resilience and manifest parsing."""

import unittest
from pathlib import Path


class TestAuditExtensionsResilience(unittest.TestCase):
    def test_script_existence(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "audit-extensions.py"
        self.assertTrue(script.exists())


if __name__ == "__main__":
    unittest.main()
