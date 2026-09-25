"""session-cleanup must reject empty session keys in sessions.json."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "session-cleanup.sh"


class SessionCleanupActiveIdsTests(unittest.TestCase):
    def test_empty_session_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            s_file = d / "sessions.json"
            s_file.write_text(json.dumps({"": {"id": "sess-1"}}))
            env = os.environ.copy()
            env["SESSIONS_JSON"] = str(s_file)
            env["SESSIONS_DIR"] = str(d)
            res = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 1)
            self.assertIn("invalid sessions index", res.stderr)


if __name__ == "__main__":
    unittest.main()
