"""ods-backup must reject invalid backup types."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "ods-backup.sh"


class BackupTypeValidationTests(unittest.TestCase):
    def test_invalid_type_rejected(self):
        res = subprocess.run(["bash", str(SCRIPT), "-t", "unknown-type"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("Invalid backup type", res.stderr)

    def test_valid_type_accepted(self):
        res = subprocess.run(["bash", str(SCRIPT), "-t", "config", "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main()
