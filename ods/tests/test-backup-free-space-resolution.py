#!/usr/bin/env python3
"""Regression test for free_bytes_for_path in ods-backup.sh on non-existent directories."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKUP_SH = ROOT_DIR / "ods-backup.sh"


class BackupFreeSpaceResolutionTests(unittest.TestCase):
    def test_free_bytes_resolves_for_non_existent_destination(self):
        """free_bytes_for_path must resolve available disk space even if the directory does not exist yet."""
        with tempfile.TemporaryDirectory() as tmp:
            non_existent_path = Path(tmp) / ".backups" / "sub" / "dest"
            self.assertFalse(non_existent_path.exists())

            script = f"""
            source "{BACKUP_SH}"
            free_bytes_for_path "{non_existent_path}"
            """
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = result.stdout.strip()
            self.assertTrue(output.isdigit(), f"Expected numeric byte output, got: {output!r}")
            self.assertGreater(int(output), 0, "Expected positive free byte count")


if __name__ == "__main__":
    unittest.main(verbosity=2)
