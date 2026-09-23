"""resolve-compose-stack must reject negative GPU count arguments."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "resolve-compose-stack.sh"


class ComposeGpuCountTests(unittest.TestCase):
    def test_negative_gpu_count_rejected(self):
        res = subprocess.run(["bash", str(SCRIPT), "--gpu-count", "-2"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("GPU count cannot be negative", res.stderr)


if __name__ == "__main__":
    unittest.main()
