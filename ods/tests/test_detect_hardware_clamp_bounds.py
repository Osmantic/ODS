"""detect-hardware clamp_int helper must guard against inverted min/max bounds."""
import subprocess
import unittest
from pathlib import Path

class HardwareClampBoundsTests(unittest.TestCase):
    def test_inverted_bounds_clamped_to_min(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "detect-hardware.sh"
        cmd = f"""
        source {script}
        clamp_int 10 50 20
        """
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "50")

if __name__ == "__main__":
    unittest.main()
