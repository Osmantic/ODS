"""session-cleanup must respect SESSIONS_JSON and output standard INFO when directory is missing."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "session-cleanup.sh"


class SessionCleanupMissingDirTests(unittest.TestCase):
    def test_missing_sessions_dir_outputs_info(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s_file = Path(tmpdir) / "sessions.json"
            s_file.write_text(json.dumps({}))
            env = os.environ.copy()
            env["SESSIONS_JSON"] = str(s_file)
            env["SESSIONS_DIR"] = "/nonexistent/sessions/path"
            res = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 0)
            self.assertIn("INFO: Sessions directory not found", res.stdout)


if __name__ == "__main__":
    unittest.main()
