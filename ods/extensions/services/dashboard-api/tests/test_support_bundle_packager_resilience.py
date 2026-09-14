"""Unit test suite for support bundle archive packager resilience."""

import unittest


def generate_support_bundle_manifest(include_logs: bool = True) -> dict[str, object]:
    return {
        "status": "ready",
        "logs_included": bool(include_logs),
        "archive_format": "tar.gz",
    }


class TestSupportBundlePackagerResilience(unittest.TestCase):
    def test_generate_support_bundle_manifest_default(self):
        manifest = generate_support_bundle_manifest()
        self.assertEqual(manifest["status"], "ready")
        self.assertTrue(manifest["logs_included"])

    def test_generate_support_bundle_manifest_no_logs(self):
        manifest = generate_support_bundle_manifest(include_logs=False)
        self.assertFalse(manifest["logs_included"])


if __name__ == "__main__":
    unittest.main()
