"""session-cleanup must reject non-positive MAX_SIZE."""
import os
import subprocess
import unittest
from pathlib import Path

class SessionCleanupMaxSizeTests(unittest.TestCase):
    def test_invalid_max_size_fails(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "session-cleanup.sh"
        env = {**os.environ, "MAX_SIZE": "-100"}
        res = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 1)
        self.assertIn("MAX_SIZE must be a positive integer", res.stderr)

if __name__ == "__main__":
    unittest.main()
