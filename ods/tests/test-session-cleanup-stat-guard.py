#!/usr/bin/env python3
"""Regression test for session-cleanup.sh numeric size validation."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
CLEANUP_SCRIPT = ROOT_DIR / "scripts" / "session-cleanup.sh"


class SessionCleanupNumericGuardTests(unittest.TestCase):
    def test_session_cleanup_handles_unreadable_or_corrupted_stat(self):
        """When stat produces an error, session-cleanup must not crash with integer comparison error."""
        with tempfile.TemporaryDirectory() as tmp:
            sessions_dir = Path(tmp) / "sessions"
            sessions_dir.mkdir()
            sessions_json = sessions_dir / "sessions.json"
            sessions_json.write_text(json.dumps({
                "sess1": {"sessionId": "test-session-1"}
            }))
            session_file = sessions_dir / "test-session-1.jsonl"
            session_file.write_text('{"message": "hello"}\n')

            env = {
                "PATH": "/usr/bin:/bin",
                "SESSIONS_DIR": str(sessions_dir),
                "OPENCLAW_DIR": tmp,
                "MAX_SIZE": "1000",
            }
            result = subprocess.run(["bash", str(CLEANUP_SCRIPT)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("integer expression expected", result.stderr)
            self.assertIn("Session cleanup starting", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
