#!/usr/bin/env python3
"""Regression test for lib/rootless-ownership.sh inline comments in .env overrides."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
LIB_PATH = ROOT_DIR / "lib" / "rootless-ownership.sh"


class RootlessEnvOverrideCommentsTests(unittest.TestCase):
    def test_inline_comments_and_whitespace_are_stripped(self):
        """ODS_UID and ODS_GID values with inline comments and whitespace must parse as clean integers."""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("ODS_UID=1001 # custom host uid\nODS_GID=1002  # custom host gid\n")

            script = f"""
            source "{LIB_PATH}"
            uid=$(_ods_rootless_env_id_override "{tmp}" ODS_UID)
            gid=$(_ods_rootless_env_id_override "{tmp}" ODS_GID)
            echo "UID=$uid GID=$gid"
            """
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("UID=1001 GID=1002", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
