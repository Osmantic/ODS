"""ap-mode must validate that ODS_AP_CHANNEL is a valid WiFi channel between 1 and 165."""
import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ap-mode.sh"


class ApModeChannelTests(unittest.TestCase):
    def test_out_of_bound_channels_rejected(self):
        env = os.environ.copy()
        for bad in ("0", "166", "invalid", "-1"):
            env["ODS_AP_CHANNEL"] = bad
            res = subprocess.run(["bash", str(SCRIPT), "status"], capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 1)
            self.assertIn("ODS_AP_CHANNEL must be between 1 and 165", res.stderr)


if __name__ == "__main__":
    unittest.main()
