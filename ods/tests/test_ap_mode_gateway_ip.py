"""ap-mode must validate that ODS_AP_GATEWAY_IP is a valid IPv4 address."""
import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ap-mode.sh"


class ApModeGatewayIpTests(unittest.TestCase):
    def test_invalid_gateway_ip_rejected(self):
        env = os.environ.copy()
        for bad in ("invalid-ip", "192.168.1", "192.168.1.1.1"):
            env["ODS_AP_GATEWAY_IP"] = bad
            res = subprocess.run(["bash", str(SCRIPT), "status"], capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 1)
            self.assertIn("ODS_AP_GATEWAY_IP must be a valid IPv4 address", res.stderr)


if __name__ == "__main__":
    unittest.main()
