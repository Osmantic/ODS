"""Verify safe_reason validates fallback against REASONS enum."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.public import safe_reason

class SafeReasonFallbackTests(unittest.TestCase):
    def test_unvetted_fallback_reverts_to_default(self):
        for bad in (None, 123, "unvetted-reason", "invalid_reason"):
            res = safe_reason("nonexistent", fallback=bad)
            self.assertEqual(res, "provider-controller-unavailable")

    def test_valid_fallback_honored(self):
        res = safe_reason("nonexistent", fallback="runtime-busy")
        self.assertEqual(res, "runtime-busy")

    def test_known_value_preserved(self):
        res = safe_reason("provider-not-managed", fallback="runtime-busy")
        self.assertEqual(res, "provider-not-managed")

if __name__ == "__main__":
    unittest.main()
