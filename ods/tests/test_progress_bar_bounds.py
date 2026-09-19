"""progress bar must clamp percentage between 0 and 100."""
import subprocess
import unittest
from pathlib import Path

class ProgressBarBoundsTests(unittest.TestCase):
    def test_out_of_bounds_current_is_clamped(self):
        script = Path(__file__).resolve().parents[1] / "lib" / "progress.sh"
        cmd = f"""
        source {script}
        draw_progress_bar 150 100 40 "Test"
        """
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("100%", res.stdout)

if __name__ == "__main__":
    unittest.main()
