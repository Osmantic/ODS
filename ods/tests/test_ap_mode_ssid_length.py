"""ap-mode must enforce 802.11 SSID length boundaries (1 to 32 octets)."""
import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ap-mode.sh"


class ApModeSsidLengthTests(unittest.TestCase):
    def test_invalid_ssid_lengths_rejected(self):
        env = os.environ.copy()
        for bad in ("A" * 33, "A" * 50):
            env["ODS_AP_SSID"] = bad
            res = subprocess.run(["bash", str(SCRIPT), "status"], capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 1)
            self.assertIn("ODS_AP_SSID length must be between 1 and 32 octets", res.stderr)

    def test_valid_ssid_accepted(self):
        env = os.environ.copy()
        env["ODS_AP_SSID"] = "ValidSSID-123"
        res = subprocess.run(["bash", str(SCRIPT), "status"], capture_output=True, text=True, env=env)
        self.assertNotIn("ODS_AP_SSID length", res.stderr)


if __name__ == "__main__":
    unittest.main()
