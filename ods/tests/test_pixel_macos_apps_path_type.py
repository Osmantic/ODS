"""launch_application must reject non-string paths in approved_apps mapping."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_macos_apps import launch_application, AppLaunchError


class AppLaunchPathTypeTests(unittest.TestCase):
    def test_non_string_approved_path_rejected(self):
        approved = {"com.example.app": 12345}
        with self.assertRaises(AppLaunchError) as ctx:
            launch_application({"bundleId": "com.example.app"}, approved_apps=approved, access_status=lambda: {})
        self.assertEqual(str(ctx.exception), "approved-app-path-invalid")


if __name__ == "__main__":
    unittest.main()
