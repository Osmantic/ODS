"""Self-test for the upstream advisory watch's version-range handling."""

import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "advisories", Path(__file__).with_name("check-upstream-advisories.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Ranges(unittest.TestCase):
    def test_comma_and_space_separated_bounds(self):
        self.assertTrue(MODULE.in_range("2026.6.33", ">= 2026.6.6, < 2026.8.1"))
        self.assertTrue(MODULE.in_range("2026.6.33", ">= 2026.4.10 < 2026.8.1"))
        self.assertFalse(MODULE.in_range("2026.8.1", ">= 2026.6.6, < 2026.8.1"))
        self.assertIsNone(MODULE.in_range("2.6.4", "all versions"))

    def test_pre_release_sorts_before_release(self):
        self.assertTrue(MODULE.in_range("2.0.0-rc1", "< 2.0.0"))
        self.assertFalse(MODULE.in_range("2.0.0", "< 2.0.0"))

    def test_upper_bound_filed_as_patched_version(self):
        # A real n8n advisory lists '>= 0.211.0' as vulnerable and '< 1.122.0' as
        # patched; read together, 2.6.4 is not affected.
        misfiled = {"vulnerable_version_range": ">= 0.211.0", "patched_versions": "< 1.122.0"}
        self.assertFalse(MODULE.repository_range_affects("2.6.4", misfiled))
        self.assertTrue(MODULE.repository_range_affects("1.100.0", misfiled))

    def test_ordinary_patched_versions_are_not_bounds(self):
        normal = {"vulnerable_version_range": "< 2.20.7", "patched_versions": "2.20.7"}
        self.assertTrue(MODULE.repository_range_affects("2.6.4", normal))
        self.assertFalse(MODULE.repository_range_affects("2.20.7", normal))
        self.assertIsNone(MODULE.repository_range_affects("2.6.4", {"vulnerable_version_range": "unknown"}))


if __name__ == "__main__":
    unittest.main()
