"""detect-hardware clamp_int helper must guarantee lower bound even if max < min."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "detect-hardware.sh"


class HardwareClampIntTests(unittest.TestCase):
    def test_clamp_int_inversion_safeguard(self):
        cmd = f'source {SCRIPT} && clamp_int 5 10 2'
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "10")

    def test_clamp_int_standard_behavior(self):
        cmd = f'source {SCRIPT} && clamp_int 5 1 10 && clamp_int 0 1 10 && clamp_int 15 1 10'
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip().split(), ["5", "1", "10"])


if __name__ == "__main__":
    unittest.main()
