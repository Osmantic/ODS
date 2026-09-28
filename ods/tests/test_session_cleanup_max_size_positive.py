"""session-cleanup must validate that MAX_SIZE is a positive integer."""
import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "session-cleanup.sh"


class SessionCleanupMaxSizeTests(unittest.TestCase):
    def test_invalid_max_size_rejected(self):
        env = os.environ.copy()
        for bad in ("0", "-100", "invalid"):
            env["MAX_SIZE"] = bad
            res = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 1, f"Failed for MAX_SIZE={bad}")
            self.assertIn("MAX_SIZE must be a positive integer", res.stderr)


if __name__ == "__main__":
    unittest.main()
