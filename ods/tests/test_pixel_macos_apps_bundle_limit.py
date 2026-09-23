"""launch_application must enforce the 155-character CFBundleIdentifier maximum."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_macos_apps import launch_application, AppLaunchError


class AppLaunchBundleIdTests(unittest.TestCase):
    def test_oversized_bundle_id_rejected(self):
        huge_bundle = ("a" * 150) + "." + ("b" * 10)
        with self.assertRaises(AppLaunchError) as ctx:
            launch_application({"bundleId": huge_bundle}, approved_apps={}, access_status=lambda: {})
        self.assertEqual(str(ctx.exception), "invalid-app-request")


if __name__ == "__main__":
    unittest.main()
