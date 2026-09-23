"""ap-mode must reject empty or whitespace-only ODS_AP_INTERFACE values."""
import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ap-mode.sh"


class ApModeInterfaceTests(unittest.TestCase):
    def test_whitespace_interface_rejected(self):
        env = os.environ.copy()
        env["ODS_AP_INTERFACE"] = "   "
        res = subprocess.run(["bash", str(SCRIPT), "status"], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 1)
        self.assertIn("ODS_AP_INTERFACE cannot be empty", res.stderr)


if __name__ == "__main__":
    unittest.main()
