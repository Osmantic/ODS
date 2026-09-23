"""ods-backup must reject non-positive or malformed RETENTION_COUNT values."""
import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "ods-backup.sh"


class BackupRetentionTests(unittest.TestCase):
    def test_invalid_retention_counts_rejected(self):
        env = os.environ.copy()
        for bad in ("0", "-5", "abc"):
            env["RETENTION_COUNT"] = bad
            res = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 1, f"Failed to reject RETENTION_COUNT={bad}")
            self.assertIn("RETENTION_COUNT must be a positive integer", res.stderr)

    def test_valid_retention_count_accepted(self):
        env = os.environ.copy()
        env["RETENTION_COUNT"] = "10"
        res = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main()
