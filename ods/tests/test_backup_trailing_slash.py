"""ods-backup must normalize destination paths with trailing slashes."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "ods-backup.sh"


class BackupTrailingSlashTests(unittest.TestCase):
    def test_trailing_slash_stripped(self):
        res = subprocess.run(["bash", str(SCRIPT), "-o", "/custom/backup/dir/", "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main()
