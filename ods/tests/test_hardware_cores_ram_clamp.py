"""detect-hardware must safely clamp unbound cores and ram variables."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "detect-hardware.sh"


class HardwareCoresRamTests(unittest.TestCase):
    def test_unbound_cores_ram_safe(self):
        cmd = f'source {SCRIPT} && clamp_int "${{cores:-1}}" 1 1024'
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "1")


if __name__ == "__main__":
    unittest.main()
