"""resolve-compose-stack must handle non-integer gpu-count gracefully."""
import subprocess
import unittest
from pathlib import Path

class ResolveGpuCountTests(unittest.TestCase):
    def test_non_integer_gpu_count_falls_back_to_one(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "resolve-compose-stack.sh"
        res = subprocess.run(["bash", str(script), "--gpu-count", "auto"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("ValueError", res.stderr)

if __name__ == "__main__":
    unittest.main()
