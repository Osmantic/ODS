"""ap-mode netmask conversion must strip quotes and whitespace."""
import subprocess
import unittest
from pathlib import Path

class ApModeNetmaskTests(unittest.TestCase):
    def test_quoted_netmask_converts_correctly(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "ap-mode.sh"
        cmd = f"""
        source {script}
        _netmask_to_prefix ' "255.255.255.0" '
        """
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "24")

if __name__ == "__main__":
    unittest.main()
