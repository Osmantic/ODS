"""Verify deployment rejects identical sourceRoot and providerDirectory."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.managed_deployment import deployment
from pixel_provider.store import StoreError

class DeploymentCollisionTests(unittest.TestCase):
    def test_identical_source_and_provider_dirs_rejected(self):
        binding = {"schemaVersion": 1, "activationId": "12345678-1234-1234-1234-123456789abc", "revision": 1, "allowCloud": False}
        with self.assertRaises(StoreError) as ctx:
            deployment(binding, "/data/app", "/usr/bin/python3", "/data/app", True)
        self.assertEqual(str(ctx.exception), "invalid-managed-deployment")

    def test_distinct_dirs_accepted(self):
        binding = {"schemaVersion": 1, "activationId": "12345678-1234-1234-1234-123456789abc", "revision": 1, "allowCloud": False}
        res = deployment(binding, "/data/source", "/usr/bin/python3", "/data/providers", True)
        self.assertEqual(res["sourceRoot"], "/data/source")
        self.assertEqual(res["providerDirectory"], "/data/providers")

if __name__ == "__main__":
    unittest.main()
