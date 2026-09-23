"""ods-backup must sanitize newlines in description arguments."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "ods-backup.sh"


class BackupDescriptionSanitizeTests(unittest.TestCase):
    def test_description_with_newlines_accepted(self):
        res = subprocess.run(["bash", str(SCRIPT), "--description", "line1\nline2", "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main()
